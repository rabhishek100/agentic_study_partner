"""FastAPI boundary over the existing conversational study workflow."""

# Environment must be loaded before project modules evaluate model defaults.
# ruff: noqa: E402

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from starlette.concurrency import run_in_threadpool

load_dotenv()

from api.auth import current_owner
from api.version import build_revision, build_time
from api.ingestions import router as ingestion_router
from api.video_chat import chat_router as video_chat_router
from api.videos import jobs_router as video_ingestion_router
from api.videos import videos_router
from ingestion.errors import IngestionError
from ingestion.storage_objects import signed_object_url
from retrieval.langchain import warm_models
from storage.conversations import (
    append_turn,
    create_conversation,
    delete_conversation,
    derive_title,
    list_conversations,
    list_side_chats,
    load_conversation,
    load_turns,
    set_conversation_state,
    update_conversation,
)
from storage.database import (
    book_retrieval_completeness,
    close_pools,
    database_readiness,
)
from storage.database import (
    connection as database_connection,
)
from storage.postgres import list_books, ready_book
from storage.preferences import load_prompt_profile, save_prompt_profile
from study.analyze import ConversationDecisionError
from study.contracts import (
    AnswerArchetype,
    ContractModel,
    ConversationState,
    PromptProfile,
    QuoteAnchor,
    ResponseDepth,
    TurnResult,
)
from study.conversation import execute_conversation_turn, new_conversation_state
from study.side_context import ParentTurn, build_side_context, readable_quote
from study.prompts import (
    DEFAULT_PROMPT_PROFILE,
    LOCKED_GROUNDING_PROMPT,
    profile_version,
    prompt_preview,
)
from study.query import QueryExecutionError
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("study_partner.api")


RetrievalMode = Literal["bm25", "vector", "hybrid", "hybrid_rerank"]


class ChatRequest(ContractModel):
    question: str = Field(min_length=1, max_length=10_000)
    # Chosen from the frozen gold-set comparison in
    # evaluation/retrieval_comparison_artifact.json, where reranking wins
    # on every metric: Recall@5 1.00 against 0.93 for hybrid alone, and
    # MRR@5 0.96 against 0.85. This is the product default; the library
    # functions keep "hybrid" so evaluations state their mode explicitly.
    retrieval_mode: RetrievalMode = "hybrid_rerank"
    # Required and always verified against the caller's ready books. There is
    # deliberately no default and an empty list is rejected: a chat turn must
    # never fall back to book 1, and it must never silently widen to the whole
    # library either. The client sends the reader's selection explicitly.
    book_ids: list[int] = Field(min_length=1, max_length=50)
    # Book ids explicitly tagged with @ in this turn. These narrow retrieval
    # for the turn without rewriting the conversation's default selection.
    mentioned_book_ids: list[int] = Field(default_factory=list, max_length=50)
    # Null starts a new conversation. Conversation state is loaded from and
    # written to the database by the server; it is deliberately no longer
    # accepted from the client, which previously held the only copy and could
    # submit arbitrary state.
    conversation_id: UUID | None = None
    response_depth: ResponseDepth = "interview"

    @field_validator("book_ids", "mentioned_book_ids")
    @classmethod
    def unique_positive_book_ids(cls, value: list[int]) -> list[int]:
        if any(identifier <= 0 for identifier in value):
            raise ValueError("book_ids must be positive")
        return sorted(set(value))


class ChatResponse(ContractModel):
    result: TurnResult
    state: ConversationState


class ConversationSummary(ContractModel):
    conversation_id: UUID
    title: str
    book_ids: list[int]
    retrieval_mode: RetrievalMode
    turn_count: int
    created_at: datetime
    updated_at: datetime
    # Side chats opened over this conversation. They are counted rather than
    # listed alongside it: a side chat belongs to a passage of one
    # conversation, not to the library.
    side_thread_count: int = 0


class ConversationListResponse(ContractModel):
    conversations: list[ConversationSummary]


class ConversationTurn(ContractModel):
    turn_index: int
    question: str
    answer: str
    result: TurnResult
    created_at: datetime


class ConversationDetail(ContractModel):
    conversation_id: UUID
    title: str
    book_ids: list[int]
    retrieval_mode: RetrievalMode
    created_at: datetime
    updated_at: datetime
    prompt_profile: PromptProfile
    turns: list[ConversationTurn]
    # Set when this conversation is a side chat, along with the passages it was
    # opened over. Resuming a side chat uses this endpoint like any other.
    parent_conversation_id: UUID | None = None
    anchors: list[QuoteAnchor] = Field(default_factory=list)


class UpdateConversationRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    retrieval_mode: RetrievalMode | None = None
    prompt_profile: PromptProfile | None = None


class SideChatAnchorInput(ContractModel):
    """One passage a reader carried into a side chat.

    `anchor_id` is echoed back when an existing chip is being kept, and omitted
    for a new one; the server assigns ids it has not seen so two chips can
    never share one.
    """

    anchor_id: str | None = Field(default=None, min_length=1, max_length=64)
    parent_turn_index: int = Field(ge=0)
    quoted_text: str = Field(min_length=1, max_length=4_000)


