"""Side chat context for a lecture conversation.

The assembly rules are the book chat's — quoted passages first, the evidence
their markers name pinned ahead of fresh retrieval, surrounding context under a
token budget — so this module is an adapter over `study.side_context` rather
than a second implementation. What differs is what a marker points at: a book
chat pins chunks, a lecture pins evidence units, and a unit belongs to the
published ingestion version that produced it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from study.side_context import ParentTurn, ranked_identities
from video.contracts import VideoEvidenceRef, VideoTurnResult
from video.retrieval import VideoEvidence

# The retrieval method recorded for a unit that was not searched for but named,
# by the citation markers inside a passage the reader highlighted.
ANCHOR_RETRIEVAL_METHOD = "anchor_pin"


def parent_turn(
    turn_index: int,
    question: str,
    answer: str,
    result: VideoTurnResult,
) -> ParentTurn:
    """Adapt one recorded lecture turn for the shared assembler."""

    return ParentTurn(
        turn_index=turn_index,
        question=question,
        answer=answer,
        ranked_ids=ranked_identities(result.evidence, identity="evidence_id"),
        citation_ranks=tuple(
            citation.evidence_rank for citation in result.citations
        ),
    )


def parent_turns(rows: Sequence[dict[str, Any]]) -> list[ParentTurn]:
    """Adapt the parent conversation's stored turns, skipping unusable ones.

    A running or failed turn has no result to anchor to; a side chat opened over
    one would have nothing to pin and nothing to quote.
    """

    turns: list[ParentTurn] = []
    for row in rows:
        if not row.get("answer") or not row.get("result_json"):
            continue
        turns.append(
            parent_turn(
                row["turn_index"],
                row["question"],
                row["answer"],
                VideoTurnResult.model_validate(row["result_json"]),
            )
        )
    return turns


def pinned_evidence(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    ingestion_version_id: str | UUID,
    evidence_ids: Sequence[str],
) -> tuple[list[VideoEvidence], tuple[str, ...]]:
    """Load specific evidence units, in the order named.

    Returns the units found and the ids that were not, because the second half
    matters: a unit is scoped to the ingestion version that produced it, so a
    lecture re-ingested since the anchored answer may no longer contain the
    moment the reader highlighted. Dropping it and saying so is honest; silently
    resolving the anchor against a different cut of the lecture is not.
    """

    if not evidence_ids:
        return [], ()
    rows = connection.execute(
        """
        select id, modality, retrieval_text, start_ms, end_ms, page_number,
               transcript_segment_id, frame_id, visual_event_id, resource_page_id
        from video.evidence_units
        where owner_id = %s
          and video_id = %s
          and ingestion_version_id = %s
          and id = any(%s)
        """,
        (
            parse_owner_id(owner_id),
            str(video_id),
            str(ingestion_version_id),
            list(dict.fromkeys(evidence_ids)),
        ),
    ).fetchall()
    found = {row["id"]: row for row in rows}
    units = [
        VideoEvidence(
            id=row["id"],
            modality=row["modality"],
            text=row["retrieval_text"],
            start_ms=int(row["start_ms"]) if row["start_ms"] is not None else None,
            end_ms=int(row["end_ms"]) if row["end_ms"] is not None else None,
            page_number=(
                int(row["page_number"]) if row["page_number"] is not None else None
            ),
            transcript_segment_id=row["transcript_segment_id"],
            frame_id=row["frame_id"],
            visual_event_id=row["visual_event_id"],
            resource_page_id=row["resource_page_id"],
            # Anchored units are named, not ranked against the query. A sentinel
            # score keeps that visible in the inspector rather than implying a
            # retrieval score.
            score=1.0,
            retrieval_method=ANCHOR_RETRIEVAL_METHOD,
        )
        for row in (found[key] for key in dict.fromkeys(evidence_ids) if key in found)
    ]
    missing = tuple(key for key in dict.fromkeys(evidence_ids) if key not in found)
    return units, missing


def merge_pinned(
    pinned: Sequence[VideoEvidence],
    retrieved: Sequence[VideoEvidence],
    *,
    limit: int,
) -> list[VideoEvidence]:
    """Put the anchored units first, then retrieval, and renumber the ranks.

    Ranks are what citation markers refer to, so they have to describe the list
    the model is actually given: the pinned units take `[S1]` onward, which is
    the mechanism behind a side chat's priority context.
    """

    ordered = list(pinned)
    seen = {unit.id for unit in ordered}
    for unit in retrieved:
        if unit.id in seen:
            continue
        ordered.append(unit)
        seen.add(unit.id)
    return [
        replace(unit, rank=index)
        for index, unit in enumerate(ordered[:limit], start=1)
    ]


def evidence_ref_ids(evidence: Sequence[VideoEvidenceRef]) -> tuple[str, ...]:
    """The evidence ids a recorded turn's references name."""

    return tuple(reference.evidence_id for reference in evidence)
