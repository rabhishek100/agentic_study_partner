"""Replay of the frozen lecture conversations through the real video graph.

The books half of this project has a gold set, so every retrieval and prompt
change there is defended with a number. The video half has had none, which
means everything built for it has been verified as working and never as
better. This is the runner that closes that gap, and it is deliberately the
same shape as `evals.multiturn`: replay predicted state forward, score only
behaviour a reader could observe, and keep the optional judge diagnostic
rather than load-bearing.

Two things it measures that the book runner has no equivalent for.

**Summary substance.** The runtime scores a chapter summary by whether every
required stretch was cited. `evals.video_coverage` scores whether the citing
sentence said anything about that stretch, and the two rates are reported side
by side.

**The rewriting ablation.** Every follow-up in the gold set is replayed through
retrieval three ways — as the reader typed it, as the router rewrote it, and as
the gold rewrite — against the same published version and the same anchors.
Rewriting is the most expensive thing the router does and the only claim made
for it is that a follow-up resolves; whether it retrieves better than not
rewriting was, until this ran, an assumption.

Evidence is matched on time, not identity. See `judgment_policy` in
`evaluation/video_gold.json`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from psycopg import Connection

from evals.video_coverage import SummarySubstance, measure_summary_substance
from video.answers import (
    VideoAnswerDependencies,
    retrieve_turn_evidence,
)
from video.contracts import VideoConversationState, VideoEvidenceRef, VideoTurnResult
from video.conversation import (
    DEFAULT_EVIDENCE_LIMIT,
    DEFAULT_TIMELINE_WINDOW_MS,
    execute_video_turn,
    new_video_conversation_state,
)
from video.lecture import coverage_units, load_chapters, load_lecture_scope


# The arms of the ablation, in the order a report should read them: what the
# reader typed, what the router made of it, and the rewrite the gold set says
# was available to be made.
REWRITE_ARMS = ("raw", "rewritten", "gold")


class AnswerJudge(Protocol):
    def evaluate(self, **kwargs): ...


@dataclass
class VideoProjectRunner:
    """The real graph, over one lecture, with production dependencies."""

    connection: Connection
    owner_id: str | UUID
    video_id: str | UUID
    video_title: str
    dependencies: VideoAnswerDependencies = field(
        default_factory=VideoAnswerDependencies
    )
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT

    def __call__(
        self, question: str, state: VideoConversationState
    ) -> tuple[VideoTurnResult, VideoConversationState]:
        return execute_video_turn(
            self.connection,
            question,
            state,
            owner_id=self.owner_id,
            video_id=self.video_id,
            video_title=self.video_title,
            dependencies=self.dependencies,
            evidence_limit=self.evidence_limit,
        )

    def retrieve(self, query: str) -> list[VideoEvidenceRef]:
        """One retrieval, with the settings a first attempt would use.

        The broadened retry is deliberately excluded from the ablation: it
        changes the limit and the timeline window, and a rewrite that only
        wins after the system has already paid for a second search has not
        shown that rewriting is what helped.
        """

        return retrieve_turn_evidence(
            self.connection,
            owner_id=self.owner_id,
            video_id=self.video_id,
            query=query,
            limit=self.evidence_limit,
            timeline_window_ms=DEFAULT_TIMELINE_WINDOW_MS,
            dependencies=self.dependencies,
        ).evidence


def _overlaps(item: VideoEvidenceRef, anchor: dict[str, Any]) -> bool:
    """Does this evidence item come from the place the anchor names?

    A moment for anything on the timeline, and a page for the linked document —
    a slide deck has no timestamps, and inventing an alignment between its
    pages and the lecture is exactly what the ingestion pipeline refuses to do.
    So an anchor names whichever locator its modality actually has.
    """

    if item.modality not in set(anchor["modalities"]):
        return False
    pages = anchor.get("resource_pages")
    if pages is not None:
        return item.page_number in set(pages)
    if item.start_ms is None:
        return False
    start, end = int(anchor["start_ms"]), int(anchor["end_ms"])
    item_end = item.end_ms if item.end_ms is not None else item.start_ms
    return item.start_ms < end and item_end > start


def anchor_recall(
    anchors: list[dict[str, Any]], evidence: list[VideoEvidenceRef]
) -> float:
    """Fraction of required stretches that any retrieved item reached."""

    required = [anchor for anchor in anchors if anchor.get("role") == "required"]
    if not required:
        return 1.0
    hit = sum(
        1
        for anchor in required
        if any(_overlaps(item, anchor) for item in evidence)
    )
    return hit / len(required)


def _near_miss_rate(
    anchors: list[dict[str, Any]], evidence: list[VideoEvidenceRef]
) -> float:
    """How much of an unanswerable turn's retrieval is plausible-but-wrong.

    Reported rather than scored. Retrieving a near miss is correct behaviour —
    it is what a lexical retriever should do — and only answering from it is
    the failure, which `outcome` already captures.
    """

    if not evidence:
        return 0.0
    return sum(
        1
        for item in evidence
        if any(_overlaps(item, anchor) for anchor in anchors)
    ) / len(evidence)


def _expected_outcome(turn: dict[str, Any]) -> str:
    if turn["expected_route"] == "clarify":
        return "clarify"
    return "answer" if turn.get("answerable", True) else "abstain"


def _score(turn: dict[str, Any], result: VideoTurnResult) -> dict[str, Any]:
    evidence_ranks = {item.rank for item in result.evidence}
    checks: dict[str, Any] = {
        "route": result.route == turn["expected_route"],
        "history_dependency": (
            result.history_dependency == turn["history_dependency"]
        ),
        "outcome": result.outcome == _expected_outcome(turn),
        # Every marker in the answer must point at evidence that was actually
        # supplied to the model; a marker for rank 9 in an eight-item set is a
        # fabricated locator whatever the prose around it says.
        "citations_valid": all(
            citation.evidence_rank in evidence_ranks
            for citation in result.citations
        ),
    }
    anchors = turn.get("expected_evidence") or []
    if turn.get("answerable", True):
        checks["evidence_recall"] = anchor_recall(anchors, result.evidence)
        checks["cited_evidence_recall"] = anchor_recall(
            anchors,
            [
                item
                for item in result.evidence
                if item.rank in {c.evidence_rank for c in result.citations}
            ],
        )
    else:
        checks["near_miss_share"] = _near_miss_rate(
            turn.get("near_miss_evidence") or [], result.evidence
        )
    if turn.get("requires_visual_evidence"):
        # The modality-aware sufficiency check exists for exactly these turns;
        # scoring them without it would let a fluent transcript-only answer
        # about a diagram count as a success.
        checks["visual_evidence_present"] = any(
            item.is_visual or item.modality == "resource_page"
            for item in result.evidence
        )
    return checks


def _rewrite_arms(
    turn: dict[str, Any], result: VideoTurnResult, runner: VideoProjectRunner
) -> dict[str, Any] | None:
    """Retrieve the same follow-up three ways and compare against the anchors.

    Only the query differs between arms. The published version, the retrieval
    limits, the embedder, and the anchors are identical, so a difference in
    recall is attributable to the rewrite and to nothing else.
    """

    anchors = turn.get("expected_evidence") or []
    if not turn.get("rewrite_probe") or not anchors:
        return None
    queries = {
        "raw": turn["user"],
        "rewritten": result.standalone_query or turn["user"],
        "gold": turn.get("expected_standalone_query") or turn["user"],
    }
    arms: dict[str, Any] = {"queries": queries, "recall": {}}
    for arm in REWRITE_ARMS:
        arms["recall"][arm] = anchor_recall(anchors, runner.retrieve(queries[arm]))
    arms["rewrite_changed_query"] = (
        queries["rewritten"].strip().casefold() != queries["raw"].strip().casefold()
    )
    arms["rewrite_helped"] = arms["recall"]["rewritten"] > arms["recall"]["raw"]
    arms["rewrite_hurt"] = arms["recall"]["rewritten"] < arms["recall"]["raw"]
    return arms


def _summary_substance(
    turn: dict[str, Any], result: VideoTurnResult, runner: VideoProjectRunner
) -> SummarySubstance | None:
    if not turn.get("measures_summary_coverage") or result.outcome != "answer":
        return None
    scope = load_lecture_scope(
        runner.connection, owner_id=runner.owner_id, video_id=runner.video_id
    )
    chapters = load_chapters(
        runner.connection, owner_id=runner.owner_id, video_id=runner.video_id
    )
    return measure_summary_substance(
        result.answer, scope=scope, units=coverage_units(scope, chapters)
    )


def _mean(rows: list[dict], field_name: str) -> float:
    values = [
        row["checks"][field_name]
        for row in rows
        if field_name in (row.get("checks") or {})
    ]
    return sum(values) / len(values) if values else 0.0


def _rewrite_summary(rows: list[dict]) -> dict[str, Any]:
    """The replay's verdict, stated as a comparison rather than a score."""

    probes = [row["rewrite_arms"] for row in rows if row.get("rewrite_arms")]
    if not probes:
        return {"probes": 0}
    return {
        "probes": len(probes),
        "queries_actually_rewritten": sum(
            1 for probe in probes if probe["rewrite_changed_query"]
        ),
        "mean_recall": {
            arm: round(
                sum(probe["recall"][arm] for probe in probes) / len(probes), 4
            )
            for arm in REWRITE_ARMS
        },
        "rewrite_helped": sum(1 for probe in probes if probe["rewrite_helped"]),
        "rewrite_hurt": sum(1 for probe in probes if probe["rewrite_hurt"]),
        "rewrite_neutral": sum(
            1
            for probe in probes
            if not probe["rewrite_helped"] and not probe["rewrite_hurt"]
        ),
        # How much of the achievable gain the router's own rewrite captured.
        # Reported as null when the gold rewrite gains nothing over the raw
        # message, because a ratio against a zero denominator would read as a
        # failure of the router rather than as a set with nothing to gain.
        "share_of_gold_gain": _share_of_gold_gain(probes),
    }