# More than a handful of references in one small window stops being a focused
# question, and every anchor pins evidence that competes with retrieval for the
# same context budget.
MAXIMUM_ANCHORS = 5


class CreateSideChatRequest(ContractModel):
    anchors: list[SideChatAnchorInput] = Field(min_length=1, max_length=MAXIMUM_ANCHORS)
    title: str | None = Field(default=None, min_length=1, max_length=200)


class UpdateSideChatRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    anchors: list[SideChatAnchorInput] | None = Field(
        default=None,
        max_length=MAXIMUM_ANCHORS,
    )


class SideChatSummary(ContractModel):
    conversation_id: UUID
    parent_conversation_id: UUID
    title: str
    book_ids: list[int]
    retrieval_mode: RetrievalMode
    anchors: list[QuoteAnchor]
    turn_count: int
    created_at: datetime
    updated_at: datetime


class SideChatListResponse(ContractModel):
    side_chats: list[SideChatSummary]


class SideChatTurnRequest(ContractModel):
    question: str = Field(min_length=1, max_length=10_000)
    # A side question is a clarification, and a long answer in a small window
    # scrolls badly. The reader can still ask for more depth per window.
    response_depth: ResponseDepth = "quick"


class PromptSettingsResponse(ContractModel):
    profile: PromptProfile
    defaults: PromptProfile
    locked_system_prompt: str
    preview_system_prompt: str
    preview_user_prompt: str
    profile_version: str


class UpdatePromptSettingsRequest(ContractModel):
    profile: PromptProfile


class PromptPreviewRequest(ContractModel):
    profile: PromptProfile
    answer_archetype: AnswerArchetype = "concept_explanation"
    response_depth: ResponseDepth = "interview"


class BookSummary(ContractModel):
    book_id: int
    title: str
    author: str | None
    page_count: int | None
    ready_at: datetime | None
    chunk_count: int
    embedding_count: int
    retrieval_complete: bool


class BookListResponse(ContractModel):
    books: list[BookSummary]


class HealthResponse(ContractModel):
    status: Literal["ok", "unavailable"]
    canonical_database_ready: bool
    retrieval_database_ready: bool
    # What code is actually answering. Deployment drift is otherwise invisible:
    # the worker once ran five commits behind for hours, and finding out meant
    # comparing a deployment timestamp against a git log by eye.
    build_revision: str
    build_time: str


class QueueHealthResponse(ContractModel):
    """Aggregate ingestion-queue state. Carries no per-user information."""

    queued_jobs: int
    processing_jobs: int
    retry_scheduled_jobs: int
    failed_jobs_last_day: int
    oldest_queued_seconds: float | None
    worker_heartbeat_seconds: float | None
    expired_leases: int


BOOK_NOT_FOUND = HTTPException(status_code=404, detail="book not found")


def _require_ready_books(owner_id: UUID, book_ids: list[int]) -> None:
    """Reject anything that is not this owner's verified, ready book.

    Missing, someone else's, and still-processing books all return the same
    404 so book IDs cannot be probed. Every id in a multi-book selection is
    checked: one unreadable book fails the turn rather than being dropped from
    the scope, which would answer a narrower question than the one asked.
    """

    with database_connection(readonly=True) as connection:
        for book_id in book_ids:
            if ready_book(connection, book_id, owner_id=owner_id) is None:
                raise BOOK_NOT_FOUND


def _allowed_origins() -> list[str]:
    configured = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app = FastAPI(
    title="Agentic Study Partner API",
    version="0.1.0",
    description="Grounded book questions and complete-scope summaries.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Idempotency-Key",
        "X-Caption-Filename",
    ],
)
app.include_router(ingestion_router)
app.include_router(videos_router)
app.include_router(video_ingestion_router)
app.include_router(video_chat_router)


@app.on_event("startup")
async def _warm_retrieval_models() -> None:
    logger.info("Initializing the hosted embedding client")
    await run_in_threadpool(warm_models)
    logger.info("Hosted embedding client ready; reranker remains on demand")


@app.on_event("shutdown")
async def _close_database_pools() -> None:
    close_pools()


@app.get("/api/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    canonical_ready, retrieval_ready = await run_in_threadpool(database_readiness)
    if not (canonical_ready and retrieval_ready):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=("ok" if canonical_ready and retrieval_ready else "unavailable"),
        canonical_database_ready=canonical_ready,
        retrieval_database_ready=retrieval_ready,
        build_revision=build_revision(),
        build_time=build_time(),
    )


