"""FastAPI boundary over the existing conversational study workflow."""

# Environment must be loaded before project modules evaluate model defaults.
# ruff: noqa: E402

import asyncio
import json
import logging
import os
import queue
import threading
from datetime import datetime
from typing import Literal
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from starlette.concurrency import run_in_threadpool

load_dotenv()

from api.auth import current_owner
from api.ingestions import router as ingestion_router
from retrieval.langchain import warm_models
from storage.database import (
    book_retrieval_completeness,
    close_pools,
    connection as database_connection,
    database_readiness,
)
from storage.conversations import (
    append_turn,
    create_conversation,
    delete_conversation,
    derive_title,
    list_conversations,
    load_conversation,
    load_turns,
    update_conversation,
)
from storage.postgres import list_books, ready_book
from study.analyze import ConversationDecisionError
from study.contracts import ContractModel, ConversationState, TurnResult
from study.conversation import execute_conversation_turn, new_conversation_state
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
    retrieval_mode: RetrievalMode = "hybrid"
    # Required and always verified against the caller's ready books. There is
    # deliberately no default and an empty list is rejected: a chat turn must
    # never fall back to book 1, and it must never silently widen to the whole
    # library either. The client sends the reader's selection explicitly.
    book_ids: list[int] = Field(min_length=1, max_length=50)
    # Null starts a new conversation. Conversation state is loaded from and
    # written to the database by the server; it is deliberately no longer
    # accepted from the client, which previously held the only copy and could
    # submit arbitrary state.
    conversation_id: UUID | None = None

    @field_validator("book_ids")
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
    turns: list[ConversationTurn]


class UpdateConversationRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    retrieval_mode: RetrievalMode | None = None


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
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
)
app.include_router(ingestion_router)


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


def _resume_state(
    owner_id: UUID,
    request: ChatRequest,
) -> tuple[UUID, ConversationState]:
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
            if list(existing["book_ids"]) == request.book_ids:
                state = ConversationState.model_validate(existing["state_json"])
                if existing["retrieval_mode"] != request.retrieval_mode:
                    update_conversation(
                        connection,
                        existing["id"],
                        owner_id=owner_id,
                        retrieval_mode=request.retrieval_mode,
                    )
                return existing["id"], state

        created = create_conversation(
            connection,
            owner_id=owner_id,
            book_ids=request.book_ids,
            retrieval_mode=request.retrieval_mode,
            title=derive_title(request.question),
        )

    return created["id"], new_conversation_state(
        book_ids=request.book_ids,
        conversation_id=str(created["id"]),
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
    conversation_id, state = _resume_state(owner_id, request)
    result, updated = execute_conversation_turn(
        question,
        state,
        owner_id=owner_id,
        retrieval_mode=request.retrieval_mode,
        book_ids=request.book_ids,
        token_callback=token_callback,
    )
    # The stored conversation is the identity; a fresh state object from the
    # workflow must not invent a different one.
    updated = updated.model_copy(update={"conversation_id": str(conversation_id)})
    _persist_turn(owner_id, conversation_id, question, result, updated)
    return ChatResponse(result=result, state=updated)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    owner_id: UUID = Depends(current_owner),
) -> ChatResponse:
    await run_in_threadpool(_require_ready_books, owner_id, request.book_ids)
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

    await run_in_threadpool(_require_ready_books, owner_id, request.book_ids)
    loop = asyncio.get_running_loop()
    events: queue.Queue = queue.Queue()

    def on_token(kind: str, text: str) -> None:
        events.put((kind, text))

    def run() -> None:
        try:
            response = _run_turn(owner_id, request, token_callback=on_token)
            events.put(("final", response.model_dump_json()))
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
            record = load_conversation(
                connection, conversation_id, owner_id=owner_id
            )
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
            )
            if record is None:
                raise CONVERSATION_NOT_FOUND
            turn_count = connection.execute(
                """
                select count(*) as turn_count from conversation_turns
                where conversation_id = %s and owner_id = %s
                """,
                (conversation_id, owner_id),
            ).fetchone()["turn_count"]
        return ConversationSummary(
            conversation_id=record["id"],
            title=record["title"],
            book_ids=list(record["book_ids"]),
            retrieval_mode=record["retrieval_mode"],
            turn_count=turn_count,
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    return await run_in_threadpool(apply)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def remove_conversation(
    conversation_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    def remove() -> bool:
        with database_connection() as connection:
            return delete_conversation(
                connection, conversation_id, owner_id=owner_id
            )

    if not await run_in_threadpool(remove):
        raise CONVERSATION_NOT_FOUND
    return Response(status_code=204)
