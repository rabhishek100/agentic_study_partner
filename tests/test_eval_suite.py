"""The same output and budget must survive interrupted multi-step experiments."""
import json

import pytest

from evals.budget import BudgetStop
from evals.suite import Case, Manifest, fingerprint, load_manifest, run_suite


def manifest():
    return Manifest(version="fixture", cases=[Case(id="a", flow="chat", adapter="test", title="First",
                    tier="fixture", inputs={}, expected={}, source={"fingerprint": "source"}),
                    Case(id="b", flow="summary", adapter="test", title="Second", tier="fixture",
                         inputs={}, expected={}, source={}, depends_on=["a"])], requirements={})


def test_completed_output_is_not_paid_for_again_and_dependencies_resume(tmp_path):
    calls = []
    def adapter(case, parents, directory):
        calls.append(case.id)
        return {"output": {"answer": case.id}, "evidence": [], "artifacts": {}, "state": {"last": case.id}}
    config = {"execution_layer": "fixture"}
    first = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config=config, selected=["a"])
    assert [row["status"] for row in first["cases"]] == ["completed", "queued"]
    resumed = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config=config)
    assert calls == ["a", "b"] and all(r["status"] == "completed" for r in resumed["cases"])
    run_suite(manifest(), tmp_path, adapters={"test": adapter}, config=config)
    assert calls == ["a", "b"]


def test_judge_failure_preserves_generation_and_rejudge_does_not_regenerate(tmp_path):
    count = []
    def adapter(case, parents, directory):
        count.append(case.id)
        return {"output": {"answer": "Saved"}, "evidence": []}
    def fail(*args):
        raise RuntimeError("judge offline")
    bundle = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config={}, judge=fail)
    assert all(row["status"] == "judge_failed" and row["output"] for row in bundle["cases"])
    bundle = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config={}, judge=lambda *args: {"supported": True})
    assert count == ["a", "b"] and all(row["status"] == "completed" for row in bundle["cases"])


def test_budget_stop_during_judging_keeps_generation_and_queues_rest(tmp_path):
    def stop(*args):
        raise BudgetStop("ceiling")
    bundle = run_suite(manifest(), tmp_path, adapters={"test": lambda *args: {"output": "saved"}}, config={}, judge=stop)
    assert bundle["cases"][0]["output"] == "saved"
    assert [r["status"] for r in bundle["cases"]] == ["blocked", "queued"]
    calls = []
    def adapter(case, *args):
        calls.append(case.id)
        return {"output": "saved"}
    resumed = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config={}, judge=lambda *args: {})
    assert calls == ["b"]
    assert resumed["cases"][0]["error"] is None
    assert resumed["cases"][0]["issues"][0]["kind"] == "budget"


def test_interrupted_generation_requires_explicit_retry(tmp_path):
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_suite(manifest(), tmp_path, adapters={"test": interrupt}, config={})
    calls = []
    def adapter(case, *args):
        calls.append(case.id)
        return {"output": "saved"}
    result = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config={})
    assert calls == [] and result["cases"][1]["status"] == "dependency_unavailable"
    result = run_suite(manifest(), tmp_path, adapters={"test": adapter}, config={}, retry_failed=True)
    assert calls == ["a", "b"]


def test_changed_configuration_and_tampered_output_are_rejected(tmp_path):
    run_suite(manifest(), tmp_path, adapters={"test": lambda *args: {"output": "saved"}}, config={}, selected=["a"])
    with pytest.raises(ValueError, match="configuration changed"):
        run_suite(manifest(), tmp_path, adapters={}, config={"model": "different"})
    path = tmp_path / "bundle.json"
    value = json.loads(path.read_text())
    value["cases"][0]["output"] = "edited"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Saved output changed"):
        run_suite(manifest(), tmp_path, adapters={}, config={})


def test_live_without_budget_and_unknown_case_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="requires the shared budget"):
        run_suite(manifest(), tmp_path, adapters={}, config={"execution_layer": "live"})
    with pytest.raises(ValueError, match="Unknown selected case"):
        run_suite(manifest(), tmp_path, adapters={}, config={}, selected=["missing"])


def test_committed_manifest_has_five_flows_valid_dependencies_and_real_test_links():
    from pathlib import Path
    from scripts.build_eval_manifest import build
    m = load_manifest("evaluation/five_flow_manifest.json")
    assert m.model_dump() == build().model_dump()
    assert {case.flow for case in m.cases} == {"chat", "summary", "video_course", "revision_sheet", "interview"}
    assert 40 <= len(m.cases) <= 60
    assert all(Path(path.split("::")[0]).exists() for requirement in m.requirements.values()
               for path in requirement.get("contract_tests", []))


def test_sdk_root_interval_is_recorded_without_claiming_hosted_delivery():
    from datetime import datetime, UTC, timedelta
    from langsmith.run_trees import RunTree
    from evals.reporting import sdk_span_metrics
    start = datetime(2026, 10, 3, tzinfo=UTC)
    run = RunTree(name="fixture generation", run_type="chain", start_time=start)
    assert sdk_span_metrics(run)["latency_seconds"] is None
    run.end(end_time=start + timedelta(seconds=3))
    metrics = sdk_span_metrics(run)
    assert metrics["latency_seconds"] == 3
    assert metrics["source"] == "local_langsmith_sdk_run_tree"
    assert metrics["hosted_delivery_verified"] is False
    assert "cost_usd" not in metrics and "total_tokens" not in metrics
    assert sdk_span_metrics(None) is None