@app.get("/api/health/queue", response_model=QueueHealthResponse)
async def queue_health() -> QueueHealthResponse:
    """Ingestion queue depth, age, and worker liveness.

    Separate from /api/health on purpose: a deep queue or a quiet worker is
    an operational signal, not a reason for the API to report itself down.
    """

    def load() -> QueueHealthResponse:
        with database_connection(readonly=True) as connection:
            row = connection.execute(
                """
                select
                    count(*) filter (where status = 'queued') as queued,
                    count(*) filter (where status in (
                        'validating', 'parsing', 'persisting', 'chunking',
                        'embedding', 'verifying'
                    )) as processing,
                    count(*) filter (where status = 'retry_scheduled')
                        as retry_scheduled,
                    count(*) filter (
                        where status = 'failed'
                          and completed_at > now() - interval '1 day'
                    ) as failed_last_day,
                    extract(epoch from now() - min(created_at) filter (
                        where status = 'queued'
                    )) as oldest_queued_seconds,
                    extract(epoch from now() - max(heartbeat_at)) as heartbeat_age,
                    count(*) filter (
                        where lease_expires_at is not null
                          and lease_expires_at < now()
                    ) as expired_leases
                from ingestion_jobs
                """
            ).fetchone()
        return QueueHealthResponse(
            queued_jobs=row["queued"],
            processing_jobs=row["processing"],
            retry_scheduled_jobs=row["retry_scheduled"],
            failed_jobs_last_day=row["failed_last_day"],
            oldest_queued_seconds=row["oldest_queued_seconds"],
            worker_heartbeat_seconds=row["heartbeat_age"],
            expired_leases=row["expired_leases"],
        )

    return await run_in_threadpool(load)


@app.get("/api/books", response_model=BookListResponse)
async def books(owner_id: UUID = Depends(current_owner)) -> BookListResponse:
    """List the caller's ready books. Processing books are not selectable."""

    def load() -> list[BookSummary]:
        with database_connection(readonly=True) as connection:
            rows = list_books(connection, owner_id=owner_id)
            summaries = []
            for row in rows:
                chunks, embeddings = book_retrieval_completeness(
                    connection, owner_id=owner_id, book_id=row["id"]
                )
                summaries.append(
                    BookSummary(
                        book_id=row["id"],
                        title=row["title"],
                        author=row["author"],
                        page_count=row["page_count"],
                        ready_at=row["ready_at"],
                        chunk_count=chunks,
                        embedding_count=embeddings,
                        retrieval_complete=chunks > 0 and chunks == embeddings,
                    )
                )
            return summaries

    return BookListResponse(books=await run_in_threadpool(load))


CONVERSATION_NOT_FOUND = HTTPException(
    status_code=404,
    detail="conversation not found",
)


def _stored_profile(value) -> PromptProfile:
    if value:
        return PromptProfile.model_validate(value)
    return DEFAULT_PROMPT_PROFILE


def _resume_state(
    owner_id: UUID,
    request: ChatRequest,
) -> tuple[UUID, ConversationState, PromptProfile]:
    """Return the conversation to continue, creating one when needed.

    A selection that differs from the conversation's own starts a new
    conversation rather than re-scoping an existing one: answers already
    recorded were grounded in the old selection and the new scope may not be
    able to reproduce them.
    """

    with database_connection() as connection:
        if request.conversation_id is not None:
            existing = load_conversation(
                connection,
                request.conversation_id,
                owner_id=owner_id,
            )
            if existing is None:
                raise CONVERSATION_NOT_FOUND
            if existing["parent_conversation_id"] is not None:
                # A side chat's turns must carry its anchors, and this endpoint
                # has none to give. Continuing here would answer the reader's
                # question with the priority context silently missing.
                raise HTTPException(
                    status_code=422,
                    detail="use the side-chat turn endpoint for a side chat",
                )
            if list(existing["book_ids"]) == request.book_ids:
                state = ConversationState.model_validate(existing["state_json"])
                if existing["retrieval_mode"] != request.retrieval_mode:
                    update_conversation(
                        connection,
                        existing["id"],
                        owner_id=owner_id,
                        retrieval_mode=request.retrieval_mode,
                    )
                return (
                    existing["id"],
                    state,
                    _stored_profile(existing["prompt_profile_json"]),
                )

        saved_profile = load_prompt_profile(connection, owner_id=owner_id)
        prompt_profile = _stored_profile(saved_profile)

        created = create_conversation(
            connection,
            owner_id=owner_id,
            book_ids=request.book_ids,
            retrieval_mode=request.retrieval_mode,
            title=derive_title(request.question),
            prompt_profile=prompt_profile.model_dump(mode="json"),
        )

    return (
        created["id"],
        new_conversation_state(
            book_ids=request.book_ids,
            conversation_id=str(created["id"]),
        ),
        prompt_profile,
    )


def _persist_turn(
    owner_id: UUID,
    conversation_id: UUID,
    question: str,
    result: TurnResult,
    state: ConversationState,
) -> None:
    with database_connection() as connection:
        append_turn(
            connection,
            conversation_id,
            owner_id=owner_id,
            question=question,
            answer=result.answer,
            result=result.model_dump(mode="json"),
            state=state.model_dump(mode="json"),
        )


