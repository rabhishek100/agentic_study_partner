"""Trace readback is authoritative for timing/tokens/cost; unknown stays unknown."""
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
from threading import Event, Thread

import psutil

from evals.budget import atomic_json


def trace_metrics(root, runs):
    candidates = {str(run.id): run for run in runs if run.run_type in {"llm", "embedding"}}
    # A LangChain wrapper can enclose another model span. Count physical leaves,
    # not both the receipt and an ancestor's rolled-up totals.
    ancestors = set()
    all_runs = {str(run.id): run for run in runs}
    for run in candidates.values():
        parent = run.parent_run_id
        while parent is not None and str(parent) in all_runs:
            ancestors.add(str(parent))
            parent = all_runs[str(parent)].parent_run_id
    leaves = [run for key, run in candidates.items() if key not in ancestors]
    tokens, costs = [], []
    for run in leaves:
        metadata = (run.extra or {}).get("metadata", {})
        usage = metadata.get("usage_metadata") or {}
        token_count = usage.get("total_tokens", getattr(run, "total_tokens", None))
        cost = usage.get("total_cost", getattr(run, "total_cost", None))
        tokens.append(token_count)
        costs.append(cost)
    def total(values):
        return float(sum(Decimal(str(value)) for value in values)) if all(value is not None for value in values) else None
    return {"source": "langsmith_readback", "latency_seconds": (root.end_time - root.start_time).total_seconds()
            if root.start_time and root.end_time else None,
            "total_tokens": int(total(tokens)) if total(tokens) is not None else None,
            "cost_usd": total(costs), "physical_model_spans": len(leaves),
            "unknown_token_spans": sum(value is None for value in tokens),
            "unknown_cost_spans": sum(value is None for value in costs),
            "first_content_seconds": None, "note": "Non-streaming generation; first content latency is unmeasured."}


def sdk_span_metrics(run):
    """Persist the SDK's own root interval even when hosted export is rejected.

    This is a local LangSmith RunTree observation, not hosted readback, a model
    billing estimate, first-token timing or proof of a successfully uploaded tree.
    """
    if run is None:
        return None
    start, end = run.start_time, run.end_time
    return {"source": "local_langsmith_sdk_run_tree", "trace_id": str(run.id),
            "start_time": start.isoformat() if start else None,
            "end_time": end.isoformat() if end else None,
            "latency_seconds": (end - start).total_seconds() if start and end else None,
            "hosted_delivery_verified": False}


def read_trace(client, trace_id):
    root = client.read_run(trace_id)
    runs = list(client.list_runs(trace_id=trace_id))
    return trace_metrics(root, runs)


@contextmanager
def process_resources():
    """Isolated eval Python process only; remote models and child browsers excluded."""
    process = psutil.Process()
    before = process.cpu_times()
    peak = [process.memory_info().rss]
    stopped = Event()
    def sample():
        while not stopped.wait(0.1):
            try:
                peak[0] = max(peak[0], process.memory_info().rss)
            except psutil.Error:
                return
    thread = Thread(target=sample, daemon=True)
    thread.start()
    result = {}
    try:
        yield result
    finally:
        stopped.set()
        thread.join(timeout=1)
        after = process.cpu_times()
        result.update(cpu_seconds=max(0, after.user + after.system - before.user - before.system),
                      peak_rss_bytes=max(peak[0], process.memory_info().rss), sample_interval_seconds=0.1,
                      scope="eval Python process; includes background telemetry, excludes child processes and remote model compute")


