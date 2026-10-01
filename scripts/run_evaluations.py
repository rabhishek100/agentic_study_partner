"""Plan, fixture smoke, or budgeted native five-flow evaluation. No API writes."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--fixture", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--manifest", type=Path, default=Path("evaluation/five_flow_manifest.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--flow", choices=["chat", "summary", "video_course", "revision_sheet", "interview"])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-usd", default="2")
    parser.add_argument("--judge-model", default="openai/gpt-6-luna")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--retrieval-mode", choices=["bm25", "hybrid", "hybrid_rerank"], default="bm25")
    parser.add_argument("--project")
    args = parser.parse_args()
    from evals.suite import load_manifest, coverage, run_suite
    manifest = load_manifest(args.manifest)
    if args.plan:
        print(json.dumps({"cases": len(manifest.cases), "coverage": coverage(manifest)}, indent=2))
        return
    if args.output is None:
        parser.error("--output is required; existing outputs automatically resume")
    output = args.output.resolve()
    # All private source/prompt captures stay under the repository's ignored runs directory.
    runs = Path(__file__).resolve().parents[1] / "evaluation" / "runs"
    if not output.is_relative_to(runs.resolve()):
        parser.error("--output must be inside evaluation/runs/")
    selected = args.cases or ([case.id for case in manifest.cases if case.flow == args.flow] if args.flow else None)
    config = {"execution_layer": "fixture" if args.fixture else "live", "retrieval_mode": args.retrieval_mode,
              "judge_model": None if args.no_judge else args.judge_model,
              "bounded_output_tokens": 16000, "provider_streaming": False}
    if args.fixture:
        def fixture(case, parents, directory):
            return {"output": {"answer": f"Fixture plumbing only: {case.id}"}, "evidence": [],
                    "checks": {"live_quality": None}, "state": {"fixture": True}}
        bundle = run_suite(manifest, output, adapters={case.adapter: fixture for case in manifest.cases},
                           config=config, selected=selected, retry_failed=args.retry_failed)
    else:
        from evals.adapters import bind_sources, NativeAdapters
        from evals.budget import Budget, pricing_snapshot
        from evals.reporting import traced_adapters, refresh_metrics
        from evals.suite_judge import SuiteJudge
        from langsmith import Client
        from observability import safe_value, flush_traces
        from storage.database import environment_owner_id
        if not os.getenv("LANGSMITH_API_KEY"):
            parser.error("LANGSMITH_API_KEY is required for trace-backed live evaluation")
        config["bindings"] = bind_sources(manifest, owner_id=environment_owner_id())
        # Freeze model and prompt-affecting environment without credential values.
        config["model_environment"] = {key: value for key, value in os.environ.items() if key.startswith("OPENROUTER_")
            and any(part in key for part in ("MODEL", "REASONING", "MAX_TOKENS", "TIMEOUT", "MAX_RETRIES"))
            and not key.endswith(("_KEY", "_TOKEN", "_SECRET"))}
        config["revision_environment"] = {key: value for key,value in os.environ.items() if key in {
            "REVISION_CONTEXT_WINDOW_TOKENS", "REVISION_MAX_PAGES", "REVISION_MAX_REPAIRS"}}
        config["summary_environment"] = {key: value for key,value in os.environ.items() if key in {
            "SUMMARY_CONTEXT_WINDOW_TOKENS", "SUMMARY_MAX_OUTPUT_TOKENS", "SUMMARY_SAFETY_MARGIN_TOKENS"}}
        from evals.suite import fingerprint
        from hashlib import sha256
        root = Path(__file__).resolve().parents[1]
        paths = subprocess.check_output(["git", "ls-files", "*.py"], text=True).splitlines()
        paths.extend(["evals/adapters.py", "evals/reporting.py", "evals/suite_judge.py", "scripts/run_evaluations.py"])
        config["implementation_sha256"] = fingerprint({path: sha256((root / path).read_bytes()).hexdigest()
                                                      for path in sorted(set(paths)) if not path.startswith("tests/")})
        config["project"] = args.project or f"study-partner-evals-{output.name}"
        budget = Budget(output, cap=args.max_usd, prices=None if (output / "budget.json").exists() else pricing_snapshot())
        client = Client(anonymizer=safe_value)
        native = NativeAdapters(config["bindings"], retrieval_mode=args.retrieval_mode)
        adapters = traced_adapters(native.registry(), budget=budget, project=config["project"], experiment_id=output.name, client=client)
        judge = None if args.no_judge else SuiteJudge(model=args.judge_model, project=config["project"],
                                                     client=client, experiment_id=output.name)
        with budget.guard():
            bundle = run_suite(manifest, output, adapters=adapters, config=config, budget=budget, judge=judge,
                selected=selected, retry_failed=args.retry_failed, on_case=lambda row: print(f"Running {row['id']} ({row['flow']})", flush=True))
        flush_traces()
        refresh_metrics(bundle, output, client)
    from evals.reporting import write_report
    write_report(bundle, output)
    print(json.dumps(bundle["summary"], indent=2))
    print(f"Saved {output / 'report.md'}")
    if any(row["status"] in {"failed", "judge_failed", "blocked", "interrupted", "dependency_unavailable"} for row in bundle["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