def _run_turn(
    owner_id: UUID,
    request: ChatRequest,
    token_callback=None,
) -> ChatResponse:
    """Load, execute, and persist one turn. Runs on a worker thread."""

    question = request.question.strip()
    conversation_id, state, prompt_profile = _resume_state(owner_id, request)
    result, updated = execute_conversation_turn(
        question,
        state,
        owner_id=owner_id,
        retrieval_mode=request.retrieval_mode,
        book_ids=request.book_ids,
        turn_book_ids=request.mentioned_book_ids or None,
        token_callback=token_callback,
        prompt_profile=prompt_profile,
        response_depth=request.response_depth,
    )
    # The stored conversation is the identity; a fresh state object from the
    # workflow must not invent a different one.
    updated = updated.model_copy(update={"conversation_id": str(conversation_id)})
    _persist_turn(owner_id, conversation_id, question, result, updated)
    return ChatResponse(result=result, state=updated)


SIDE_CHAT_NOT_FOUND = HTTPException(status_code=404, detail="side chat not found")


def _assigned_anchors(
    requested: list[SideChatAnchorInput],
    *,
    existing: list[QuoteAnchor],
    turn_indexes: set[int],
) -> list[QuoteAnchor]:
    """Validate anchors against the parent's turns and give each a unique id.

    An anchor naming a turn the parent does not have is rejected rather than
    dropped: it means the client and the server disagree about the
    conversation, and silently answering with less context than the reader
    highlighted is the wrong way to find that out.
    """

    known = {anchor.anchor_id for anchor in existing}
    used: set[str] = set()
    anchors: list[QuoteAnchor] = []
    for item in requested:
        if item.parent_turn_index not in turn_indexes:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"turn {item.parent_turn_index} is not part of the parent "
                    "conversation"
                ),
            )
        anchor_id = item.anchor_id
        if anchor_id is None or anchor_id not in known or anchor_id in used:
            anchor_id = uuid4().hex
        used.add(anchor_id)
        anchors.append(
            QuoteAnchor(
                anchor_id=anchor_id,
                parent_turn_index=item.parent_turn_index,
                quoted_text=item.quoted_text.strip(),
            )
        )
    return anchors


def _side_chat_summary(record: dict, turn_count: int) -> SideChatSummary:
    return SideChatSummary(
        conversation_id=record["id"],
        parent_conversation_id=record["parent_conversation_id"],
        title=record["title"],
        book_ids=list(record["book_ids"]),
        retrieval_mode=record["retrieval_mode"],
        anchors=[
            QuoteAnchor.model_validate(anchor)
            for anchor in record["anchors_json"] or []
        ],
        turn_count=turn_count,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


def _seeded_side_chat_state(
    conversation_id: UUID,
    *,
    book_ids: list[int],
    anchored_turn: dict,
) -> ConversationState:
    """Open a side chat already knowing the answer it was opened over.

    In a side chat, "the previous answer" is the one being asked about, so the
    anchored turn's answer, evidence, citations and scope are seeded here. That
    makes "shorten this" or "reword that as bullets" work on the first turn
    through the existing `prior_answer_transform` route, instead of only after
    the side chat has produced an answer of its own.
    """

    state = new_conversation_state(
        book_ids=book_ids,
        conversation_id=str(conversation_id),
    )
    result = TurnResult.model_validate(anchored_turn["result_json"])
    state.previous_answer = anchored_turn["answer"]
    state.previous_evidence = list(result.evidence)
    state.previous_citations = list(result.citations)
    state.previous_route = result.route
    state.active_scope = result.resolved_scope
    return state


def _side_chat_state(record: dict) -> ConversationState:
    """The stored state to continue, corrected to this conversation's identity."""

    stored = record["state_json"] or {}
    if not stored:
        return new_conversation_state(
            book_ids=list(record["book_ids"]),
            conversation_id=str(record["id"]),
        )
    return ConversationState.model_validate(stored).model_copy(
        update={"conversation_id": str(record["id"])}
    )


def _create_side_chat(
    owner_id: UUID,
    parent_conversation_id: UUID,
    request: CreateSideChatRequest,
) -> SideChatSummary:
    with database_connection() as connection:
        parent = load_conversation(
            connection,
            parent_conversation_id,
            owner_id=owner_id,
        )
        if parent is None:
            raise CONVERSATION_NOT_FOUND
        if parent["parent_conversation_id"] is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "side chats cannot be nested; open one over the main "
                    "conversation instead"
                ),
            )
        turns = {
            turn["turn_index"]: turn
            for turn in load_turns(
                connection,
                parent_conversation_id,
                owner_id=owner_id,
            )
        }
        anchors = _assigned_anchors(
            request.anchors,
            existing=[],
            turn_indexes=set(turns),
        )
        anchored_turn = turns[anchors[0].parent_turn_index]
        # A selection can be nothing but a citation marker, which names the
        # thread badly; the question it came from is the better fallback.
        title = (
            request.title
            or readable_quote(anchors[0].quoted_text)
            or anchored_turn["question"]
        )
        created = create_conversation(
            connection,
            owner_id=owner_id,
            # Inherited and then frozen. The parent's selection may change
            # later — that starts a new conversation there — and an open side
            # chat must not be regrounded underneath the reader.
            book_ids=list(parent["book_ids"]),
            retrieval_mode=parent["retrieval_mode"],
            title=derive_title(title),
            prompt_profile=parent["prompt_profile_json"] or None,
            parent_conversation_id=parent_conversation_id,
            anchors=[anchor.model_dump(mode="json") for anchor in anchors],
        )
        state = _seeded_side_chat_state(
            created["id"],
            book_ids=list(parent["book_ids"]),
            anchored_turn=anchored_turn,
        )
        set_conversation_state(
            connection,
            created["id"],
            owner_id=owner_id,
            state=state.model_dump(mode="json"),
        )
    return _side_chat_summary(created, turn_count=0)


