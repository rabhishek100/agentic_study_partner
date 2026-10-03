"""Lexical-first retrieval across the published lectures in one course."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from observability import traced
from storage.database import parse_owner_id
from video.course_contracts import CourseEvidenceRef
from video.course_repository import CourseNotFoundError
from video.embeddings import ImageEmbedder, TextEmbedder
from video.retrieval import VideoNotReadyError, retrieve_video_evidence


class _MemoizedQueryEmbedder:
    """One embedder that charges for a given query text at most once.

    A course question is asked of every selected lecture, and each lecture's
    retrieval embedded the query again from scratch: the same sentence, the
    same model, the same vector, bought twenty times for a twenty-lecture
    course, and twice that whenever image search was on too. The per-lecture
    retriever is right to ask for the vector it needs — it just should not be
    the thing that pays for it repeatedly.

    Scoped to a single question. Nothing is cached across requests, so a
    changed model or a changed query is never served a stale vector, and the
    identity attributes the embedding lookup filters on are delegated
    unchanged to the real embedder.
    """

    def __init__(self, embedder) -> None:
        self._embedder = embedder
        self._results: dict[str, object] = {}
        self.model_name = embedder.model_name
        self.model_revision = embedder.model_revision
        self.dimension = embedder.dimension

    def __getattr__(self, name: str):
        return getattr(self._embedder, name)

    def embed_query(self, text: str):
        if text not in self._results:
            self._results[text] = self._embedder.embed_query(text)
        return self._results[text]

    @property
    def calls(self) -> int:
        """How many query embeddings this question actually purchased."""

        return len(self._results)


@dataclass(frozen=True)
class CourseRetrieval:
    evidence: tuple[CourseEvidenceRef, ...]
    versions: dict[UUID, UUID]
    excluded_video_ids: tuple[UUID, ...]


@traced("video.course_retrieval.retrieve_course_evidence", flow="retrieval")
def retrieve_course_evidence(
    connection: Connection,
    *,
    owner_id: str | UUID,
    course_id: str | UUID,
    video_ids: list[UUID],
    query: str,
    limit: int = 12,
    per_lecture_limit: int = 4,
    text_embedder: TextEmbedder | None = None,
    image_embedder: ImageEmbedder | None = None,
) -> CourseRetrieval:
    """Search selected member lectures and retain both relevance and diversity."""

    owner, course = parse_owner_id(owner_id), UUID(str(course_id))
    if not video_ids:
        raise ValueError("a course question requires at least one lecture")
    if not 2 <= limit <= 24:
        raise ValueError("course evidence limit must be between 2 and 24")
    rows = connection.execute(
        """
        select lecture.video_id, lecture.lecture_index,
               coalesce(lecture.title_override, video.title) as video_title
        from video.course_lectures as lecture
        join video.videos as video
          on video.id = lecture.video_id and video.owner_id = lecture.owner_id
        where lecture.owner_id = %s and lecture.course_id = %s
          and lecture.video_id = any(%s::uuid[])
        order by lecture.lecture_index
        """,
        (owner, course, video_ids),
    ).fetchall()
    if len(rows) != len(set(video_ids)):
        raise CourseNotFoundError("selected lectures must belong to this course")

    # Wrapped once for the whole question, so the identical query text is
    # embedded once no matter how many lectures are searched.
    text_embedder = (
        _MemoizedQueryEmbedder(text_embedder) if text_embedder is not None else None
    )
    image_embedder = (
        _MemoizedQueryEmbedder(image_embedder) if image_embedder is not None else None
    )

    versions: dict[UUID, UUID] = {}
    excluded: list[UUID] = []
    candidates: dict[UUID, list[tuple[object, dict]]] = {}
    for row in rows:
        video_id = row["video_id"]
        try:
            version_id, found = retrieve_video_evidence(
                connection,
                owner_id=owner,
                video_id=video_id,
                query=query,
                limit=max(2, per_lecture_limit),
                # Cross-lecture evidence should stay near the actual match;
                # the per-lecture retriever still adds a missing modality.
                timeline_window_ms=60_000,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
            )
        except VideoNotReadyError:
            excluded.append(video_id)
            continue
        versions[video_id] = version_id
        candidates[video_id] = [(item, row) for item in found]

    # Give every matching lecture one place before ranking the remainder.
    # This prevents one verbose lecture from consuming the whole context while
    # still allowing relevance to decide which lectures survive a small limit.
    first = sorted(
        (items[0] for items in candidates.values() if items),
        key=lambda pair: (-float(pair[0].score), pair[1]["lecture_index"]),
    )
    selected = first[:limit]
    selected_ids = {item.id for item, _ in selected}
    remainder = sorted(
        (
            pair
            for items in candidates.values()
            for pair in items
            if pair[0].id not in selected_ids
        ),
        key=lambda pair: (
            -float(pair[0].score),
            pair[1]["lecture_index"],
            pair[0].start_ms or 0,
        ),
    )
    selected.extend(remainder[: max(0, limit - len(selected))])

    page_ids = [item.resource_page_id for item, _ in selected if item.resource_page_id]
    documents = _resource_identity(connection, owner=owner, page_ids=page_ids)
    evidence = tuple(
        CourseEvidenceRef(
            rank=rank,
            evidence_id=item.id,
            modality=item.modality,
            # Retrieval already bounds windows and item count. A display
            # preview here cut off mechanisms while keeping full timestamps.
            excerpt=item.text,
            retrieval_method=item.retrieval_method,
            score=round(float(item.score), 6),
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            page_number=item.page_number,
            frame_id=item.frame_id,
            visual_event_id=item.visual_event_id,
            transcript_segment_id=item.transcript_segment_id,
            resource_page_id=item.resource_page_id,
            resource_id=documents.get(item.resource_page_id, {}).get("resource_id"),
            resource_title=documents.get(item.resource_page_id, {}).get("title"),
            video_id=str(row["video_id"]),
            video_title=row["video_title"],
            lecture_index=row["lecture_index"],
            ingestion_version_id=str(versions[row["video_id"]]),
        )
        for rank, (item, row) in enumerate(selected, start=1)
    )
    return CourseRetrieval(
        evidence=evidence,
        versions=versions,
        excluded_video_ids=tuple(excluded),
    )


def _resource_identity(
    connection: Connection, *, owner: UUID, page_ids: list[int]
) -> dict[int, dict]:
    if not page_ids:
        return {}
    return {
        row["page_id"]: row
        for row in connection.execute(
            """
            select page.id as page_id, resource.id::text as resource_id,
                   resource.title
            from video.resource_pages as page
            join video.resources as resource
              on resource.id = page.resource_id and resource.owner_id = page.owner_id
            where page.owner_id = %s and page.id = any(%s::bigint[])
            """,
            (owner, page_ids),
        ).fetchall()
    }
