"""Grounded conversation, visual timeline, and frame images for one video.

The turn endpoints mirror the book chat surface — server-authoritative
conversations, one JSON route and one server-sent-events route sharing the
same execution path — so the interface can reuse its chat behavior against
video-specific contracts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from storage.database import connection as database_connection
from study.contracts import ContractModel
from video.answers import VideoAnswerDependencies
from video.contracts import VideoConversationState, VideoTurnResult
from video.conversation import execute_video_turn, new_video_conversation_state
from video.conversation_store import (
    VideoConversationNotFoundError,
    VideoTurnCostExceeded,
    append_turn,
    create_conversation,
    delete_conversation,
    list_conversations,
    load_conversation,
    load_turns,
    rename_conversation,
)
from video.embeddings import (
    OpenRouterRegionEmbedder,
    OpenRouterTextEmbedder,
    VideoEmbeddingProviderError,
)
from video.media_store import FilesystemMediaStore, MediaStoreError
from video.models import VideoModelError
from video.prompts import prompt_snapshot
from video.repository import load_standalone_video
from video.retrieval import VideoNotReadyError


chat_router = APIRouter(tags=["video-chat"])
logger = logging.getLogger("study_partner.api.video_chat")

CONVERSATION_NOT_FOUND = HTTPException(
    status_code=404, detail="video conversation not found"
)
VIDEO_NOT_FOUND = HTTPException(status_code=404, detail="video not found")
IMAGE_NOT_FOUND = HTTPException(status_code=404, detail="frame not found")
IMAGE_CACHE_CONTROL = "private, max-age=31536000, immutable"
MAXIMUM_IMAGE_BYTES = 8 * 1024 * 1024
HEARTBEAT_INTERVAL_SECONDS = 15
_STREAM_DONE = object()


class CreateConversationRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class ConversationSummary(ContractModel):
    conversation_id: UUID
    video_id: UUID
    video_title: str
    title: str
    turn_count: int
    created_at: Any
    updated_at: Any


class ConversationListResponse(ContractModel):
    conversations: list[ConversationSummary]


class TurnView(ContractModel):
    turn_index: int
    question: str
    answer: str | None
    result: VideoTurnResult | None
    cost_usd: float
    trace_id: str | None
    created_at: Any


class ConversationDetail(ContractModel):
    conversation_id: UUID
    video_id: UUID
    title: str
    turns: list[TurnView]


class AskRequest(ContractModel):
    question: str = Field(min_length=1, max_length=2_000)


class AskResponse(ContractModel):
    conversation_id: UUID
    result: VideoTurnResult


class RenameRequest(ContractModel):
    title: str = Field(min_length=1, max_length=200)


class TimelineEntry(ContractModel):
    frame_id: int
    timestamp_ms: int
    summary: str | None
    visual_types: list[str] = Field(default_factory=list)
    ocr_text: str | None = None
    image_url: str


class TimelineResponse(ContractModel):
    video_id: UUID
    entries: list[TimelineEntry]


def _answer_dependencies() -> VideoAnswerDependencies:
    """Build the same retrieval space the published version was indexed in."""

    try:
        text_embedder = OpenRouterTextEmbedder()
        image_embedder = OpenRouterRegionEmbedder()
    except ValueError:
        # Without a key the video still answers lexically rather than 503ing.
        logger.warning("Video answers running without semantic retrieval")
        text_embedder = image_embedder = None
    try:
        store = FilesystemMediaStore()
    except MediaStoreError:
        logger.warning("Video answers running without frame images")
        store = None
    return VideoAnswerDependencies(
        media_store=store,
        text_embedder=text_embedder,
        image_embedder=image_embedder,
    )


def _require_video(connection, video_id: UUID, owner_id: UUID) -> dict[str, Any]:
    video = load_standalone_video(connection, video_id, owner_id=owner_id)
    if video is None:
        raise VIDEO_NOT_FOUND
    return video


@chat_router.post("/api/videos/{video_id}/conversations", status_code=201)
async def start_conversation(
    video_id: UUID,
    request: CreateConversationRequest,
    owner_id: UUID = Depends(current_owner),
) -> ConversationSummary:
    def create() -> dict[str, Any]:
        with database_connection() as connection:
            video = _require_video(connection, video_id, owner_id)
            record = create_conversation(
                connection,
                owner_id=owner_id,
                video_id=video_id,
                title=request.title or "New conversation",
                prompt_snapshot=prompt_snapshot(),
            )
            return {**record, "video_title": video["title"], "turn_count": 0}

    try:
        created = await run_in_threadpool(create)
    except VideoConversationNotFoundError as error:
        raise VIDEO_NOT_FOUND from error
    return ConversationSummary(
        conversation_id=created["id"],
        video_id=created["video_id"],
        video_title=created["video_title"],
        title=created["title"],
        turn_count=0,
        created_at=created["created_at"],
        updated_at=created["updated_at"],
    )


@chat_router.get("/api/videos/{video_id}/conversations")
async def video_conversations(
    video_id: UUID, owner_id: UUID = Depends(current_owner)
) -> ConversationListResponse:
    def load():
        with database_connection(readonly=True) as connection:
            _require_video(connection, video_id, owner_id)
            return list_conversations(
                connection, owner_id=owner_id, video_id=video_id
            )

    return ConversationListResponse(
        conversations=[_summary(row) for row in await run_in_threadpool(load)]
    )


@chat_router.get("/api/video-conversations")
async def all_conversations(
    owner_id: UUID = Depends(current_owner), limit: int = 50
) -> ConversationListResponse:
    def load():
        with database_connection(readonly=True) as connection:
            return list_conversations(connection, owner_id=owner_id, limit=limit)

    return ConversationListResponse(
        conversations=[_summary(row) for row in await run_in_threadpool(load)]
    )


@chat_router.get("/api/video-conversations/{conversation_id}")
async def conversation_detail(
    conversation_id: UUID, owner_id: UUID = Depends(current_owner)
) -> ConversationDetail:
    def load():
        with database_connection(readonly=True) as connection:
            record = load_conversation(
                connection, conversation_id, owner_id=owner_id
            )
            if record is None:
                raise CONVERSATION_NOT_FOUND
            return record, load_turns(
                connection, conversation_id, owner_id=owner_id
            )

    record, turns = await run_in_threadpool(load)
    return ConversationDetail(
        conversation_id=record["id"],
        video_id=record["video_id"],
        title=record["title"],
        turns=[
            TurnView(
                turn_index=row["turn_index"],
                question=row["question"],
                answer=row["answer"],
                result=(
                    VideoTurnResult.model_validate(row["result_json"])
                    if row["result_json"]
                    else None
                ),
                cost_usd=float(row["actual_cost_usd"]),
                trace_id=row["trace_id"],
                created_at=row["created_at"],
            )
            for row in turns
        ],
    )


@chat_router.patch("/api/video-conversations/{conversation_id}")
async def rename(
    conversation_id: UUID,
    request: RenameRequest,
    owner_id: UUID = Depends(current_owner),
) -> ConversationSummary:
    def save():
        with database_connection() as connection:
            record = rename_conversation(
                connection, conversation_id, owner_id=owner_id, title=request.title
            )
            if record is None:
                raise CONVERSATION_NOT_FOUND
            video = load_standalone_video(
                connection, record["video_id"], owner_id=owner_id
            )
            return {**record, "video_title": video["title"] if video else ""}

    record = await run_in_threadpool(save)
    return ConversationSummary(
        conversation_id=record["id"],
        video_id=record["video_id"],
        video_title=record["video_title"],
        title=record["title"],
        turn_count=0,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


@chat_router.delete("/api/video-conversations/{conversation_id}", status_code=204)
async def remove(
    conversation_id: UUID, owner_id: UUID = Depends(current_owner)
) -> Response:
    def delete() -> bool:
        with database_connection() as connection:
            return delete_conversation(
                connection, conversation_id, owner_id=owner_id
            )

    if not await run_in_threadpool(delete):
        raise CONVERSATION_NOT_FOUND
    return Response(status_code=204)


def _run_turn(
    owner_id: UUID,
    conversation_id: UUID,
    question: str,
    token_callback=None,
) -> AskResponse:
    """Load, execute, and persist one turn. Runs on a worker thread."""

    with database_connection() as connection:
        record = load_conversation(connection, conversation_id, owner_id=owner_id)
        if record is None:
            raise CONVERSATION_NOT_FOUND
        video = _require_video(connection, record["video_id"], owner_id)
        state = (
            VideoConversationState.model_validate(record["state_json"])
            if record["state_json"]
            else new_video_conversation_state(
                video_id=record["video_id"], conversation_id=conversation_id
            )
        )
        result, updated = execute_video_turn(
            connection,
            question,
            state,
            owner_id=owner_id,
            video_id=record["video_id"],
            video_title=video["title"],
            dependencies=_answer_dependencies(),
            token_callback=token_callback,
        )
        if result.ingestion_version_id:
            append_turn(
                connection,
                conversation_id,
                owner_id=owner_id,
                video_id=record["video_id"],
                ingestion_version_id=result.ingestion_version_id,
                question=result.question,
                rewritten_query=result.standalone_query or result.question,
                answer=result.answer,
                result=result.model_dump(mode="json"),
                state=updated.model_dump(mode="json"),
                cost_usd=result.cost_usd,
                trace_id=result.trace_id,
            )
    return AskResponse(conversation_id=conversation_id, result=result)


REJECTED_TURN_ERRORS = (
    VideoNotReadyError,
    VideoModelError,
    VideoTurnCostExceeded,
    VideoEmbeddingProviderError,
)


@chat_router.post("/api/video-conversations/{conversation_id}/turns")
async def ask(
    conversation_id: UUID,
    request: AskRequest,
    owner_id: UUID = Depends(current_owner),
) -> AskResponse:
    try:
        return await run_in_threadpool(
            _run_turn, owner_id, conversation_id, request.question.strip()
        )
    except HTTPException:
        raise
    except REJECTED_TURN_ERRORS as error:
        logger.warning("Video turn rejected: %s", error)
        raise HTTPException(status_code=422, detail=str(error)) from error


@chat_router.post("/api/video-conversations/{conversation_id}/turns/stream")
async def ask_stream(
    conversation_id: UUID,
    request: AskRequest,
    owner_id: UUID = Depends(current_owner),
) -> StreamingResponse:
    """Stream answer tokens, ending with one `final` or `error` event."""

    loop = asyncio.get_running_loop()
    events: queue.Queue = queue.Queue()
    question = request.question.strip()

    def on_token(kind: str, text: str) -> None:
        events.put((kind, text))

    def run() -> None:
        try:
            response = _run_turn(owner_id, conversation_id, question, on_token)
            events.put(("final", response.model_dump_json()))
        except HTTPException as error:
            logger.warning("Video turn rejected: %s", error.detail)
            events.put(("error", json.dumps({"detail": error.detail})))
        except REJECTED_TURN_ERRORS as error:
            logger.warning("Video turn rejected: %s", error)
            events.put(("error", json.dumps({"detail": str(error)})))
        except Exception:
            logger.exception("Unhandled error in a video turn")
            events.put(("error", json.dumps({"detail": "internal error"})))
        finally:
            events.put(_STREAM_DONE)

    threading.Thread(target=run, daemon=True).start()

    async def event_stream():
        while True:
            try:
                item = await loop.run_in_executor(
                    None,
                    lambda: events.get(timeout=HEARTBEAT_INTERVAL_SECONDS),
                )
            except queue.Empty:
                # Retrieval and the visual model can run long before the first
                # token; a heartbeat keeps proxies from closing the stream.
                yield ": heartbeat\n\n"
                continue
            if item is _STREAM_DONE:
                return
            kind, payload = item
            if kind == "token":
                yield f"event: token\ndata: {json.dumps({'text': payload})}\n\n"
            elif kind in {"final", "error"}:
                yield f"event: {kind}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.get("/api/videos/{video_id}/timeline")
async def timeline(
    video_id: UUID, owner_id: UUID = Depends(current_owner)
) -> TimelineResponse:
    """The published version's frames, in order, for the visual timeline."""

    def load():
        with database_connection(readonly=True) as connection:
            _require_video(connection, video_id, owner_id)
            return connection.execute(
                """
                select frame.id, frame.timestamp_ms, frame.ocr_text,
                       observation.summary, observation.visual_types
                from video.videos as video
                join video.frames as frame
                  on frame.video_id = video.id
                 and frame.owner_id = video.owner_id
                 and frame.ingestion_version_id
                     = video.current_ingestion_version_id
                left join video.visual_observations as observation
                  on observation.frame_id = frame.id
                 and observation.ingestion_version_id = frame.ingestion_version_id
                where video.id = %s and video.owner_id = %s
                order by frame.timestamp_ms
                """,
                (video_id, owner_id),
            ).fetchall()

    rows = await run_in_threadpool(load)
    return TimelineResponse(
        video_id=video_id,
        entries=[
            TimelineEntry(
                frame_id=row["id"],
                timestamp_ms=int(row["timestamp_ms"]),
                summary=row["summary"],
                visual_types=list(row["visual_types"] or []),
                ocr_text=row["ocr_text"],
                image_url=f"/api/videos/{video_id}/frames/{row['id']}/image",
            )
            for row in rows
        ],
    )