def _run_side_turn(
    owner_id: UUID,
    side_chat_id: UUID,
    request: SideChatTurnRequest,
    token_callback=None,
) -> ChatResponse:
    """Execute and persist one side-chat turn. Runs on a worker thread."""

    question = request.question.strip()
    with database_connection(readonly=True) as connection:
        record = load_conversation(connection, side_chat_id, owner_id=owner_id)
        if record is None or record["parent_conversation_id"] is None:
            raise SIDE_CHAT_NOT_FOUND
        anchors = [
            QuoteAnchor.model_validate(anchor)
            for anchor in record["anchors_json"] or []
        ]
        parent_turns = [
            ParentTurn.from_result(
                turn["turn_index"],
                turn["question"],
                turn["answer"],
                TurnResult.model_validate(turn["result_json"]),
            )
            for turn in load_turns(
                connection,
                record["parent_conversation_id"],
                owner_id=owner_id,
            )
        ]

    book_ids = list(record["book_ids"])
    _require_ready_books(owner_id, book_ids)
    result, updated = execute_conversation_turn(
        question,
        _side_chat_state(record),
        owner_id=owner_id,
        retrieval_mode=record["retrieval_mode"],
        book_ids=book_ids,
        token_callback=token_callback,
        prompt_profile=_stored_profile(record["prompt_profile_json"]),
        response_depth=request.response_depth,
        side_context=build_side_context(anchors, parent_turns),
    )
    updated = updated.model_copy(update={"conversation_id": str(side_chat_id)})
    _persist_turn(owner_id, side_chat_id, question, result, updated)
    return ChatResponse(result=result, state=updated)


@app.post(
    "/api/conversations/{conversation_id}/side-chats",
    response_model=SideChatSummary,
    status_code=201,
)
async def open_side_chat(
    conversation_id: UUID,
    request: CreateSideChatRequest,
    owner_id: UUID = Depends(current_owner),
) -> SideChatSummary:
    """Open a side chat over one or more passages of a conversation."""

    return await run_in_threadpool(
        _create_side_chat,
        owner_id,
        conversation_id,
        request,
    )


