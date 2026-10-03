"""Human review survives reload and never relabels changed artifacts or leaks traces."""
import asyncio
from hashlib import sha256
import json

import httpx
import pytest

from evals.budget import atomic_json
from evals.review_server import create_app
from evals.reviews import HumanReview, load_bundle, valid_reviews, save_review, review_stats
from evals.suite import Case, Manifest, run_suite, fingerprint


def make_bundle(tmp_path):
    manifest = Manifest(version="fixture", cases=[Case(id="sheet", flow="revision_sheet", adapter="test",
        title="Fixture <script>alert(1)</script>", tier="fixture", inputs={}, source={},
        expected={"coverage_points": ["An essential concept"]})], requirements={})
    pdf = tmp_path / "artifacts" / "sheet.pdf"
    pdf.parent.mkdir(); pdf.write_bytes(b"%PDF-test")
    capture = {"model": "fixture", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Complete source snapshot"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
        {"type": "image_url", "image_url": {"url": "https://remote.example/private.png"}},
        {"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64,ZXhlY3V0ZQ=="}}]}]}
    atomic_json(tmp_path / "requests" / "test.json", capture)
    output = {"output": {"title": "Sheet"}, "artifacts": {"pdf": {"path": "artifacts/sheet.pdf", "sha256": sha256(pdf.read_bytes()).hexdigest()}},
        "evidence": {"text": "Canonical", "generation_capture_hashes": {"requests/test.json": sha256((tmp_path / "requests/test.json").read_bytes()).hexdigest()}},
        "request_capture": ["requests/test.json"], "checks": {"valid": False}, "metrics": {"cost_usd": 0.1}, "trace_url": "https://smith.langchain.com/trace"}
    return run_suite(manifest,tmp_path, adapters={"test": lambda *args: output}, config={"execution_layer": "fixture"},
                     judge=lambda *args: {"correctness": 4, "grounding_status": "supported"})


def review(bundle, **values):
    return HumanReview(case_id="sheet", output_hash=bundle["cases"][0]["output_hash"], reviewer="Abhi",
        grounding="unsupported", correctness=2, coverage=None, usefulness=2, layout="needs_fix", verdict="needs_work",
        criteria={"0": "partial"}, **values)


def test_reviews_bind_hash_and_experiment_and_keep_edit_history(tmp_path):
    bundle = make_bundle(tmp_path)
    label = review(bundle)
    save_review(tmp_path, bundle, label)
    label.notes = "Missing equation"
    save_review(tmp_path, bundle, label)
    labels = valid_reviews(tmp_path, load_bundle(tmp_path))
    assert len(labels) == 1 and labels[0]["notes"] == "Missing equation"
    data = json.loads((tmp_path / "reviews.json").read_text())
    assert len(data["history"]) == 1 and (tmp_path / "reviews.json").stat().st_mode & 0o777 == 0o600
    stats = review_stats(bundle, labels)
    assert stats["reviewed_cases"] == 1 and stats["pending_cases"] == []
    assert stats["judge_compared_dimensions"] == 1 and stats["mean_absolute_judge_difference"] == 2
    label.output_hash = "0" * 64
    with pytest.raises(ValueError, match="current generated"):
        save_review(tmp_path, bundle, label)
    data["experiment_fingerprint"] = "other"
    atomic_json(tmp_path / "reviews.json", data)
    with pytest.raises(ValueError, match="different experiment"):
        valid_reviews(tmp_path, bundle)


@pytest.mark.parametrize("field", ["expected", "output", "config"])
def test_mutated_bundle_cannot_receive_old_review_identity(tmp_path, field):
    bundle = make_bundle(tmp_path)
    if field == "config":
        bundle["config"]["model"] = "new"
    else:
        bundle["cases"][0][field] = {"changed": True}
    atomic_json(tmp_path / "bundle.json", bundle)
    with pytest.raises(ValueError, match="changed"):
        load_bundle(tmp_path)


def test_review_server_authorization_blind_views_artifact_integrity_and_export(tmp_path):
    bundle = make_bundle(tmp_path)
    async def run():
        app = create_app(tmp_path, token="session-secret", port=8766)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766") as client:
            root = await client.get("/")
            assert root.status_code == 200 and "session-secret" not in root.text
            assert "innerHTML" not in root.text and "textContent" in root.text
            assert "no-store" == root.headers["cache-control"]
            assert (await client.get("/api/bundle")).status_code == 401
            assert (await client.get("/api/bundle", headers={"Host": "attacker.example", "Authorization": "Bearer session-secret"})).status_code == 403
            client.headers["Authorization"] = "Bearer session-secret"
            private = (await client.get("/api/cases/sheet")).json()
            assert "judgment" not in private and "checks" not in private and "metrics" not in private and "trace_url" not in private
            assert private["source_images"] == ["data:image/png;base64,aW1hZ2U="]
            assert private["source_contexts"] == ["Complete source snapshot"] and private["capture_integrity"] == "verified"
            revealed = (await client.get("/api/cases/sheet?reveal=true")).json()
            assert revealed["judgment"]["correctness"] == 4 and revealed["checks"]["valid"] is False
            body = review(bundle).model_dump(mode="json")
            assert (await client.post("/api/reviews", json=body)).status_code == 403
            assert (await client.post("/api/reviews", json=body, headers={"Origin": "https://evil.example"})).status_code == 403
            assert (await client.post("/api/reviews", json=body, headers={"Origin": "http://127.0.0.1:8766"})).status_code == 200
            exported = (await client.get("/api/export")).json()
            assert exported["stats"]["reviewed_cases"] == 1 and exported["reviews"][0]["grounding"] == "unsupported"
            assert "Abhi" in (tmp_path / "report.md").read_text()
            assert (await client.get("/api/cases/sheet/artifacts/unknown")).status_code == 404
            assert (await client.get("/api/cases/sheet/artifacts/pdf")).headers["content-type"] == "application/pdf"
            (tmp_path / "artifacts" / "sheet.pdf").write_bytes(b"changed")
            assert (await client.get("/api/cases/sheet/artifacts/pdf")).status_code == 409
            (tmp_path / "requests" / "test.json").write_text("{}")
            assert (await client.get("/api/cases/sheet")).status_code == 409
    asyncio.run(run())


def test_unknown_criterion_is_rejected(tmp_path):
    bundle = make_bundle(tmp_path)
    label = review(bundle)
    label.criteria = {"99": "met"}
    with pytest.raises(ValueError, match="Unknown review criterion"):
        save_review(tmp_path, bundle, label)
