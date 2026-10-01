"""Human labels bind both experiment identity and the exact saved output."""
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.budget import atomic_json
from evals.suite import fingerprint


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    output_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=80)
    grounding: Literal["supported", "unsupported", "unknown"]
    correctness: int | None = Field(ge=0, le=4)
    coverage: int | None = Field(ge=0, le=4)
    usefulness: int | None = Field(ge=0, le=4)
    layout: Literal["readable", "needs_fix", "unknown", "not_applicable"]
    verdict: Literal["usable", "needs_work", "unsure"]
    notes: str = Field(default="", max_length=6000)
    criteria: dict[str, Literal["met", "partial", "missed", "unknown"]] = Field(default_factory=dict, max_length=100)


def load_bundle(directory):
    bundle = json.loads((Path(directory) / "bundle.json").read_text())
    if bundle["fingerprint"] != fingerprint({"manifest": bundle["manifest"], "config": bundle["config"]}):
        raise ValueError("Experiment manifest or configuration changed")
    cases = {case["id"]: case for case in bundle["manifest"]["cases"]}
    for row in bundle["cases"]:
        original = cases[row["id"]]
        if any(row.get(key) != original.get(key) for key in ("id", "flow", "title", "tier", "source", "expected", "inputs", "aspects")):
            raise ValueError("Review case identity changed")
        if row.get("output") is not None and row["output_hash"] != fingerprint(
                {key: row.get(key) for key in ("output", "evidence", "artifacts")}):
            raise ValueError("Saved artifact changed; review identity is invalid")
    return bundle


def valid_reviews(directory, bundle):
    path = Path(directory) / "reviews.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if data["experiment_fingerprint"] != bundle["fingerprint"]:
        raise ValueError("Human labels belong to a different experiment")
    rows = {row["id"]: row for row in bundle["cases"]}
    return [item for item in data["labels"] if item["case_id"] in rows
            and item["output_hash"] == rows[item["case_id"]].get("output_hash")]


def save_review(directory, bundle, review):
    row = next((row for row in bundle["cases"] if row["id"] == review.case_id), None)
    if row is None or row.get("output") is None or row["output_hash"] != review.output_hash:
        raise ValueError("Review requires the current generated output hash")
    if len(review.criteria) > len(row["expected"].get("coverage_points", [])):
        raise ValueError("Unknown review criterion")
    allowed = {str(index) for index,_ in enumerate(row["expected"].get("coverage_points", []))}
    if set(review.criteria) - allowed:
        raise ValueError("Unknown review criterion")
    path = Path(directory) / "reviews.json"
    data = json.loads(path.read_text()) if path.exists() else {"version": 1, "experiment_fingerprint": bundle["fingerprint"],
                                                             "labels": [], "history": []}
    if data["experiment_fingerprint"] != bundle["fingerprint"]:
        raise ValueError("Human labels belong to a different experiment")
    label = {**review.model_dump(mode="json"), "saved_at": datetime.now(UTC).isoformat()}
    prior = [item for item in data["labels"] if item["case_id"] == review.case_id and item["reviewer"] == review.reviewer]
    data["history"].extend(prior)
    data["labels"] = [item for item in data["labels"] if item not in prior] + [label]
    atomic_json(path, data)
    return label


def review_stats(bundle, reviews):
    rows = {row["id"]: row for row in bundle["cases"]}
    reviewed = {item["case_id"] for item in reviews}
    generated = {row["id"] for row in bundle["cases"] if row.get("output") is not None}
    # A rubric mismatch is diagnostic only; this is not judge accuracy or a
    # representative population estimate, especially with one reviewer.
    disagreements = []
    for label in reviews:
        judgment = rows[label["case_id"]].get("judgment") or {}
        for key in ("correctness", "coverage", "usefulness"):
            if label.get(key) is not None and judgment.get(key) is not None:
                disagreements.append(abs(label[key] - judgment[key]))
    return {"reviewed_cases": len(reviewed), "generated_cases": len(generated),
            "automated_reviewed_cases": sum(row.get("judgment") is not None for row in rows.values()),
            "manual_review_required": False,
            "pending_cases": sorted(generated - reviewed), "labels": len(reviews),
            "judge_compared_dimensions": len(disagreements),
            "mean_absolute_judge_difference": sum(disagreements) / len(disagreements) if disagreements else None,
            "note": "Diagnostic rubric agreement on reviewed outputs; not population-level judge accuracy."}