def traced_adapters(adapters, *, budget, project, experiment_id, client):
    from langsmith import tracing_context
    from observability import span, flush_traces
    result = {}
    for name, adapter in adapters.items():
        def execute(case, parents, directory, native=adapter):
            metadata = {"case_id": case.id, "experiment_id": experiment_id, "flow": case.flow,
                        "execution_layer": "native_generation"}
            run = None
            value = None
            with process_resources() as resources:
                try:
                    with tracing_context(project_name=project, client=client, enabled=True, metadata=metadata,
                                         tags=["evaluation", case.flow, experiment_id]):
                        with span("evaluation.case.generate", metadata=metadata,
                                  inputs={"case_id": case.id, "question": case.title}) as run:
                            value = native(case, parents, directory)
                finally:
                    captures = [call["capture"] for call in budget.data["calls"] if call.get("case_id") == case.id
                                and call["phase"] == "generation" and call.get("capture")]
                    trace = {"trace_id": str(run.id) if run else None, "trace_url": None, "request_capture": captures,
                             "local_sdk_span": sdk_span_metrics(run)}
                    if run:
                        try:
                            trace["trace_url"] = run.get_url()
                        except Exception:
                            pass
                    atomic_json(Path(directory) / "artifacts" / case.id / "trace.json", trace)
            flush_traces()
            metrics = {"source": "langsmith_readback", "status": "unavailable", "resources": resources,
                       "local_sdk_span": trace.get("local_sdk_span")}
            if run:
                try:
                    metrics.update(read_trace(client, str(run.id)), status="available")
                except Exception as error:
                    metrics["readback_error_kind"] = type(error).__name__
            from hashlib import sha256
            value.setdefault("evidence", {})["generation_capture_hashes"] = {
                relative: sha256((Path(directory) / relative).read_bytes()).hexdigest() for relative in captures}
            value.update(trace, metrics=metrics)
            return value
        result[name] = execute
    return result


def refresh_metrics(bundle, directory, client):
    """A read-only repair for delayed hosted delivery; never regenerate outputs."""
    for row in bundle["cases"]:
        if row.get("trace_id"):
            try:
                resources = row.get("metrics", {}).get("resources", {})
                local_sdk = row.get("metrics", {}).get("local_sdk_span")
                row["metrics"] = {**read_trace(client, row["trace_id"]), "status": "available", "resources": resources, "local_sdk_span": local_sdk}
            except Exception as error:
                row.setdefault("metrics", {})["readback_error_kind"] = type(error).__name__
    atomic_json(Path(directory) / "bundle.json", bundle)


def write_report(bundle, directory):
    from evals.reviews import valid_reviews, review_stats
    labels = valid_reviews(directory, bundle)
    lines = ["# Five-flow evaluation", "", f"Execution layer: {bundle['config']['execution_layer']}", "",
             "Native generation does not establish API/browser, voice or ingestion journey coverage.", "",
             "| Case | Status | Deterministic failures | Judge grounding | Seconds | Trace USD |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in bundle["cases"]:
        failed = ", ".join(key for key, value in row.get("checks", {}).items()
                           if value is False and key != "standalone_exact") or "—"
        metrics = row.get("metrics", {})
        def measured(key):
            value = metrics.get(key)
            return f"{value:.4f}" if value is not None else "unknown"
        lines.append(f"| {row['id']} | {row['status']} | {failed} | {row.get('judgment', {}).get('grounding_status', 'unjudged')} | {measured('latency_seconds')} | {measured('cost_usd')} |")
    lines += ["", "Budget ledger (includes judging and unknown reservations):", "",
              "```json", json.dumps(bundle.get("budget", {}), indent=2), "```", "",
              "Review mode: LLM-as-judge. Manual review is optional; model judgments are not human calibration.", "",
              "Optional human review:", "", "```json", json.dumps(review_stats(bundle, labels), indent=2), "```", "",
              "| Case | Reviewer | Grounding | Correctness | Coverage | Usefulness | Verdict |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    def safe(value):
        return str(value if value is not None else "unknown").replace("|", "\\|").replace("\n", " ")
    for label in labels:
        lines.append("| " + " | ".join(safe(label.get(key)) for key in
            ("case_id", "reviewer", "grounding", "correctness", "coverage", "usefulness", "verdict")) + " |")
    rewrite_mismatches = sum(row.get("checks", {}).get("standalone_exact") is False for row in bundle["cases"])
    lines += ["", f"Strict rewrite-text mismatches: {rewrite_mismatches}. `standalone_exact` is a diagnostic, "
              "not a semantic correctness gate; missing rewrite gold remains unknown.", "",
              "Citation locator validity alone does not prove supported claims."]
    path = Path(directory) / "report.md"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
