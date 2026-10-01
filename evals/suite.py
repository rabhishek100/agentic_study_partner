"""One resumable bundle for all five flows; adapters own production behavior."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.budget import BudgetStop, atomic_json, experiment_lock

FLOWS = ("chat", "summary", "video_course", "revision_sheet", "interview")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, default=str).encode()).hexdigest()


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    flow: Literal["chat", "summary", "video_course", "revision_sheet", "interview"]
    adapter: str
    title: str
    tier: str
    inputs: dict
    expected: dict
    source: dict
    depends_on: list[str] = Field(default_factory=list)
    aspects: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cases: list[Case]
    requirements: dict[str, dict]

    @model_validator(mode="after")
    def valid_graph(self):
        seen = set()
        for case in self.cases:
            if case.id in seen or any(parent not in seen for parent in case.depends_on):
                raise ValueError("Case IDs must be unique and dependencies must precede their cases")
            seen.add(case.id)
        for name, requirement in self.requirements.items():
            if not set(requirement.get("cases", [])) <= seen:
                raise ValueError(f"Unknown coverage case in {name}")
        return self


def load_manifest(path):
    return Manifest.model_validate_json(Path(path).read_text())


def coverage(manifest, bundle=None):
    rows = {row["id"]: row for row in (bundle or {}).get("cases", [])}
    result = {}
    for name, requirement in manifest.requirements.items():
        cases = requirement.get("cases", [])
        completed = [case for case in cases if rows.get(case, {}).get("status") == "completed"]
        result[name] = {**requirement, "completed_cases": completed,
                        "execution": "complete" if cases and len(completed) == len(cases) else "incomplete",
                        "quality": "human_review_pending" if completed else "unknown"}
    return result


def summary(bundle):
    result = {}
    for flow in FLOWS:
        rows = [row for row in bundle["cases"] if row["flow"] == flow]
        result[flow] = {"cases": len(rows), "statuses": dict(Counter(row["status"] for row in rows)),
                        "generated": sum(row.get("output") is not None for row in rows),
                        "judged": sum(row.get("judgment") is not None for row in rows),
                        "human_reviewed": 0}
    return result


def run_suite(manifest, directory, *, adapters, config, budget=None, judge=None,
              selected=None, retry_failed=False, on_case=lambda row: None):
    """Checkpoint output before judging; resume never regenerates completed output.

    Adapters return output, evidence, artifacts and checks. Failed/interrupted
    generation requires an explicit retry; its previous spend remains committed.
    """
    directory = Path(directory)
    config = dict(config)
    layer = config.get("execution_layer", "fixture")
    if layer == "live" and budget is None:
        raise ValueError("Live execution requires the shared budget")
    identity = fingerprint({"manifest": manifest.model_dump(mode="json"), "config": config})
    wanted = set(selected or [case.id for case in manifest.cases])
    ids = {case.id for case in manifest.cases}
    if not wanted <= ids:
        raise ValueError("Unknown selected case")
    for case in reversed(manifest.cases):
        if case.id in wanted:
            wanted.update(case.depends_on)
    with experiment_lock(directory):
        path = directory / "bundle.json"
        if path.exists():
            bundle = json.loads(path.read_text())
            if bundle["fingerprint"] != identity:
                raise ValueError("Source, manifest or configuration changed; use a distinct experiment")
        else:
            bundle = {"version": "five-flow-v1", "fingerprint": identity, "config": config,
                      "manifest": manifest.model_dump(mode="json"),
                      "cases": [{"id": case.id, "flow": case.flow, "title": case.title,
                                 "tier": case.tier, "source": case.source, "expected": case.expected,
                                 "inputs": case.inputs, "aspects": case.aspects, "status": "queued"}
                                for case in manifest.cases]}
        by_id = {row["id"]: row for row in bundle["cases"]}
        def save():
            bundle["summary"] = summary(bundle)
            bundle["coverage"] = coverage(manifest, bundle)
            if budget is not None:
                bundle["budget"] = {"cap_usd": budget.data["cap_usd"],
                                     "committed_usd": str(budget.committed),
                                     "reported_usd": str(sum((Decimal(str(c.get("cost_usd", 0))) for c in budget.data["calls"]), Decimal(0))),
                                     "unknown_reservations": sum(c["status"] == "reserved" for c in budget.data["calls"])}
            atomic_json(path, bundle)
        save()
        for case in manifest.cases:
            row = by_id[case.id]
            if row.get("output") is not None:
                if row["output_hash"] != fingerprint({key: row.get(key) for key in ("output", "evidence", "artifacts")}):
                    raise ValueError("Saved output changed; previous review identity is invalid")
            if case.id not in wanted or row["status"] == "completed":
                continue
            if row.get("output") is None and row["status"] in {"generating", "failed", "interrupted", "blocked"} and not retry_failed:
                row["status"] = "interrupted" if row["status"] == "generating" else row["status"]
                save()
                continue
            if any(by_id[parent].get("output") is None for parent in case.depends_on):
                row.update(status="dependency_unavailable")
                save()
                continue
            if budget is not None:
                budget.case_id, budget.phase = case.id, "generation"
            on_case(row)
            try:
                if row.get("output") is None:
                    row.update(status="generating", error=None)
                    save()
                    value = adapters[case.adapter](case, [by_id[parent] for parent in case.depends_on], directory)
                    if "output" not in value:
                        raise ValueError("Adapter did not return an output")
                    if set(value) - {"output", "state", "evidence", "artifacts", "checks", "source_fingerprint",
                                     "trace_id", "trace_url", "metrics", "limits", "request_capture"}:
                        raise ValueError("Adapter returned unknown or identity-overwriting fields")
                    row.update(value)
                    row["output_hash"] = fingerprint({key: row.get(key) for key in ("output", "evidence", "artifacts")})
                    row["status"] = "generated"
                    save()
                if judge is not None and row.get("judgment") is None:
                    if budget is not None:
                        budget.phase = "judging"
                    row["status"] = "judging"
                    save()
                    row["judgment"] = judge(case, row, directory)
                row.update(status="completed", error=None)
                save()
            except BudgetStop as error:
                row.update(status="blocked", error={"kind": "budget", "message": str(error)})
                row.setdefault("issues", []).append(row["error"])
                save()
                break
            except (KeyboardInterrupt, SystemExit):
                row.update(status="generated" if row.get("output") is not None else "interrupted")
                save()
                raise
            except Exception as error:
                row.update(status="judge_failed" if row.get("output") is not None else "failed",
                           error={"kind": type(error).__name__, "message": str(error)})
                row.setdefault("issues", []).append(row["error"])
                save()
        return bundle
