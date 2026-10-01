"""Evaluate native outputs without mutating saved user sessions or paying providers."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from evals.adapters import NativeAdapters, read_dataset, remap_gold, write_artifact
from evals.budget import atomic_json
from evals.reporting import trace_metrics
from evals.suite import Case, fingerprint, load_manifest
from evals.suite_judge import judge_payload


def book():
    return {"id": 526, "fingerprint": "frozen", "nodes": {
        "34": {"id": 2605, "start_page": 69, "end_page": 69},
        "35": {"id": 2606, "start_page": 70, "end_page": 72}}}


def test_gold_rebinds_locators_and_rejects_wrong_source_pages():
    original = {"expected_scope": {"node_id": 34}, "expected_evidence": [{"node_id": 35, "pages": [70, 72]}],
                "reference_answer": "Sources [N35:P70].", "book_id": 1}
    frozen = deepcopy(original)
    rebound = remap_gold(original, book())
    assert rebound["book_id"] == 526 and rebound["expected_scope"]["node_id"] == 2605
    assert rebound["expected_evidence"][0]["node_id"] == 2606
    assert rebound["reference_answer"] == "Sources [N2606:P70]." and original == frozen
    for invalid in ({"node_id": 35, "pages": [69]}, {"node_id": 36}, "[N35:P73]"):
        with pytest.raises(ValueError, match="Gold"):
            remap_gold(invalid, book())


def test_dataset_access_and_frozen_bytes_are_checked():
    with pytest.raises(ValueError, match="inside evaluation"):
        read_dataset(".env")
    adapters = NativeAdapters({"owner_id": "00000000-0000-4000-8000-000000000001", "datasets": {
        "evaluation/interview_candidate_profiles.json": "stale"}})
    case = next(c for c in load_manifest("evaluation/five_flow_manifest.json").cases if c.adapter == "interview_grade")
    with pytest.raises(ValueError, match="dataset changed"):
        adapters.dataset(case)


def test_book_adapter_replays_saved_parent_state_and_rebound_gold(monkeypatch, tmp_path):
    from study.contracts import TurnResult
    from study.conversation import new_conversation_state
    import evals.multiturn as module
    bindings = {"owner_id": "00000000-0000-4000-8000-000000000001"}
    adapters = NativeAdapters(bindings, retrieval_mode="bm25")
    monkeypatch.setattr(adapters, "book_binding", lambda case: book())
    monkeypatch.setattr(adapters, "dataset", lambda case: {})
    state = new_conversation_state(book_ids=[526], conversation_id="parent")
    output = TurnResult(question="Follow up", answer="Need more evidence", route="clarify", outcome="clarify",
                        history_dependency="dependent")
    calls = []
    def runner(owner, database, mode):
        def execute(question, previous):
            calls.append((question, previous, mode))
            return output, previous
        return execute
    monkeypatch.setattr(module, "ProjectRunner", runner)
    case = Case(id="followup", flow="chat", adapter="book", title="Follow up", tier="fixture", source={},
                inputs={"question": "Follow up"}, expected={"expected_route": "clarify", "history_dependency": "dependent", "answerable": False})
    result = adapters.book(case, [{"state": state.model_dump(mode="json")}], tmp_path)
    assert calls[0][1] == state and calls[0][2] == "bm25"
    assert result["state"]["conversation_id"] == "parent" and result["checks"]["outcome"] is True


def test_native_grading_provider_failure_is_not_completed_output(monkeypatch, tmp_path):
    import evals.interview_candidate_calibration as module
    import interviews.models as models
    case = next(c for c in load_manifest("evaluation/five_flow_manifest.json").cases if c.adapter == "interview_grade")
    data = read_dataset(case.inputs["dataset"])
    adapters = NativeAdapters({"owner_id": "00000000-0000-4000-8000-000000000001",
                              "datasets": {case.inputs["dataset"]: fingerprint(data)}})
    monkeypatch.setattr(models, "structured_model", lambda schema: object())
    monkeypatch.setattr(module, "evaluate_candidate_profile", lambda *args, **kwargs: {"provider_error": "offline"})
    with pytest.raises(RuntimeError, match="offline"):
        adapters.interview_grade(case, [], tmp_path)


def test_native_ideal_generation_saves_partial_dialogue_before_failure(monkeypatch, tmp_path):
    import interviews.ideal_generation as generation
    import interviews.models as models
    case = next(c for c in load_manifest("evaluation/five_flow_manifest.json").cases if c.adapter == "ideal")
    data = read_dataset(case.inputs["dataset"])
    adapters = NativeAdapters({"owner_id": "00000000-0000-4000-8000-000000000001",
                              "datasets": {case.inputs["dataset"]: fingerprint(data)}})
    monkeypatch.setattr(models, "structured_model", lambda *args, **kwargs: object())
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise RuntimeError("second exchange failed")
        return SimpleNamespace(model_dump=lambda **kwargs: {"topic_key": "first"}), 0
    monkeypatch.setattr(generation, "generate_ideal_exchange", generate)
    with pytest.raises(RuntimeError, match="second exchange"):
        adapters.ideal(case, [], tmp_path)
    assert json.loads((tmp_path / "artifacts" / case.id / "partial-exchanges.json").read_text()) == [{"topic_key": "first"}]


def test_artifact_hash_and_private_permissions(tmp_path):
    result = write_artifact(tmp_path, "sheet-1", "sheet.pdf", b"artifact")
    path = tmp_path / result["path"]
    from hashlib import sha256
    assert result["sha256"] == sha256(path.read_bytes()).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o600


@contextmanager
def fake_connection(*args, **kwargs):
    assert kwargs.get("readonly") is True
    yield SimpleNamespace(execute=lambda *args, **kwargs: None)


def test_video_adapter_uses_production_media_and_version_before_generation(monkeypatch, tmp_path):
    import evals.adapters as module
    import api.video_chat as api
    import video.conversation as conversation
    from video.contracts import VideoTurnResult
    case = next(c for c in load_manifest("evaluation/five_flow_manifest.json").cases if c.adapter == "video")
    frozen = {"id": case.source["video_id"], "title": "Frozen", "version": "v1", "fingerprint": "source"}
    adapters = NativeAdapters({"owner_id": "00000000-0000-4000-8000-000000000001", "videos": {frozen["id"]: frozen}})
    monkeypatch.setattr(adapters, "dataset", lambda case: {})
    monkeypatch.setattr(module, "connection", fake_connection)
    monkeypatch.setattr(module, "bind_video", lambda *args: frozen)
    dependencies = SimpleNamespace(media_store=object())
    monkeypatch.setattr(api, "_answer_dependencies", lambda: dependencies)
    calls = []
    def execute(current, question, state, **kwargs):
        calls.append(kwargs)
        return VideoTurnResult(question=question, answer="Insufficient", outcome="abstain",
                               route="evidence_qa", history_dependency="independent"), state
    monkeypatch.setattr(conversation, "execute_video_turn", execute)
    result = adapters.video(case, [], tmp_path)
    assert result["source_fingerprint"] == "source" and calls[0]["dependencies"] is dependencies
    assert calls[0]["video_id"] == frozen["id"] and "token_callback" not in calls[0]
    monkeypatch.setattr(module, "bind_video", lambda *args: {**frozen, "version": "v2"})
    with pytest.raises(ValueError, match="version changed"):
        adapters.video(case, [], tmp_path)
    assert len(calls) == 1
    monkeypatch.setattr(module, "bind_video", lambda *args: frozen)
    dependencies.media_store = None
    with pytest.raises(ValueError, match="cannot fall back"):
        adapters.video(case, [], tmp_path)
    assert len(calls) == 1


def test_course_filter_is_passed_to_native_retrieval_and_drift_is_rejected(monkeypatch, tmp_path):
    import evals.adapters as module
    import api.video_chat as api
    import video.course_repository as repository
    import video.course_conversation as conversation
    from video.course_contracts import CourseTurnResult
    ids = ["00000000-0000-4000-8000-000000000002", "00000000-0000-4000-8000-000000000003"]
    members = [{"id": id_, "title": title, "version": "v1", "fingerprint": title, "lecture_index": index}
               for index,(id_, title) in enumerate(zip(ids,["RPC and Threads", "GFS"]))]
    case = next(c for c in load_manifest("evaluation/five_flow_manifest.json").cases if c.id == "course-exclusion")
    frozen = {"id": "00000000-0000-4000-8000-000000000004", "title": case.source["title"],
              "members": members, "fingerprint": "course"}
    adapters = NativeAdapters({"owner_id": "00000000-0000-4000-8000-000000000001", "courses": {frozen["title"]: frozen}})
    monkeypatch.setattr(module, "connection", fake_connection)
    monkeypatch.setattr(module, "bind_video", lambda current, owner, id_: {k:v for k,v in members[ids.index(id_)].items() if k != "lecture_index"})
    lectures = [{"video_id": id_, "lecture_index": index, "readiness_status": "ready"} for index,id_ in enumerate(ids)]
    monkeypatch.setattr(repository, "list_course_lectures", lambda *args, **kwargs: lectures)
    monkeypatch.setattr(api, "_answer_dependencies", lambda: SimpleNamespace(media_store=object()))
    calls = []
    def execute(current, question, state, **kwargs):
        calls.append(kwargs)
        return CourseTurnResult(question=question, answer="Insufficient", outcome="abstain"), state, {}
    monkeypatch.setattr(conversation, "execute_course_turn", execute)
    adapters.course(case, [], tmp_path)
    assert [str(id_) for id_ in calls[0]["video_ids"]] == [ids[0]]
    lectures.pop()
    with pytest.raises(ValueError, match="membership"):
        adapters.course(case, [], tmp_path)
    assert len(calls) == 1


def test_sheet_adapter_persists_pdf_provenance_and_explicit_review_gaps(monkeypatch, tmp_path):
    import evals.adapters as module
    import revision_sheets.source as source_module
    import importlib
    generation = importlib.import_module("revision_sheets.generate")
    case = next(c for c in load_manifest("evaluation/five_flow_manifest.json").cases if c.id == "sheet-chapter-1")
    bound = {"id": 526, "nodes": {"14": {"id": 2585, "node_type": "chapter", "parent_id": None,
                                           "title": "Chapter 1. Overview"}}}
    adapters = NativeAdapters({"owner_id": "00000000-0000-4000-8000-000000000001"})
    monkeypatch.setattr(adapters, "book_binding", lambda case: bound)
    monkeypatch.setattr(module, "connection", fake_connection)
    source = SimpleNamespace(text="Complete canonical evidence", references={}, units={"p1": {"[N2585:P1]"}}, fingerprint="source")
    requested = []
    def load(current, **kwargs):
        requested.append(kwargs["request"])
        return source
    monkeypatch.setattr(source_module, "load_source", load)
    monkeypatch.setattr(generation, "generate", lambda *args, **kwargs: (
        SimpleNamespace(model_dump=lambda **kwargs: {"title": "Sheet"}), b"%PDF-fixture",
        {"outstanding_findings": ["Missing concept"], "page_count": 1}))
    result = adapters.sheet(case, [], tmp_path)
    assert requested[0].chapter_node_id == 2585 and requested[0].book_id == 526
    assert (tmp_path / result["artifacts"]["pdf"]["path"]).read_bytes() == b"%PDF-fixture"
    assert result["checks"] == {"production_findings_clear": False, "independent_concept_coverage": None, "human_layout_review": None}


def run(id, kind="chain", parent=None, **kwargs):
    return SimpleNamespace(id=id, run_type=kind, parent_run_id=parent, extra={}, total_tokens=None, total_cost=None, **kwargs)


def test_trace_rollup_counts_physical_leaves_and_unknown_receipts():
    now = datetime.now(UTC)
    root = run("root", start_time=now, end_time=now + timedelta(seconds=4))
    wrapper = run("wrapper", "llm", "root")
    leaf = run("physical", "llm", "wrapper")
    leaf.extra = {"metadata": {"usage_metadata": {"total_tokens": 12, "total_cost": "0.001"}}}
    embedding = run("embedding", "embedding", "root")
    embedding.total_tokens, embedding.total_cost = 5, Decimal("0.0002")
    metrics = trace_metrics(root, [root, wrapper, leaf, embedding])
    assert metrics["latency_seconds"] == 4 and metrics["total_tokens"] == 17
    assert metrics["cost_usd"] == pytest.approx(0.0012) and metrics["physical_model_spans"] == 2
    embedding.total_cost = None
    assert trace_metrics(root, [root, wrapper, leaf, embedding])["cost_usd"] is None


def test_judge_gets_full_captured_context_and_original_images(tmp_path):
    case = load_manifest("evaluation/five_flow_manifest.json").cases[0]
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,original"}}
    atomic_json(tmp_path / "requests" / "generation.json", {"model": "fixture", "messages": [
        {"role": "system", "content": "Source " + "long evidence " * 1000},
        {"role": "user", "content": [{"type": "text", "text": "Frame"}, image, image]}]})
    row = {"output": {"answer": "Generated"}, "evidence": {"bound_expected": {"coverage_points": ["Rebound"]}},
           "request_capture": ["requests/generation.json"]}
    payload, images = judge_payload(case, row, tmp_path)
    assert len(payload["exact_generation_contexts"][0]["messages"][0]["content"]) > 10000
    assert images == [image] and payload["expected"]["coverage_points"] == ["Rebound"]
    row["request_capture"] = ["../../.env"]
    with pytest.raises(ValueError, match="inside requests"):
        judge_payload(case, row, tmp_path)
