"""Owner-scoped hybrid retrieval with timeline expansion across modalities."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Literal
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from video.embeddings import (
    IMAGE_DOCUMENT_FORMAT_VERSION,
    TEXT_DOCUMENT_FORMAT_VERSION,
    ImageEmbedder,
    TextEmbedder,
)


VideoModality = Literal[
    "transcript", "visual_frame", "visual_event", "resource_page"
]
RetrievalMethod = Literal[
    "fts", "text_vector", "image_vector", "hybrid", "timeline_expansion"
]
RRF_RANK_CONSTANT = 60
QUERY_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


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
    retrieval_method: RetrievalMethod
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
    text_embedder: TextEmbedder | None = None,
    image_embedder: ImageEmbedder | None = None,
) -> tuple[UUID, tuple[VideoEvidence, ...]]:
    """Retrieve a mixed evidence set from the current published version.

    Lexical matches remain the baseline because lecture questions often reuse
    the lecturer's exact wording. Evidence-text vectors recover paraphrases,
    and diagram-region vectors recover drawings whose surrounding words never
    named them. The three rankings are fused by reciprocal rank rather than by
    comparing incomparable raw scores.

    Timeline expansion then adds nearby evidence from any modality still
    missing, preventing the transcript from silently becoming the only
    modality that can reach answer generation.
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

    candidate_limit = limit * 4
    lexical = _lexical_query(cleaned)
    rows = (
        connection.execute(
            """
            with query as (
                select to_tsquery('english', %s) as value
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
            (lexical, owner, video, version_id, candidate_limit),
        ).fetchall()
        if lexical
        else []
    )
    ranked = [[_evidence(row, method="fts") for row in rows]]
    if text_embedder is not None:
        ranked.append(
            _vector_candidates(
                connection,
                owner=owner,
                video=video,
                version_id=version_id,
                query=cleaned,
                embedder=text_embedder,
                embedding_kind="text",
                document_format_version=TEXT_DOCUMENT_FORMAT_VERSION,
                method="text_vector",
                limit=candidate_limit,
            )
        )
    if image_embedder is not None:
        ranked.append(
            _vector_candidates(
                connection,
                owner=owner,
                video=video,
                version_id=version_id,
                query=cleaned,
                embedder=image_embedder,
                embedding_kind="image",
                document_format_version=IMAGE_DOCUMENT_FORMAT_VERSION,
                method="image_vector",
                limit=max(2, limit * 2),
            )
        )
    direct = (
        _reciprocal_rank_fusion(ranked, limit=candidate_limit)
        if len(ranked) > 1
        else ranked[0]
    )
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


def _lexical_query(query: str) -> str:
    """Match any meaningful word, the way the book pipeline's BM25 does.

    Requiring every word would make an ordinary spoken question match nothing;
    the 'english' configuration then drops the stopwords that carry no signal
    and stems the rest, so "what did he draw" reaches a frame described as
    "drawn".
    """

    terms = list(dict.fromkeys(QUERY_TOKEN.findall(query.casefold())))
    return " | ".join(f"'{term}'" for term in terms)


def _vector_candidates(
    connection: Connection,
    *,
    owner: UUID,
    video: UUID,
    version_id: UUID,
    query: str,
    embedder: TextEmbedder | ImageEmbedder,
    embedding_kind: Literal["text", "image"],
    document_format_version: str,
    method: RetrievalMethod,
    limit: int,
) -> list[VideoEvidence]:
    """Rank evidence by cosine distance in exactly one embedding space.

    An evidence unit can carry several diagram-region vectors, so the closest
    region represents the unit and the rest are dropped before fusion.
    """

    vector = list(embedder.embed_query(query).vectors[0])
    rows = connection.execute(
        """
        select distinct on (evidence.id) evidence.*,
               1 - (embedding.embedding <=> %s::extensions.vector) as score
        from video.evidence_embeddings as embedding
        join video.evidence_units as evidence
          on evidence.id = embedding.evidence_id
         and evidence.ingestion_version_id = embedding.ingestion_version_id
         and evidence.video_id = embedding.video_id
         and evidence.owner_id = embedding.owner_id
        where embedding.owner_id = %s and embedding.video_id = %s
          and embedding.ingestion_version_id = %s
          and embedding.embedding_kind = %s
          and embedding.model_name = %s
          and embedding.model_revision = %s
          and embedding.dimension = %s
          and embedding.document_format_version = %s
        order by evidence.id, embedding.embedding <=> %s::extensions.vector
        """,
        (
            vector,
            owner,
            video,
            version_id,
            embedding_kind,
            embedder.model_name,
            embedder.model_revision,
            embedder.dimension,
            document_format_version,
            vector,
        ),
    ).fetchall()
    candidates = [_evidence(row, method=method) for row in rows]
    candidates.sort(key=lambda item: (-item.score, item.start_ms or 0, item.id))
    return candidates[:limit]


def _reciprocal_rank_fusion(
    ranked_lists: list[list[VideoEvidence]], *, limit: int
) -> list[VideoEvidence]:
    """Fuse rankings from incomparable scoring spaces by rank position."""

    scores: dict[str, float] = {}
    best: dict[str, VideoEvidence] = {}
    methods: dict[str, set[RetrievalMethod]] = {}
    for ranked in ranked_lists:
        for position, item in enumerate(ranked, start=1):
            scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (
                RRF_RANK_CONSTANT + position
            )
            best.setdefault(item.id, item)
            methods.setdefault(item.id, set()).add(item.retrieval_method)
    fused = [
        replace(
            item,
            score=scores[item.id],
            retrieval_method=(
                item.retrieval_method
                if len(methods[item.id]) == 1
                else "hybrid"
            ),
        )
        for item in best.values()
    ]
    fused.sort(key=lambda item: (-item.score, item.start_ms or 0, item.id))
    return fused[:limit]


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
    method: RetrievalMethod,
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
