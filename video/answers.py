"""Turn retrieved lecture evidence into one grounded, cited answer."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any
from uuid import UUID

from psycopg import Connection

from study.streaming import TokenCallback, invoke_with_streaming
from video.contracts import (
    VideoCitationRef,
    VideoConversationState,
    VideoEvidenceRef,
    VisualCard,
)
from video.embeddings import ImageEmbedder, TextEmbedder
from video.media_store import FilesystemMediaStore, MediaStoreError
from video.models import ChatModel, answer_model, reported_cost_usd
from video.prompts import (
    INSUFFICIENT_EVIDENCE_MARKER,
    build_answer_messages,
    conversation_context,
)
from video.retrieval import VideoNotReadyError, retrieve_video_evidence


SOURCE_CITATION = re.compile(r"\[S(\d+)]")
INSUFFICIENT_EVIDENCE_LANGUAGE = re.compile(
    r"\b(?:the\s+)?evidence\s+is\s+insufficient\b|"
    r"\bnot\s+enough\s+evidence\b|"
    r"\bcannot\s+be\s+answered\s+from\s+(?:the|this)\s+(?:lecture|evidence)\b",
    re.IGNORECASE,
)
MAXIMUM_VISUAL_CARDS = 4
EXCERPT_CHARACTERS = 700


@dataclass(frozen=True)
class VideoAnswerDependencies:
    """Everything answering needs that is not the database."""

    media_store: FilesystemMediaStore | None = None
    text_embedder: TextEmbedder | None = None
    image_embedder: ImageEmbedder | None = None
    model: ChatModel | None = None
    maximum_images: int = 3


@dataclass(frozen=True)
class RetrievedTurn:
    version_id: UUID
    evidence: list[VideoEvidenceRef] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerDraft:
    answer: str
    outcome: str
    citations: list[VideoCitationRef]
    visual_cards: list[VisualCard]
    cost_usd: float
    image_count: int


def retrieve_turn_evidence(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    query: str,
    limit: int = 8,
    timeline_window_ms: int = 60_000,
    dependencies: VideoAnswerDependencies,
) -> RetrievedTurn:
    """Search every modality, then restore each item's own identity."""

    version_id, found = retrieve_video_evidence(
        connection,
        owner_id=owner_id,
        video_id=video_id,
        query=query,
        limit=limit,
        timeline_window_ms=timeline_window_ms,
        text_embedder=dependencies.text_embedder,
        image_embedder=dependencies.image_embedder,
    )
    documents = _resource_identity(
        connection,
        owner_id=owner_id,
        page_ids=[item.resource_page_id for item in found if item.resource_page_id],
    )
    evidence = [
        VideoEvidenceRef(
            rank=item.rank,
            evidence_id=item.id,
            modality=item.modality,
            excerpt=item.text[:EXCERPT_CHARACTERS],
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
        )
        for item in found
    ]
    return RetrievedTurn(version_id=version_id, evidence=evidence)


def synthesize_answer(
    connection: Connection,
    *,
    owner_id: str | UUID,
    question: str,
    evidence: list[VideoEvidenceRef],
    video_title: str,
    state: VideoConversationState,
    dependencies: VideoAnswerDependencies,
    token_callback: TokenCallback | None = None,
) -> AnswerDraft:
    """Answer from the supplied evidence, or say plainly that it cannot."""

    if not evidence:
        return AnswerDraft(
            answer=(
                "Insufficient evidence: nothing in this lecture's transcript, "
                "frames, or linked documents matches that question."
            ),
            outcome="abstain",
            citations=[],
            visual_cards=[],
            cost_usd=0.0,
            image_count=0,
        )
    images = _frame_images(
        connection,
        owner_id=owner_id,
        evidence=evidence,
        dependencies=dependencies,
    )
    model = dependencies.model or answer_model()
    response = invoke_with_streaming(
        model,
        build_answer_messages(
            question=question,
            evidence=evidence,
            video_title=video_title,
            images=images,
            conversation_context=(
                conversation_context(state.recent_messages(turns=2))
                if state.messages
                else None
            ),
        ),
        token_callback=token_callback,
    )
    text = str(response.content).strip()
    insufficient = text.startswith(INSUFFICIENT_EVIDENCE_MARKER) or bool(
        INSUFFICIENT_EVIDENCE_LANGUAGE.search(text)
    )
    if text.startswith(INSUFFICIENT_EVIDENCE_MARKER):
        explanation = text.removeprefix(INSUFFICIENT_EVIDENCE_MARKER).strip()
        text = "Insufficient evidence"
        if explanation:
            text += f": {explanation}"
    citations = _citations(text, evidence)
    return AnswerDraft(
        answer=text,
        outcome="abstain" if insufficient else "answer",
        citations=citations,
        visual_cards=_visual_cards(evidence, citations),
        cost_usd=reported_cost_usd(response),
        image_count=len(images),
    )