@app.get(
    "/api/conversations/{conversation_id}/side-chats",
    response_model=SideChatListResponse,
)
async def side_chats(
    conversation_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> SideChatListResponse:
    """List the side chats opened over one conversation, most recent first."""

    def load() -> list[SideChatSummary]:
        with database_connection(readonly=True) as connection:
            if load_conversation(connection, conversation_id, owner_id=owner_id) is None:
                raise CONVERSATION_NOT_FOUND
            return [
                _side_chat_summary(row, turn_count=row["turn_count"])
                for row in list_side_chats(
                    connection,
                    conversation_id,
                    owner_id=owner_id,
                )
            ]

    return SideChatListResponse(side_chats=await run_in_threadpool(load))


@app.patch("/api/side-chats/{side_chat_id}", response_model=SideChatSummary)
async def update_side_chat(
    side_chat_id: UUID,
    request: UpdateSideChatRequest,
    owner_id: UUID = Depends(current_owner),
) -> SideChatSummary:
    """Rename a side chat, or replace the passages it is anchored to."""

    def apply() -> SideChatSummary:
        with database_connection() as connection:
            record = load_conversation(connection, side_chat_id, owner_id=owner_id)
            if record is None or record["parent_conversation_id"] is None:
                raise SIDE_CHAT_NOT_FOUND
            anchors = None
            if request.anchors is not None:
                turn_indexes = {
                    turn["turn_index"]
                    for turn in load_turns(
                        connection,
                        record["parent_conversation_id"],
                        owner_id=owner_id,
                    )
                }
                anchors = [
                    anchor.model_dump(mode="json")
                    for anchor in _assigned_anchors(
                        request.anchors,
                        existing=[
                            QuoteAnchor.model_validate(anchor)
                            for anchor in record["anchors_json"] or []
                        ],
                        turn_indexes=turn_indexes,
                    )
                ]
            updated = update_conversation(
                connection,
                side_chat_id,
                owner_id=owner_id,
                title=request.title.strip() if request.title else None,
                anchors=anchors,
            )
            if updated is None:
                raise SIDE_CHAT_NOT_FOUND
            turn_count = connection.execute(
                """
                select count(*) as turn_count from conversation_turns
                where conversation_id = %s and owner_id = %s
                """,
                (side_chat_id, owner_id),
            ).fetchone()["turn_count"]
        return _side_chat_summary(updated, turn_count=turn_count)

    return await run_in_threadpool(apply)


@app.post("/api/side-chats/{side_chat_id}/turns/stream")
async def side_chat_turn_stream(
    side_chat_id: UUID,
    request: SideChatTurnRequest,
    owner_id: UUID = Depends(current_owner),
) -> StreamingResponse:
    """Stream one side-chat answer, framed exactly like a main chat turn.

    The books, retrieval mode and prompt profile all come from the stored side
    chat rather than from the request: they were inherited from the parent at
    creation and are deliberately not the client's to change per turn.
    """

    return _streamed_turn(
        lambda on_token: _run_side_turn(
            owner_id,
            side_chat_id,
            request,
            token_callback=on_token,
        )
    )


def _prompt_settings(profile: PromptProfile) -> PromptSettingsResponse:
    preview = prompt_preview(profile)
    return PromptSettingsResponse(
        profile=profile,
        defaults=DEFAULT_PROMPT_PROFILE,
        locked_system_prompt=LOCKED_GROUNDING_PROMPT,
        preview_system_prompt=preview[0][1],
        preview_user_prompt=preview[1][1],
        profile_version=profile_version(profile),
    )


@app.get("/api/prompt-settings", response_model=PromptSettingsResponse)
async def prompt_settings(
    owner_id: UUID = Depends(current_owner),
) -> PromptSettingsResponse:
    def load() -> PromptSettingsResponse:
        with database_connection(readonly=True) as connection:
            stored = load_prompt_profile(connection, owner_id=owner_id)
        return _prompt_settings(_stored_profile(stored))

    return await run_in_threadpool(load)


@app.patch("/api/prompt-settings", response_model=PromptSettingsResponse)
async def update_prompt_settings(
    request: UpdatePromptSettingsRequest,
    owner_id: UUID = Depends(current_owner),
) -> PromptSettingsResponse:
    def save() -> PromptSettingsResponse:
        with database_connection() as connection:
            stored = save_prompt_profile(
                connection,
                owner_id=owner_id,
                profile=request.profile.model_dump(mode="json"),
            )
        return _prompt_settings(PromptProfile.model_validate(stored))

    return await run_in_threadpool(save)


@app.post("/api/prompt-settings/preview", response_model=PromptSettingsResponse)
async def preview_prompt_settings(
    request: PromptPreviewRequest,
    owner_id: UUID = Depends(current_owner),
) -> PromptSettingsResponse:
    del owner_id
    preview = prompt_preview(
        request.profile,
        archetype=request.answer_archetype,
        depth=request.response_depth,
    )
    return PromptSettingsResponse(
        profile=request.profile,
        defaults=DEFAULT_PROMPT_PROFILE,
        locked_system_prompt=LOCKED_GROUNDING_PROMPT,
        preview_system_prompt=preview[0][1],
        preview_user_prompt=preview[1][1],
        profile_version=profile_version(request.profile),
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    owner_id: UUID = Depends(current_owner),
) -> ChatResponse:
    await run_in_threadpool(
        _require_ready_books,
        owner_id,
        sorted(set(request.book_ids + request.mentioned_book_ids)),
    )
    try:
        return await run_in_threadpool(_run_turn, owner_id, request)
    except HTTPException:
        raise
    except (
        ContextWindowExceededError,
        QueryExecutionError,
        ScopeResolutionError,
        ConversationDecisionError,
    ) as error:
        logger.warning("Chat turn rejected: %s", error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        logger.exception("Unhandled error while executing chat turn")
        raise


_REJECTED_TURN_ERRORS = (
    ContextWindowExceededError,
    QueryExecutionError,
    ScopeResolutionError,
    ConversationDecisionError,
)
_STREAM_DONE = object()
_HEARTBEAT_INTERVAL_SECONDS = 15


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _streamed_turn(
    execute: Callable[[Callable[[str, str], None]], ChatResponse],
) -> StreamingResponse:
    """Run one turn on a worker thread and stream its tokens as SSE.

    Shared by the main chat and by side chats: `execute` receives the token
    callback and returns the response to send as `final`. Keeping one
    implementation means the framing, the heartbeat, and the mapping from a
    rejected turn to an `error` event cannot drift apart between the two.
    """

    loop = asyncio.get_running_loop()
    events: queue.Queue = queue.Queue()

    def on_token(kind: str, text: str) -> None:
        events.put((kind, text))

    def run() -> None:
        try:
            events.put(("final", execute(on_token).model_dump_json()))
        except HTTPException as error:
            # A missing conversation is a client error, not a workflow failure.
            logger.warning("Chat turn rejected: %s", error.detail)
            events.put(("error", json.dumps({"detail": error.detail})))
        except _REJECTED_TURN_ERRORS as error:
            logger.warning("Chat turn rejected: %s", error)
            events.put(("error", json.dumps({"detail": str(error)})))
        except Exception:
            logger.exception("Unhandled error while executing chat turn")
            events.put(("error", json.dumps({"detail": "internal error"})))
        finally:
            events.put(_STREAM_DONE)

    threading.Thread(target=run, daemon=True).start()

    async def event_stream():
        while True:
            try:
                item = await loop.run_in_executor(
                    None,
                    lambda: events.get(timeout=_HEARTBEAT_INTERVAL_SECONDS),
                )
            except queue.Empty:
                # Routing/retrieval can run long before the first token; a
                # heartbeat keeps the connection from looking dead to
                # proxies and client-side idle timeouts.
                yield ": heartbeat\n\n"
                continue
            if item is _STREAM_DONE:
                return
            kind, payload = item
            if kind == "token":
                yield _sse("token", {"text": payload})
            elif kind in {"final", "error"}:  # already-serialized JSON
                yield f"event: {kind}\ndata: {payload}\n\n"
            else:
                logger.warning("Ignoring unknown stream event kind: %s", kind)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    owner_id: UUID = Depends(current_owner),
) -> StreamingResponse:
    """Stream the answer as it is generated instead of waiting for it whole.

    Ordinary answers emit `token` events as generation text arrives. Hierarchy
    summaries buffer validation/repair attempts and emit only the validated
    answer. Every request ends with one `final` (matching ChatResponse) or
    `error` event.
    """

    await run_in_threadpool(
        _require_ready_books,
        owner_id,
        sorted(set(request.book_ids + request.mentioned_book_ids)),
    )
    return _streamed_turn(
        lambda on_token: _run_turn(owner_id, request, token_callback=on_token)
    )


@app.get("/api/conversations", response_model=ConversationListResponse)
async def conversations(
    owner_id: UUID = Depends(current_owner),
    limit: int = 50,
) -> ConversationListResponse:
    """List the caller's conversations, most recently used first."""

    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")

    def load() -> list[ConversationSummary]:
        with database_connection(readonly=True) as connection:
            return [
                ConversationSummary(
                    conversation_id=row["id"],
                    title=row["title"],
                    book_ids=list(row["book_ids"]),
                    retrieval_mode=row["retrieval_mode"],
                    turn_count=row["turn_count"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    side_thread_count=row["side_thread_count"],
                )
                for row in list_conversations(
                    connection, owner_id=owner_id, limit=limit
                )
            ]

    return ConversationListResponse(conversations=await run_in_threadpool(load))


@app.get(
    "/api/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def conversation_detail(
    conversation_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> ConversationDetail:
    """Full turn history, enough to render a resumed conversation intact."""

    def load() -> ConversationDetail:
        with database_connection(readonly=True) as connection:
            record = load_conversation(connection, conversation_id, owner_id=owner_id)
            if record is None:
                raise CONVERSATION_NOT_FOUND
            turns = load_turns(connection, conversation_id, owner_id=owner_id)
        return ConversationDetail(
            conversation_id=record["id"],
            title=record["title"],
            book_ids=list(record["book_ids"]),
            retrieval_mode=record["retrieval_mode"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            prompt_profile=_stored_profile(record["prompt_profile_json"]),
            parent_conversation_id=record["parent_conversation_id"],
            anchors=[
                QuoteAnchor.model_validate(anchor)
                for anchor in record["anchors_json"] or []
            ],
            turns=[
                ConversationTurn(
                    turn_index=turn["turn_index"],
                    question=turn["question"],
                    answer=turn["answer"],
                    result=TurnResult.model_validate(turn["result_json"]),
                    created_at=turn["created_at"],
                )
                for turn in turns
            ],
        )

    return await run_in_threadpool(load)


@app.patch(
    "/api/conversations/{conversation_id}",
    response_model=ConversationSummary,
)
async def rename_conversation(
    conversation_id: UUID,
    request: UpdateConversationRequest,
    owner_id: UUID = Depends(current_owner),
) -> ConversationSummary:
    def apply() -> ConversationSummary:
        with database_connection() as connection:
            record = update_conversation(
                connection,
                conversation_id,
                owner_id=owner_id,
                title=request.title.strip() if request.title else None,
                retrieval_mode=request.retrieval_mode,
                prompt_profile=(
                    request.prompt_profile.model_dump(mode="json")
                    if request.prompt_profile
                    else None
                ),
            )
            if record is None:
                raise CONVERSATION_NOT_FOUND
            counts = connection.execute(
                """
                select
                    (
                        select count(*) from conversation_turns
                        where conversation_id = %s and owner_id = %s
                    ) as turn_count,
                    (
                        select count(*) from conversations
                        where parent_conversation_id = %s and owner_id = %s
                    ) as side_thread_count
                """,
                (conversation_id, owner_id, conversation_id, owner_id),
            ).fetchone()
        return ConversationSummary(
            conversation_id=record["id"],
            title=record["title"],
            book_ids=list(record["book_ids"]),
            retrieval_mode=record["retrieval_mode"],
            turn_count=counts["turn_count"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            side_thread_count=counts["side_thread_count"],
        )

    return await run_in_threadpool(apply)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def remove_conversation(
    conversation_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    def remove() -> bool:
        with database_connection() as connection:
            return delete_conversation(connection, conversation_id, owner_id=owner_id)

    if not await run_in_threadpool(remove):
        raise CONVERSATION_NOT_FOUND
    return Response(status_code=204)


IMAGE_NOT_FOUND = HTTPException(status_code=404, detail="image not found")
# Canonical content is immutable, so a fetched figure never needs revalidating.
IMAGE_CACHE_CONTROL = "private, max-age=31536000, immutable"
MAXIMUM_IMAGE_BYTES = 8 * 1024 * 1024


@app.get("/api/books/{book_id}/blocks/{block_id}/image")
async def block_image(
    book_id: int,
    block_id: int,
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    """Serve one canonical figure.

    Owner-scoped in the query itself: a block belonging to someone else is
    indistinguishable from one that does not exist.
    """

    def load() -> tuple[bytes, str]:
        with database_connection(readonly=True) as connection:
            row = connection.execute(
                """
                select image_blocks.mime_type, image_blocks.base64_content
                from image_blocks
                join content_blocks
                  on content_blocks.id = image_blocks.block_id
                 and content_blocks.owner_id = image_blocks.owner_id
                where image_blocks.block_id = %s
                  and image_blocks.book_id = %s
                  and image_blocks.owner_id = %s
                """,
                (block_id, book_id, owner_id),
            ).fetchone()
        if row is None:
            raise IMAGE_NOT_FOUND
        try:
            payload = base64.b64decode(row["base64_content"], validate=True)
        except (ValueError, binascii.Error) as error:
            logger.warning("Figure %s has undecodable content", block_id)
            raise IMAGE_NOT_FOUND from error
        if len(payload) > MAXIMUM_IMAGE_BYTES:
            logger.warning("Figure %s exceeds the response ceiling", block_id)
            raise IMAGE_NOT_FOUND
        return payload, row["mime_type"]

    payload, mime_type = await run_in_threadpool(load)
    etag = f'"{hashlib.sha256(payload).hexdigest()[:32]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": IMAGE_CACHE_CONTROL},
        )
    return Response(
        content=payload,
        media_type=mime_type,
        headers={"ETag": etag, "Cache-Control": IMAGE_CACHE_CONTROL},
    )


class BookSourceResponse(ContractModel):
    """A time-limited link to one book's original PDF."""

    book_id: int
    url: str
    expires_at: datetime
    page_count: int | None


SOURCE_UNAVAILABLE = HTTPException(
    status_code=404,
    detail="this book's original file is no longer stored",
)
# Long enough to read a chapter, short enough that a leaked link expires.
SOURCE_URL_TTL_SECONDS = 900


@app.get("/api/books/{book_id}/source", response_model=BookSourceResponse)
async def book_source(
    book_id: int,
    owner_id: UUID = Depends(current_owner),
) -> BookSourceResponse:
    """Sign a short-lived URL for the caller's own book PDF.

    Ready books keep their source precisely so it can be read alongside an
    answer. A book whose object the retention policy removed returns 404 with
    a message the interface can explain, rather than presenting a viewer that
    silently fails to load.
    """

    def sign() -> BookSourceResponse:
        with database_connection(readonly=True) as connection:
            row = ready_book(connection, book_id, owner_id=owner_id)
            if row is None:
                raise BOOK_NOT_FOUND
            details = connection.execute(
                """
                select source_storage_bucket, source_storage_path,
                       viewer_storage_bucket, viewer_storage_path, page_count
                from books where id = %s and owner_id = %s
                """,
                (book_id, owner_id),
            ).fetchone()

        # A viewer copy wins when there is one. It exists only for books whose
        # own bytes could not be stored - a scan above the upload ceiling - and
        # it is a rendering of the same pages, so a citation still lands where
        # it should. The source stays the hash-identified original everywhere
        # else in the system.
        bucket = details["viewer_storage_bucket"] or details["source_storage_bucket"]
        path = details["viewer_storage_path"] or details["source_storage_path"]
        if not bucket or not path:
            # Books imported by the manual CLI path never had a stored object.
            raise SOURCE_UNAVAILABLE

        try:
            url = signed_object_url(bucket, path, expires_in=SOURCE_URL_TTL_SECONDS)
        except IngestionError as error:
            logger.warning("signing book %s failed: %s", book_id, error.code)
            raise HTTPException(
                status_code=503,
                detail="the document store is unavailable",
            ) from error
        if url is None:
            raise SOURCE_UNAVAILABLE

        return BookSourceResponse(
            book_id=book_id,
            url=url,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=SOURCE_URL_TTL_SECONDS),
            page_count=details["page_count"],
        )

    return await run_in_threadpool(sign)