def _share_of_gold_gain(probes: list[dict]) -> float | None:
    raw = sum(probe["recall"]["raw"] for probe in probes)
    rewritten = sum(probe["recall"]["rewritten"] for probe in probes)
    gold = sum(probe["recall"]["gold"] for probe in probes)
    if gold <= raw:
        return None
    return round((rewritten - raw) / (gold - raw), 4)


def _coverage_summary(rows: list[dict]) -> dict[str, Any]:
    measured = [row["summary_substance"] for row in rows if row.get("summary_substance")]
    if not measured:
        return {"summaries": 0}
    return {
        "summaries": len(measured),
        "mean_cited_rate": round(
            sum(item["cited_rate"] for item in measured) / len(measured), 4
        ),
        "mean_substantive_rate": round(
            sum(item["substantive_rate"] for item in measured) / len(measured), 4
        ),
        "vacuous_citations": sum(
            item["vacuous_citation_count"] for item in measured
        ),
    }


def evaluate_retrieval_only(
    conversations: list[dict[str, Any]],
    runner: VideoProjectRunner,
    *,
    on_turn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Retrieval alone, scored against the anchors, with nothing generated.

    Every turn is retrieved with its gold rewrite rather than the router's, so
    routing, answering and conversation state are all held still and a change
    in this number is a change in retrieval and nothing else. One query
    embedding per turn is the entire cost, which is what makes it the loop
    worth iterating in — the full run is for confirming, not for exploring.

    The modality mix is reported alongside recall because it is the diagnostic
    that found the first defect: eight evidence slots of which exactly one
    could ever hold transcript.
    """

    rows: list[dict[str, Any]] = []
    for conversation in conversations:
        for turn in conversation["turns"]:
            anchors = turn.get("expected_evidence") or []
            # Whole-lecture routes never search: they load the complete
            # transcript. Scoring them here would measure a retrieval that
            # production does not perform for these questions.
            if not anchors or turn["expected_route"] != "evidence_qa":
                continue
            if on_turn:
                on_turn(turn["turn_id"])
            query = turn.get("expected_standalone_query") or turn["user"]
            evidence = runner.retrieve(query)
            counts = Counter(item.modality for item in evidence)
            rows.append(
                {
                    "turn_id": turn["turn_id"],
                    "query": query,
                    "recall": anchor_recall(anchors, evidence),
                    "modalities": dict(counts),
                    "retrieved": [
                        {
                            "rank": item.rank,
                            "modality": item.modality,
                            "start_ms": item.start_ms,
                            "method": item.retrieval_method,
                            "excerpt": item.excerpt[:160],
                        }
                        for item in evidence
                    ],
                    "wanted": [
                        {
                            "where": (
                                f"pages {anchor['resource_pages']}"
                                if anchor.get("resource_pages")
                                else f"{anchor['start_ms']}-{anchor['end_ms']}ms"
                            ),
                            "modalities": anchor["modalities"],
                            "hit": any(
                                _overlaps(item, anchor) for item in evidence
                            ),
                        }
                        for anchor in anchors
                        if anchor.get("role") == "required"
                    ],
                }
            )

    total = Counter()
    for row in rows:
        total.update(row["modalities"])
    return {
        "summary": {
            "turns": len(rows),
            "anchor_recall": round(
                sum(row["recall"] for row in rows) / len(rows), 4
            )
            if rows
            else 0.0,
            "turns_fully_covered": sum(1 for row in rows if row["recall"] == 1.0),
            "turns_missing_everything": sum(
                1 for row in rows if row["recall"] == 0.0
            ),
            "retrieved_modality_mix": dict(total),
        },
        "turns": rows,
    }


def evaluate_video_conversations(
    conversations: list[dict[str, Any]],
    runner: VideoProjectRunner,
    *,
    answer_judge: AnswerJudge | None = None,
    run_rewrite_ablation: bool = True,
    on_turn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Replay each conversation, carrying predicted state forward."""

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for conversation in conversations:
        state = new_video_conversation_state(
            video_id=runner.video_id, conversation_id=conversation["id"]
        )
        for turn in conversation["turns"]:
            turn_id = turn["turn_id"]
            if on_turn:
                on_turn(turn_id)
            row: dict[str, Any] = {
                "conversation_id": conversation["id"],
                "conversation_title": conversation["title"],
                "turn_id": turn_id,
                "gold": turn,
            }
            try:
                result, state = runner(turn["user"], state)
            except Exception as error:  # noqa: BLE001 - one failed turn, not a run
                errors.append({"turn_id": turn_id, "error": str(error)})
                rows.append(
                    row
                    | {
                        "prediction": None,
                        "checks": {
                            "route": False,
                            "history_dependency": False,
                            "outcome": False,
                            "citations_valid": False,
                        },
                        "error": str(error),
                    }
                )
                continue

            row["prediction"] = result.model_dump(mode="json")
            row["state"] = state.model_dump(mode="json")
            row["checks"] = _score(turn, result)
            substance = _summary_substance(turn, result, runner)
            if substance is not None:
                row["summary_substance"] = substance.summary()
                row["summary_units"] = [
                    {
                        "label": unit.label,
                        "required": unit.required,
                        "cited": unit.cited,
                        "substantive": unit.substantive,
                        "matched_terms": list(unit.matched_terms),
                        "distinctive_terms": list(unit.distinctive_terms[:8]),
                        "citing_claims": list(unit.citing_claims),
                    }
                    for unit in substance.units
                ]
            if run_rewrite_ablation:
                arms = _rewrite_arms(turn, result, runner)
                if arms is not None:
                    row["rewrite_arms"] = arms
            if answer_judge is not None:
                row["answer_judgment"] = answer_judge.evaluate(
                    question=turn["user"],
                    reference_answer=turn["reference_answer"],
                    candidate_answer=result.answer,
                    expected_route=turn["expected_route"],
                    answerable=turn.get("answerable", True),
                    turn_id=turn_id,
                ).model_dump(mode="json")
            rows.append(row)

    return {
        "summary": {
            "turns": len(rows),
            "route_accuracy": _mean(rows, "route"),
            "history_dependency_accuracy": _mean(rows, "history_dependency"),
            "outcome_accuracy": _mean(rows, "outcome"),
            "required_evidence_recall": _mean(rows, "evidence_recall"),
            "cited_evidence_recall": _mean(rows, "cited_evidence_recall"),
            "citation_validity": _mean(rows, "citations_valid"),
            "visual_evidence_present": _mean(rows, "visual_evidence_present"),
            "errors": len(errors),
        },
        "summary_coverage": _coverage_summary(rows),
        "rewrite_ablation": _rewrite_summary(rows),
        "turns": rows,
        "errors": errors,
    }


__all__ = [
    "REWRITE_ARMS",
    "evaluate_retrieval_only",
    "VideoProjectRunner",
    "anchor_recall",
    "evaluate_video_conversations",
]