def _citations(
    answer: str, evidence: list[VideoEvidenceRef]
) -> list[VideoCitationRef]:
    """Keep only markers that point at evidence actually supplied."""

    by_rank = {item.rank: item for item in evidence}
    citations: list[VideoCitationRef] = []
    seen: set[str] = set()
    for match in SOURCE_CITATION.finditer(answer):
        marker, rank = match.group(0), int(match.group(1))
        item = by_rank.get(rank)
        if marker in seen or item is None:
            continue
        seen.add(marker)
        citations.append(
            VideoCitationRef(
                marker=marker,
                evidence_rank=rank,
                modality=item.modality,
                start_ms=item.start_ms,
                page_number=item.page_number,
                frame_id=item.frame_id,
                resource_id=item.resource_id,
            )
        )
    return citations


def _visual_cards(
    evidence: list[VideoEvidenceRef], citations: list[VideoCitationRef]
) -> list[VisualCard]:
    """Show the strongest visual evidence, preferring what the answer cited."""

    cited = {citation.evidence_rank for citation in citations}
    visual = [item for item in evidence if item.is_visual]
    ordered = sorted(visual, key=lambda item: (item.rank not in cited, item.rank))
    return [
        VisualCard(
            evidence_rank=item.rank,
            frame_id=item.frame_id,
            visual_event_id=item.visual_event_id,
            start_ms=item.start_ms or 0,
            end_ms=item.end_ms,
            summary=item.excerpt[:300],
            kind="transition" if item.modality == "visual_event" else "frame",
        )
        for item in ordered[:MAXIMUM_VISUAL_CARDS]
    ]


def _resource_identity(
    connection: Connection, *, owner_id: str | UUID, page_ids: list[int]
) -> dict[int, dict[str, Any]]:
    if not page_ids:
        return {}
    rows = connection.execute(
        """
        select page.id, resource.id as resource_id, resource.title
        from video.resource_pages as page
        join video.resources as resource
          on resource.id = page.resource_id
         and resource.owner_id = page.owner_id
        where page.owner_id = %s and page.id = any(%s)
        """,
        (UUID(str(owner_id)), page_ids),
    ).fetchall()
    return {
        row["id"]: {"resource_id": str(row["resource_id"]), "title": row["title"]}
        for row in rows
    }


def _frame_images(
    connection: Connection,
    *,
    owner_id: str | UUID,
    evidence: list[VideoEvidenceRef],
    dependencies: VideoAnswerDependencies,
) -> list[tuple[int, str, bytes]]:
    """Attach the top-ranked frames themselves, not only their descriptions."""

    store = dependencies.media_store
    if store is None or dependencies.maximum_images <= 0:
        return []
    wanted = [
        item
        for item in evidence
        if item.modality == "visual_frame" and item.frame_id is not None
    ][: dependencies.maximum_images]
    if not wanted:
        return []
    rows = connection.execute(
        """
        select id, preview_storage_key from video.frames
        where owner_id = %s and id = any(%s)
        """,
        (UUID(str(owner_id)), [item.frame_id for item in wanted]),
    ).fetchall()
    keys = {row["id"]: row["preview_storage_key"] for row in rows}
    images: list[tuple[int, str, bytes]] = []
    for item in wanted:
        key = keys.get(item.frame_id)
        if not key:
            continue
        try:
            payload = store.open_path(
                owner_id=owner_id, storage_key=key
            ).read_bytes()
        except (MediaStoreError, OSError):
            # A missing preview degrades the answer to text-only evidence;
            # it must not fail the turn.
            continue
        images.append((item.rank, "image/jpeg", payload))
    return images


__all__ = [
    "AnswerDraft",
    "RetrievedTurn",
    "VideoAnswerDependencies",
    "VideoNotReadyError",
    "retrieve_turn_evidence",
    "synthesize_answer",
]
