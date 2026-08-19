"""Evaluation of source-first study: does an anchored question stay anchored?

Four things are measured, and they are not the same kind of thing.

*   **Anchor recall** and **anchor lead** are about retrieval. The passage the
    reader was looking at has to reach the model, and it has to reach it first,
    or "ask about this page" is a description of an intention rather than of a
    mechanism.
*   **Rung correctness** is about routing. Cases are written to be answerable
    from the open source, from the wider library, and from neither, and the
    recorded rung has to agree with which of those it was.
*   **No-blend** is not a metric. An answer either rests on the reader's
    sources and cites them or it does not and says so; an ungrounded answer
    carrying citation markers is a defect, and the run fails rather than
    scoring lower.
*   **Selection resolution** is about the deterministic half, and it is the
    only one that needs no model at all: given selections taken verbatim from
    rendered pages, how many match canonical text?

The runner is a protocol for the same reason `evals.multiturn` makes one: the
scoring has to be testable without spending a model call per case.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from study.contracts import GROUNDED_RUNGS, TurnResult

# A citation marker in a rendered answer. The no-blend check looks for these in
# an answer that is not supposed to have any.
SOURCE_MARKER = re.compile(r"\[S(\d+)\]")


class AnchorResolver(Protocol):
    """Turns a case's anchor into the canonical ids it names."""

    def __call__(self, anchor: dict[str, Any]) -> tuple[bool, tuple[str, ...]]: ...


class TurnRunner(Protocol):
    """Runs one anchored question and returns the turn it produced."""

    def __call__(
        self,
        question: str,
        anchor: dict[str, Any],
        *,
        stay_in_source: bool,
    ) -> TurnResult: ...


@dataclass
class CaseResult:
    case_id: str
    checks: dict[str, Any] = field(default_factory=dict)
    failures: tuple[str, ...] = ()
    result: TurnResult | None = None
    error: str | None = None


def _evidence_ids(result: TurnResult) -> list[str]:
    """The identities of a turn's evidence, in the order the model saw them."""

    return [
        str(reference.chunk_id)
        for reference in result.evidence
        if reference.chunk_id
    ]


def _cited_markers(answer: str) -> set[int]:
    return {int(match.group(1)) for match in SOURCE_MARKER.finditer(answer or "")}


def score_case(
    case: dict[str, Any],
    result: TurnResult,
    *,
    resolved: tuple[str, ...],
    matched: bool,
) -> CaseResult:
    """Judge one anchored turn against what the case said should happen."""

    checks: dict[str, Any] = {}
    failures: list[str] = []

    checks["selection_resolved"] = matched
    if case.get("expect_selection_resolves") is not None:
        if matched is not case["expect_selection_resolves"]:
            failures.append(
                "selection resolved" if matched else "selection did not resolve"
            )

    evidence = _evidence_ids(result)
    anchored = set(resolved)
    # A turn that produced no evidence at all did not fail to retrieve the
    # anchor — it never retrieved anything. That happens for two legitimate
    # reasons: the ladder climbed past the reader's sources, and the router
    # asked a clarifying question instead of answering. Scoring either as an
    # anchor-recall failure measures the wrong thing, and it did on the first
    # run: an escalation that worked perfectly was reported as a miss.
    if anchored and not evidence:
        checks["anchor_recall"] = None
        checks["anchor_leads"] = None
    elif anchored:
        found = anchored.intersection(evidence)
        checks["anchor_recall"] = len(found) / len(anchored)
        # The anchored passage is pinned, so it enters the evidence list first
        # and carries the lowest markers. If it does not lead, the reader's
        # page is competing with retrieval rather than grounding the answer.
        checks["anchor_leads"] = bool(evidence) and evidence[0] in anchored
        if case.get("expect_anchor_in_evidence", True) and checks["anchor_recall"] == 0:
            failures.append("no anchored passage reached the evidence")
    else:
        checks["anchor_recall"] = None
        checks["anchor_leads"] = None

    rung = result.grounding_rung
    checks["rung"] = rung
    expected_rung = case.get("expect_rung")
    if expected_rung:
        checks["rung_matches"] = rung == expected_rung
        if rung != expected_rung:
            failures.append(f"answered at {rung}, expected {expected_rung}")

    grounded = rung in GROUNDED_RUNGS if rung else result.source_type == "book_library"
    checks["grounded"] = grounded
    if case.get("expect_grounded") is not None:
        if grounded is not case["expect_grounded"]:
            failures.append("grounded" if grounded else "not grounded")

    # The hard one. An answer that left the reader's sources may not carry
    # citation markers: there is nothing for them to point at, and an answer
    # that looks cited while resting on nothing is the failure this whole
    # feature is arranged to prevent.
    markers = _cited_markers(result.answer)
    checks["blended"] = bool(markers) and not grounded
    if checks["blended"]:
        failures.append("ungrounded answer carries citation markers")

    # Recorded so a run can tell an answer from a question back. A clarify is
    # not a wrong answer; it is the absence of one, and it says the case was
    # ambiguous rather than that the system was.
    checks["outcome"] = result.outcome

    checks["widenings"] = [
        {"from": step.from_rung, "to": step.to_rung, "reason": step.reason}
        for step in result.widenings
    ]
    # Every widening carries the verdict that caused it, or the ladder is a
    # claim again rather than a record.
    if any(not step.reason.strip() for step in result.widenings):
        failures.append("a widening was recorded without a reason")

    return CaseResult(
        case_id=case["id"],
        checks=checks,
        failures=tuple(failures),
        result=result,
    )


def _rate(rows: Sequence[CaseResult], field_name: str) -> float | None:
    values = [
        row.checks[field_name]
        for row in rows
        if row.checks.get(field_name) is not None
    ]
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)


def evaluate_source_first(
    cases: Sequence[dict[str, Any]],
    *,
    resolve: AnchorResolver,
    run_turn: TurnRunner,
    on_case: Callable[[CaseResult], None] | None = None,
) -> dict[str, Any]:
    """Run every case and summarise what held."""

    rows: list[CaseResult] = []
    for case in cases:
        try:
            matched, resolved = resolve(case["anchor"])
            result = run_turn(
                case["question"],
                case["anchor"],
                stay_in_source=bool(case.get("stay_in_source")),
            )
            row = score_case(case, result, resolved=resolved, matched=matched)
        except Exception as failure:  # noqa: BLE001 - reported, not swallowed
            row = CaseResult(case_id=case["id"], error=str(failure))
        rows.append(row)
        if on_case:
            on_case(row)

    scored = [row for row in rows if row.error is None]
    blended = [row for row in scored if row.checks.get("blended")]
    return {
        "cases": len(rows),
        "errors": [row.case_id for row in rows if row.error],
        "anchor_recall": _rate(scored, "anchor_recall"),
        "anchor_leads": _rate(scored, "anchor_leads"),
        "rung_accuracy": _rate(scored, "rung_matches"),
        "selection_resolution_rate": _rate(scored, "selection_resolved"),
        # Reported as a list rather than a rate: one is too many.
        "blended": [row.case_id for row in blended],
        "failures": {
            row.case_id: list(row.failures) for row in scored if row.failures
        },
        "rows": rows,
    }