@chat_router.get("/api/videos/{video_id}/resources/{resource_id}/content")
async def resource_content(
    video_id: UUID, resource_id: UUID, owner_id: UUID = Depends(current_owner)
) -> Response:
    """Serve a linked PDF so a page citation can open the page it names."""

    def load() -> bytes:
        with database_connection(readonly=True) as connection:
            row = connection.execute(
                """
                select resource.storage_key
                from video.video_resources as link
                join video.resources as resource
                  on resource.id = link.resource_id
                 and resource.owner_id = link.owner_id
                where link.owner_id = %s and link.video_id = %s
                  and link.resource_id = %s
                  and resource.resource_kind = 'pdf'
                  and resource.storage_key is not null
                """,
                (owner_id, video_id, resource_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            return (
                FilesystemMediaStore()
                .open_path(owner_id=owner_id, storage_key=row["storage_key"])
                .read_bytes()
            )
        except (MediaStoreError, OSError) as error:
            raise HTTPException(
                status_code=404, detail="resource content not found"
            ) from error

    payload = await run_in_threadpool(load)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@chat_router.get("/api/videos/{video_id}/frames/{frame_id}/image")
async def frame_image(
    video_id: UUID,
    frame_id: int,
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    """Serve one frame preview, owner-scoped in the query itself."""

    def load() -> bytes:
        with database_connection(readonly=True) as connection:
            row = connection.execute(
                """
                select preview_storage_key from video.frames
                where id = %s and video_id = %s and owner_id = %s
                """,
                (frame_id, video_id, owner_id),
            ).fetchone()
        if row is None:
            raise IMAGE_NOT_FOUND
        try:
            path = FilesystemMediaStore().open_path(
                owner_id=owner_id, storage_key=row["preview_storage_key"]
            )
            payload = path.read_bytes()
        except (MediaStoreError, OSError) as error:
            raise IMAGE_NOT_FOUND from error
        if len(payload) > MAXIMUM_IMAGE_BYTES:
            logger.warning("Frame %s exceeds the response ceiling", frame_id)
            raise IMAGE_NOT_FOUND
        return payload

    payload = await run_in_threadpool(load)
    etag = f'"{hashlib.sha256(payload).hexdigest()[:32]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": IMAGE_CACHE_CONTROL},
        )
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={"ETag": etag, "Cache-Control": IMAGE_CACHE_CONTROL},
    )


def _summary(row: dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=row["id"],
        video_id=row["video_id"],
        video_title=row["video_title"],
        title=row["title"],
        turn_count=int(row["turn_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
