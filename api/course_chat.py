"""Grounded multi-lecture conversations for video courses."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from storage.database import connection as database_connection
from study.contracts import ContractModel
from video.answers import VideoAnswerDependencies
from video.conversation_store import PLACEHOLDER_TITLE, VideoTurnCostExceeded
from video.course_contracts import CourseConversationState, CourseTurnResult
from video.course_conversation import (
    execute_course_turn,
    new_course_conversation_state,
)
from video.course_conversation_store import (
    CourseConversationNotFoundError,
    append_turn,
    create_conversation,
    delete_conversation,
    list_conversations,
    load_conversation,
    load_turns,
    rename_conversation,
)
from video.course_repository import course_member_ids, load_course
from video.embeddings import OpenRouterRegionEmbedder, OpenRouterTextEmbedder
from video.media_store import MediaStoreError, configured_media_store
from video.models import VideoModelError


router = APIRouter(tags=["course-chat"])
logger = logging.getLogger("study_partner.api.course_chat")
CONVERSATION_NOT_FOUND = HTTPException(
    status_code=404, detail="course conversation not found"
)


def _course_answer_dependencies() -> VideoAnswerDependencies:
    try:
        text_embedder = OpenRouterTextEmbedder(dimension=768)
        image_embedder = OpenRouterRegionEmbedder()
    except ValueError:
        logger.warning("Course answers running without semantic retrieval")
        text_embedder = image_embedder = None
    try:
        store = configured_media_store()
    except MediaStoreError:
        logger.warning("Course answers running without frame images")
        store = None
    return VideoAnswerDependencies(
        media_store=store,
        text_embedder=text_embedder,
        image_embedder=image_embedder,
    )
COURSE_NOT_FOUND = HTTPException(status_code=404, detail="course not found")
HEARTBEAT_SECONDS = 15
_STREAM_DONE = object()


class CreateConversationRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    video_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)


class ConversationSummary(ContractModel):
    conversation_id: UUID
    course_id: UUID
    title: str
    selected_video_ids: list[UUID]
    turn_count: int
    created_at: Any
    updated_at: Any


class ConversationListResponse(ContractModel):
    conversations: list[ConversationSummary]


class TurnView(ContractModel):
    turn_index: int
    question: str
    answer: str | None
    result: CourseTurnResult | None
    cost_usd: float
    trace_id: str | None
    created_at: Any


class ConversationDetail(ContractModel):
    conversation_id: UUID
    course_id: UUID
    title: str
    selected_video_ids: list[UUID]
    turns: list[TurnView]


class AskRequest(ContractModel):
    question: str = Field(min_length=1, max_length=2_000)


class AskResponse(ContractModel):
    conversation_id: UUID
    result: CourseTurnResult
    turn_index: int


class RenameRequest(ContractModel):
    title: str = Field(min_length=1, max_length=200)


def _summary(row: dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=row["id"],
        course_id=row["course_id"],
        title=row["title"],
        selected_video_ids=list(row["selected_video_ids"]),
        turn_count=int(row.get("turn_count") or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post(
    "/api/courses/{course_id}/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def start_conversation(
    course_id: UUID,
    request: CreateConversationRequest,
    owner_id: UUID = Depends(current_owner),
) -> ConversationSummary:
    def create():
        with database_connection() as connection:
            state = new_course_conversation_state(course_id=course_id)
            record = create_conversation(
                connection,
                owner_id=owner_id,
                course_id=course_id,
                title=request.title or PLACEHOLDER_TITLE,
                selected_video_ids=request.video_ids,
                prompt_snapshot={"prompt_version": "course-answer-v1"},
                state=state.model_dump(mode="json"),
                conversation_id=state.conversation_id,
            )
            return {**record, "turn_count": 0}

    try:
        return _summary(await run_in_threadpool(create))
    except CourseConversationNotFoundError as error:
        raise COURSE_NOT_FOUND from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/api/courses/{course_id}/conversations",
    response_model=ConversationListResponse,
)
async def course_conversations(
    course_id: UUID, owner_id: UUID = Depends(current_owner)
) -> ConversationListResponse:
    def load():
        with database_connection(readonly=True) as connection:
            if load_course(connection, course_id, owner_id=owner_id) is None:
                raise COURSE_NOT_FOUND
            return list_conversations(
                connection, owner_id=owner_id, course_id=course_id
            )

    return ConversationListResponse(
        conversations=[_summary(row) for row in await run_in_threadpool(load)]
    )


@router.get(
    "/api/course-conversations/{conversation_id}",
    response_model=ConversationDetail,
)
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
            turns = load_turns(connection, conversation_id, owner_id=owner_id)
            return record, turns

    record, turns = await run_in_threadpool(load)
    return ConversationDetail(
        conversation_id=record["id"],
        course_id=record["course_id"],
        title=record["title"],
        selected_video_ids=list(record["selected_video_ids"]),
        turns=[
            TurnView(
                turn_index=row["turn_index"],
                question=row["question"],
                answer=row["answer"],
                result=(
                    CourseTurnResult.model_validate(row["result_json"])
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


@router.patch(
    "/api/course-conversations/{conversation_id}",
    response_model=ConversationSummary,
)
async def patch_conversation(
    conversation_id: UUID,
    request: RenameRequest,
    owner_id: UUID = Depends(current_owner),
) -> ConversationSummary:
    def rename():
        with database_connection() as connection:
            row = rename_conversation(
                connection,
                conversation_id,
                owner_id=owner_id,
                title=request.title,
            )
            if row is None:
                raise CONVERSATION_NOT_FOUND
            count = len(load_turns(connection, conversation_id, owner_id=owner_id))
            return {**row, "turn_count": count}

    return _summary(await run_in_threadpool(rename))


@router.delete(
    "/api/course-conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_conversation(
    conversation_id: UUID, owner_id: UUID = Depends(current_owner)
) -> Response:
    def remove():
        with database_connection() as connection:
            return delete_conversation(
                connection, conversation_id, owner_id=owner_id
            )

    if not await run_in_threadpool(remove):
        raise CONVERSATION_NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _run_turn(
    owner_id: UUID,
    conversation_id: UUID,
    question: str,
    token_callback=None,
) -> AskResponse:
    with database_connection() as connection:
        record = load_conversation(connection, conversation_id, owner_id=owner_id)
        if record is None:
            raise CONVERSATION_NOT_FOUND
        course = load_course(connection, record["course_id"], owner_id=owner_id)
        if course is None:
            raise COURSE_NOT_FOUND
        members = set(
            course_member_ids(connection, record["course_id"], owner_id=owner_id)
        )
        selected = [
            item for item in record["selected_video_ids"] if item in members
        ]
        if not selected:
            raise HTTPException(
                status_code=422,
                detail="this conversation has no lectures that remain in the course",
            )
        state = (
            CourseConversationState.model_validate(record["state_json"])
            if record["state_json"]
            else new_course_conversation_state(
                course_id=record["course_id"], conversation_id=conversation_id
            )
        )
        result, updated, versions = execute_course_turn(
            connection,
            question,
            state,
            owner_id=owner_id,
            course_id=record["course_id"],
            course_title=course["title"],
            video_ids=selected,
            dependencies=_course_answer_dependencies(),
            token_callback=token_callback,
        )
        turn_index = append_turn(
            connection,
            conversation_id,
            owner_id=owner_id,
            course_id=record["course_id"],
            versions=versions,
            question=result.question,
            rewritten_query=result.standalone_query or result.question,
            answer=result.answer,
            result=result.model_dump(mode="json"),
            state=updated.model_dump(mode="json"),
            cost_usd=result.cost_usd,
            trace_id=result.trace_id,
        )
    return AskResponse(
        conversation_id=conversation_id, result=result, turn_index=turn_index
    )


REJECTED_ERRORS = (VideoModelError, VideoTurnCostExceeded, ValueError)


@router.post(
    "/api/course-conversations/{conversation_id}/turns",
    response_model=AskResponse,
)
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
    except REJECTED_ERRORS as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _stream(
    execute: Callable[[Callable[[str, str], None]], AskResponse],
) -> StreamingResponse:
    loop = asyncio.get_running_loop()
    events: queue.Queue = queue.Queue()

    def run():
        try:
            events.put(("final", execute(lambda kind, text: events.put((kind, text))).model_dump_json()))
        except HTTPException as error:
            events.put(("error", json.dumps({"detail": error.detail})))
        except REJECTED_ERRORS as error:
            events.put(("error", json.dumps({"detail": str(error)})))
        except Exception:
            logger.exception("Unhandled error in a course turn")
            events.put(("error", json.dumps({"detail": "internal error"})))
        finally:
            events.put(_STREAM_DONE)

    threading.Thread(target=run, daemon=True).start()

    async def body():
        while True:
            try:
                item = await loop.run_in_executor(
                    None, lambda: events.get(timeout=HEARTBEAT_SECONDS)
                )
            except queue.Empty:
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
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/course-conversations/{conversation_id}/turns/stream")
async def ask_stream(
    conversation_id: UUID,
    request: AskRequest,
    owner_id: UUID = Depends(current_owner),
) -> StreamingResponse:
    question = request.question.strip()
    return _stream(
        lambda callback: _run_turn(
            owner_id, conversation_id, question, token_callback=callback
        )
    )
