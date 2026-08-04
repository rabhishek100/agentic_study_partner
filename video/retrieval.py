"""Owner-scoped lexical retrieval with timeline expansion across modalities."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id


VideoModality = Literal[
    "transcript", "visual_frame", "visual_event", "resource_page"
]


class VideoNotReadyError(LookupError):
    """The requested video has no published evidence version for this owner."""


@dataclass(frozen=True)
class VideoEvidence:
    id: str
    modality: VideoModality
    text: str
    start_ms: int | None
    end_ms: int | None
    page_number: int | None
    transcript_segment_id: int | None
    frame_id: int | None
    visual_event_id: int | None
    resource_page_id: int | None
    score: float
    retrieval_method: Literal["fts", "timeline_expansion"]
    rank: int = 0

    @property
    def is_visual(self) -> bool:
        return self.modality in {"visual_frame", "visual_event"}


def retrieve_video_evidence(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    query: str,
    limit: int = 8,
    timeline_window_ms: int = 60_000,
) -> tuple[UUID, tuple[VideoEvidence, ...]]:
    """Retrieve a mixed evidence set from the current published version.

    Direct lexical matches remain the ranking baseline. Timeline expansion
    adds nearby visual evidence when speech matched but the pixels used
    different words, preventing the transcript from silently becoming the
    only modality that can reach answer generation.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    cleaned = " ".join(query.split())
    if not cleaned:
        raise ValueError("video retrieval query cannot be blank")
    if len(cleaned) > 2_000:
        raise ValueError("video retrieval query is too long")
    if not 2 <= limit <= 20:
        raise ValueError("video retrieval limit must be between 2 and 20")
    if not 0 <= timeline_window_ms <= 10 * 60_000:
        raise ValueError("timeline window is outside the supported range")

    version = connection.execute(
        """
        select version.id
        from video.videos as video
        join video.ingestion_versions as version
          on version.id = video.current_ingestion_version_id
         and version.video_id = video.id and version.owner_id = video.owner_id
        where video.id = %s and video.owner_id = %s
          and video.readiness_status in ('ready', 'degraded')
          and version.status in ('ready', 'degraded')
        """,
        (video, owner),
    ).fetchone()
    if version is None:
        raise VideoNotReadyError("video has no published evidence")
    version_id = version["id"]

    rows = connection.execute(
        """
        with query as (
            select websearch_to_tsquery('simple', %s) as value
        )
        select evidence.*,
               ts_rank_cd(evidence.search_vector, query.value, 32) as score
        from video.evidence_units as evidence
        cross join query
        where evidence.owner_id = %s and evidence.video_id = %s
          and evidence.ingestion_version_id = %s
          and evidence.search_vector @@ query.value
        order by score desc, evidence.start_ms nulls last, evidence.id
        limit %s
        """,
        (cleaned, owner, video, version_id, limit * 4),
    ).fetchall()
    direct = [_evidence(row, method="fts") for row in rows]
    selected = _balanced_direct(direct, limit=limit)

    anchor_timestamps = [
        item.start_ms
        for item in selected
        if item.start_ms is not None
    ]
    expansion_modalities: list[str] = []
    if not any(item.modality == "transcript" for item in selected):
        expansion_modalities.append("transcript")
    if not any(item.is_visual for item in selected):
        expansion_modalities.extend(("visual_frame", "visual_event"))
    if anchor_timestamps and expansion_modalities and timeline_window_ms:
        expanded_rows = connection.execute(
            """
            select evidence.*,
                   min(abs(evidence.start_ms - anchor.value)) as distance_ms
            from video.evidence_units as evidence
            cross join unnest(%s::bigint[]) as anchor(value)
            where evidence.owner_id = %s and evidence.video_id = %s
              and evidence.ingestion_version_id = %s
              and evidence.modality = any(%s::text[])
              and abs(evidence.start_ms - anchor.value) <= %s
            group by evidence.id, evidence.owner_id
            order by distance_ms, evidence.start_ms, evidence.id
            limit %s
            """,
            (
                anchor_timestamps,
                owner,
                video,
                version_id,
                expansion_modalities,
                timeline_window_ms,
                max(2, limit // 2),
            ),
        ).fetchall()
        existing = {item.id for item in selected}
        for row in expanded_rows:
            if row["id"] in existing:
                continue
            distance = int(row["distance_ms"])
            score = max(0.0, 1.0 - distance / max(1, timeline_window_ms))
            selected.append(
                _evidence(row, method="timeline_expansion", score=score)
            )
            existing.add(row["id"])

    selected = _trim_mixed(selected, limit=limit)
    return version_id, tuple(
        replace(item, rank=index) for index, item in enumerate(selected, start=1)
    )


def _balanced_direct(
    candidates: list[VideoEvidence], *, limit: int
) -> list[VideoEvidence]:
    selected: list[VideoEvidence] = []
    for predicate in (
        lambda item: item.modality == "transcript",
        lambda item: item.is_visual,
        lambda item: item.modality == "resource_page",
    ):
        match = next((item for item in candidates if predicate(item)), None)
        if match is not None and match not in selected:
            selected.append(match)
    for item in candidates:
        if item not in selected:
            selected.append(item)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _trim_mixed(values: list[VideoEvidence], *, limit: int) -> list[VideoEvidence]:
    if len(values) <= limit:
        return values
    # Preserve at least one transcript and one visual when both exist, then
    # keep the highest-ranked remaining direct/expanded evidence.
    keep: list[VideoEvidence] = []
    for predicate in (
        lambda item: item.modality == "transcript",
        lambda item: item.is_visual,
    ):
        match = next((item for item in values if predicate(item)), None)
        if match is not None and match not in keep:
            keep.append(match)
    keep.extend(item for item in values if item not in keep)
    return keep[:limit]


def _evidence(
    row: dict,
    *,
    method: Literal["fts", "timeline_expansion"],
    score: float | None = None,
) -> VideoEvidence:
    return VideoEvidence(
        id=row["id"],
        modality=row["modality"],
        text=row["retrieval_text"],
        start_ms=(int(row["start_ms"]) if row["start_ms"] is not None else None),
        end_ms=(int(row["end_ms"]) if row["end_ms"] is not None else None),
        page_number=(
            int(row["page_number"]) if row["page_number"] is not None else None
        ),
        transcript_segment_id=row["transcript_segment_id"],
        frame_id=row["frame_id"],
        visual_event_id=row["visual_event_id"],
        resource_page_id=row["resource_page_id"],
        score=float(row.get("score") if score is None else score),
        retrieval_method=method,
    )
