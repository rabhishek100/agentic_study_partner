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
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from starlette.concurrency import run_in_threadpool

load_dotenv()

from api.auth import current_owner
from storage.book_images import load_figure
from video.media_store import MediaStoreError
from api.courses import router as course_router
from api.course_chat import router as course_chat_router
from api.version import build_revision, build_time
from api.decks import router as deck_router
from api.ingestions import router as ingestion_router
from api.interviews import router as interview_router
from api.notifications import router as notification_router
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
    list_reading_sessions,
    list_side_chats,
    load_conversation,
    load_turns,
    reading_session,
    set_conversation_state,
    set_source_position,
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
from storage.postgres import list_books, ready_book, rename_book
from storage.preferences import load_prompt_profile, save_prompt_profile
from storage.suggested_questions import (
    get_cached_suggested_questions,
    save_cached_suggested_questions,
    versioned_suggested_questions_key,
)
from study.question_generator import generate_book_questions
from study.analyze import ConversationDecisionError
from study.anchors import resolve_document_anchors
from study.contracts import (
    MAXIMUM_ANCHORS,
    MAXIMUM_QUOTE_CHARS,
    Anchor,
    AnswerArchetype,
    ContractModel,
    ConversationState,
    DocumentPageAnchor,
    DocumentPassageAnchor,
    DocumentSectionAnchor,
    PromptProfile,
    QuoteAnchor,
    ResponseDepth,
    TurnResult,
    parse_anchor,
    parse_anchors,
)
from study.conversation import execute_conversation_turn, new_conversation_state
from study.grounding import GroundingPolicy
from study.dictation import (
    MAXIMUM_QUESTION_BYTES,
    DictationError,
    audio_extension,
    transcribe_spoken_question,
)
from study.side_context import (
    AnchoredSource,
    ParentTurn,
    build_side_context,
    readable_quote,
)
from study.prompts import (
    DEFAULT_PROMPT_PROFILE,
    LOCKED_GROUNDING_PROMPT,
    profile_version,
    prompt_preview,
)
from study.query import QueryExecutionError
from study.scope import ScopeResolutionError, list_chapters
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
    # Which recorded turn this is. The client cannot derive it from its own
    # list: a stopped turn is never recorded, and a regenerated one is recorded
    # a second time, so list position and turn index diverge. A side chat has
    # to name a turn index to anchor to, hence returning it here.
    turn_index: int


class TranscriptionResponse(ContractModel):
    text: str


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
    anchors: list[Anchor] = Field(default_factory=list)


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

    kind: Literal["answer_quote"] = "answer_quote"
    anchor_id: str | None = Field(default=None, min_length=1, max_length=64)
    parent_turn_index: int = Field(ge=0)
    # Matches the stored contract; see the note on `QuoteAnchor.quoted_text`.
    quoted_text: str = Field(min_length=1, max_length=MAXIMUM_QUOTE_CHARS)


# The source anchors differ from their stored contracts in exactly one field:
# the id is the server's to assign, so it is optional on the way in. Subclassing
# keeps the shape, the bounds and the `extra="forbid"` of the contract rather
# than restating them here, where the two copies would drift.
class PageAnchorInput(DocumentPageAnchor):
    anchor_id: str | None = Field(default=None, min_length=1, max_length=64)


class PassageAnchorInput(DocumentPassageAnchor):
    anchor_id: str | None = Field(default=None, min_length=1, max_length=64)


class SectionAnchorInput(DocumentSectionAnchor):
    anchor_id: str | None = Field(default=None, min_length=1, max_length=64)


SideChatAnchorRequest = Annotated[
    SideChatAnchorInput | PageAnchorInput | PassageAnchorInput | SectionAnchorInput,
    Field(discriminator="kind"),
]


def _tagged_anchors(value: object) -> object:
    """Give an untagged anchor the tag every stored one before this had.

    A tagged union needs its discriminator present in the input, and the
    interface that shipped before source anchors existed sends none. Defaulting
    it here rather than making `kind` optional keeps the union tagged — an
    untagged union would have to guess which member a malformed anchor meant.
    """

    if not isinstance(value, list):
        return value
    return [
        {**item, "kind": "answer_quote"}
        if isinstance(item, dict) and "kind" not in item
        else item
        for item in value
    ]


class CreateSideChatRequest(ContractModel):
    # Zero anchors is a real request, not a malformed one: it means "ask this
    # conversation's scope without naming a passage". The anchor editor has
    # always allowed a reader to remove the last chip and keep the thread, and
    # a reading session's composer offers the same thing before the first turn
    # — a question about the book rather than about the page in front of them.
    anchors: list[SideChatAnchorRequest] = Field(
        default_factory=list,
        max_length=MAXIMUM_ANCHORS,
    )
    title: str | None = Field(default=None, min_length=1, max_length=200)

    _tag_anchors = field_validator("anchors", mode="before")(_tagged_anchors)


class UpdateSideChatRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    anchors: list[SideChatAnchorRequest] | None = Field(
        default=None,
        max_length=MAXIMUM_ANCHORS,
    )

    _tag_anchors = field_validator("anchors", mode="before")(_tagged_anchors)


class SourcePosition(ContractModel):
    """Where the reader last was in a source.

    A page for a book. Kept as its own model rather than a bare integer
    because the same field carries a lecture timestamp on the other surface,
    and a shape that has to grow a second meaning later is worse than one that
    was always a place.
    """

    page: int = Field(gt=0)


class ReadingSession(ContractModel):
    conversation_id: UUID
    book_id: int
    title: str
    document_type: str
    # Null until the reader has turned a page — a session opened and left on
    # page one has nothing to resume to that opening it would not do anyway.
    position: SourcePosition | None = None
    # Anchored questions asked in this session. Counted rather than listed:
    # they belong on the reading surface, not in a library card.
    question_count: int = 0
    updated_at: datetime


class ReadingSessionListResponse(ContractModel):
    sessions: list[ReadingSession]


class OpenReadingSessionRequest(ContractModel):
    book_id: int = Field(gt=0)


class UpdatePositionRequest(ContractModel):
    position: SourcePosition


DocumentAnchorRequest = Annotated[
    PageAnchorInput | PassageAnchorInput | SectionAnchorInput,
    Field(discriminator="kind"),
]


class ResolveAnchorRequest(ContractModel):
    anchor: DocumentAnchorRequest


class ResolvedAnchorResponse(ContractModel):
    """What a selection was found to be, before anything is asked about it.

    The popover shows this so the reader learns whether their highlight is
    citable *before* they commit a question to it. A miss is a designed state,
    not an error, and saying so up front is the difference between an answer
    that quietly rests on the page and one the reader knows rests on the page.
    """

    matched: bool
    # Where this lands, in the reader's terms: "p. 108 · 4.3 Class imbalance".
    label: str
    # How many canonical passages it names. Zero with `matched` false is a
    # selection the page itself could not account for.
    passage_count: int


class SideChatSummary(ContractModel):
    conversation_id: UUID
    parent_conversation_id: UUID
    title: str
    book_ids: list[int]
    retrieval_mode: RetrievalMode
    anchors: list[Anchor]
    turn_count: int
    created_at: datetime
    updated_at: datetime


class SideChatListResponse(ContractModel):
    side_chats: list[SideChatSummary]


class SuggestedQuestionsResponse(ContractModel):
    questions: list[str]
    scope_type: str
    scope_key: str


class SideChatTurnRequest(ContractModel):
    question: str = Field(min_length=1, max_length=10_000)
    # A side question is a clarification, and a long answer in a small window
    # scrolls badly. The reader can still ask for more depth per window.
    response_depth: ResponseDepth = "quick"
    # The **stay in this source** lock, sent per turn rather than stored on the
    # session. It is a reader's instruction about the question they are asking
    # now — "answer from this book or tell me you cannot" — and the interface
    # remembers their preference so they do not restate it. Storing it server
    # side would make it a property of the session, which would then have to be
    # reconciled with a reader who wants one question answered either way.
    #
    # Only meaningful on a turn that has an open source to stay in; a side chat
    # anchored to an answer has no first rung, so there is nothing to lock.
    stay_in_source: bool = False


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
    document_type: str = "book"


class RenameBookRequest(ContractModel):
    """A reader naming their own book or paper.

    Bounded the way the column is: `books.title` refuses a blank, and a name
    nobody could read in a list is not a name. Whitespace is collapsed by the
    storage layer, so a pasted title with a line break in it still arrives as
    one line.
    """

    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def collapse_and_require_words(cls, value: str) -> str:
        """A title of nine spaces satisfies `min_length` and names nothing.

        Collapsing here rather than only in the storage layer is what makes a
        blank arrive as a 422 the client can show against the field, instead of
        as a 500 from a constraint deeper in.
        """

        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("title cannot be blank")
        return cleaned


class RenamedBookResponse(ContractModel):
    book_id: int
    title: str
    document_type: str


class BookListResponse(ContractModel):
    books: list[BookSummary]


class ChapterSummary(ContractModel):
    """One selectable top-level scope, for anything that studies a chapter."""

    node_id: int
    title: str
    path_text: str
    start_page: int
    end_page: int


class ChapterListResponse(ContractModel):
    book_id: int
    chapters: list[ChapterSummary]


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
app.include_router(course_router)
app.include_router(course_chat_router)
app.include_router(notification_router)
app.include_router(deck_router)
app.include_router(interview_router)


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
async def books(
    owner_id: UUID = Depends(current_owner),
    document_type: str | None = Query("book"),
) -> BookListResponse:
    """List the caller's ready books/papers. Processing items are not selectable."""

    target_doc_type = None if document_type == "all" else document_type

    def load() -> list[BookSummary]:
        with database_connection(readonly=True) as connection:
            rows = list_books(
                connection, owner_id=owner_id, document_type=target_doc_type
            )
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
                        document_type=row.get("document_type", "book") or "book",
                    )
                )
            return summaries

    return BookListResponse(books=await run_in_threadpool(load))


@app.get("/api/papers", response_model=BookListResponse)
async def papers(owner_id: UUID = Depends(current_owner)) -> BookListResponse:
    """List the caller's ready scientific papers."""

    def load() -> list[BookSummary]:
        with database_connection(readonly=True) as connection:
            rows = list_books(connection, owner_id=owner_id, document_type="paper")
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
                        document_type="paper",
                    )
                )
            return summaries

    return BookListResponse(books=await run_in_threadpool(load))


@app.patch("/api/books/{book_id}", response_model=RenamedBookResponse)
async def rename_book_title(
    book_id: int,
    request: RenameBookRequest,
    owner_id: UUID = Depends(current_owner),
) -> RenamedBookResponse:
    """Rename one book or paper.

    Titles are derived now — from the PDF's metadata, its first page, or its
    filename read as words — and a derivation is a guess about a document
    nobody described. A saved web page keeps the site's chrome in its metadata
    title, and a paper whose first page was never captured keeps whatever its
    filename said. This is how those get fixed without a SQL client.

    One route for both kinds: papers are books with a `document_type`, share
    the table, and there is nothing about renaming that differs between them.
    """

    def save() -> dict | None:
        with database_connection() as connection:
            return rename_book(
                connection, book_id, owner_id=owner_id, title=request.title
            )

    row = await run_in_threadpool(save)
    if row is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return RenamedBookResponse(
        book_id=row["id"],
        title=row["title"],
        document_type=row.get("document_type") or "book",
    )


@app.get("/api/books/{book_id}/chapters", response_model=ChapterListResponse)
async def book_chapters(
    book_id: int,
    owner_id: UUID = Depends(current_owner),
) -> ChapterListResponse:
    """The book's chapters, in table-of-contents order.

    Deterministic hierarchy, no retrieval and no model call: the interface
    needs a list to pick from when a reader chooses a chapter to make cards
    from, and the canonical outline already is that list.
    """

    def load() -> list[ChapterSummary]:
        with database_connection(readonly=True) as connection:
            _require_ready_books(owner_id, [book_id])
            return [
                ChapterSummary(
                    node_id=node.id,
                    title=node.title,
                    path_text=node.path_text,
                    start_page=node.start_page,
                    end_page=node.end_page,
                )
                for node in list_chapters(
                    connection, owner_id=owner_id, book_id=book_id
                )
            ]

    return ChapterListResponse(
        book_id=book_id, chapters=await run_in_threadpool(load)
    )


@app.get("/api/books/suggested-questions", response_model=SuggestedQuestionsResponse)
async def book_suggested_questions(
    book_ids: str | None = Query(default=None, description="Comma-separated book IDs"),
    refresh: bool = Query(default=False),
    owner_id: UUID = Depends(current_owner),
) -> SuggestedQuestionsResponse:
    """Get dynamic suggested questions for selected book(s) or library scope."""

    parsed_ids = []
    if book_ids:
        try:
            parsed_ids = sorted({int(x.strip()) for x in book_ids.split(",") if x.strip().isdigit()})
        except Exception:
            parsed_ids = []

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if len(parsed_ids) == 1:
        scope_type = "book"
        scope_key = f"book:{parsed_ids[0]}"
    elif len(parsed_ids) > 1:
        scope_type = "library"
        scope_key = f"books:{','.join(map(str, parsed_ids))}:{today_str}"
    else:
        scope_type = "library"
        scope_key = f"books:all:{today_str}"
    cache_key = versioned_suggested_questions_key(scope_key)

    def load() -> SuggestedQuestionsResponse:
        with database_connection() as connection:
            if not refresh:
                cached = get_cached_suggested_questions(
                    connection,
                    owner_id=owner_id,
                    scope_type=scope_type,
                    scope_key=cache_key,
                )
                if cached and len(cached) == 5:
                    return SuggestedQuestionsResponse(
                        questions=cached,
                        scope_type=scope_type,
                        scope_key=scope_key,
                    )

            questions = generate_book_questions(
                connection, owner_id=owner_id, book_ids=parsed_ids if parsed_ids else None
            )
            save_cached_suggested_questions(
                connection,
                owner_id=owner_id,
                scope_type=scope_type,
                scope_key=cache_key,
                questions=questions,
            )
            return SuggestedQuestionsResponse(
                questions=questions,
                scope_type=scope_type,
                scope_key=scope_key,
            )

    return await run_in_threadpool(load)


@app.post("/api/books/suggested-questions/refresh", response_model=SuggestedQuestionsResponse)
async def refresh_book_suggested_questions(
    book_ids: str | None = Query(default=None, description="Comma-separated book IDs"),
    owner_id: UUID = Depends(current_owner),
) -> SuggestedQuestionsResponse:
    """Force re-generation of dynamic suggested questions."""
    return await book_suggested_questions(book_ids=book_ids, refresh=True, owner_id=owner_id)


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
) -> int:
    """Record the turn and return the index it was recorded under."""

    with database_connection() as connection:
        return append_turn(
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
    turn_index = _persist_turn(owner_id, conversation_id, question, result, updated)
    return ChatResponse(result=result, state=updated, turn_index=turn_index)


SIDE_CHAT_NOT_FOUND = HTTPException(status_code=404, detail="side chat not found")


def _assigned_anchors(
    requested: list[SideChatAnchorRequest],
    *,
    existing: list[Anchor],
    turn_indexes: set[int],
    book_ids: Sequence[int],
) -> list[Anchor]:
    """Validate anchors against what the parent actually has, and assign ids.

    Two different checks, because the two kinds of anchor point at different
    things. A quote names a turn of the parent conversation; a document anchor
    names a book. Either one naming something the parent does not have is
    rejected rather than dropped: it means the client and the server disagree
    about the conversation, and silently answering with less context than the
    reader selected is the wrong way to find that out.

    The book check is scope integrity rather than access control — the chunk
    queries are owner-scoped either way — but a side chat's books are inherited
    and frozen at creation, and an anchor is not a way around that.
    """

    known = {anchor.anchor_id for anchor in existing}
    permitted = set(book_ids)
    used: set[str] = set()
    anchors: list[Anchor] = []
    for item in requested:
        if isinstance(item, SideChatAnchorInput):
            if item.parent_turn_index not in turn_indexes:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"turn {item.parent_turn_index} is not part of the "
                        "parent conversation"
                    ),
                )
        elif item.book_id not in permitted:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"book {item.book_id} is not part of this conversation's "
                    "selection"
                ),
            )
        anchor_id = item.anchor_id
        if anchor_id is None or anchor_id not in known or anchor_id in used:
            anchor_id = uuid4().hex
        used.add(anchor_id)
        stored = item.model_dump(mode="json")
        stored["anchor_id"] = anchor_id
        for field in ("quoted_text", "selected_text"):
            if field in stored:
                stored[field] = stored[field].strip()
        # Through the union rather than the matching class: the input models
        # are the stored contracts with one field widened, so validating the
        # dump is what proves the widening is all that differs.
        anchors.append(parse_anchor(stored))
    return anchors


def _side_chat_title(anchors: Sequence[Anchor], anchored_turn: dict | None) -> str:
    """Name a side chat after whatever the reader actually pointed at.

    A selection can be nothing but a citation marker, which names a thread
    badly, so an answer quote falls back to the question it came from. A source
    anchor has no question behind it: the words the reader highlighted name it,
    and a bare page anchor is named by the page.
    """

    if not anchors:
        # Named by its first question instead, which the caller passes as the
        # title; this is only the fallback for a caller that passes neither.
        return "New question"
    first = anchors[0]
    if isinstance(first, QuoteAnchor):
        return (
            readable_quote(first.quoted_text)
            or (anchored_turn or {}).get("question")
            or "Side chat"
        )
    selected = getattr(first, "selected_text", "")
    if selected.strip():
        return " ".join(selected.split())
    if isinstance(first, DocumentPageAnchor):
        return f"Page {first.page}"
    if isinstance(first, DocumentSectionAnchor):
        return "This section"
    return "Side chat"


def _side_chat_summary(record: dict, turn_count: int) -> SideChatSummary:
    return SideChatSummary(
        conversation_id=record["id"],
        parent_conversation_id=record["parent_conversation_id"],
        title=record["title"],
        book_ids=list(record["book_ids"]),
        retrieval_mode=record["retrieval_mode"],
        anchors=parse_anchors(record["anchors_json"]),
        turn_count=turn_count,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


def _seeded_side_chat_state(
    conversation_id: UUID,
    *,
    book_ids: list[int],
    anchored_turn: dict | None,
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
    if anchored_turn is None:
        # Anchored to the source rather than to an answer: there is no previous
        # answer, and `prior_answer_transform` correctly has nothing to work on
        # until this side chat has produced one of its own.
        return state
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
            book_ids=parent["book_ids"],
        )
        quoted = [anchor for anchor in anchors if isinstance(anchor, QuoteAnchor)]
        # Only a quote anchor has a turn to seed from. A side chat opened on a
        # page of the source has no previous answer, and inventing one from the
        # parent's last turn would seed it with an exchange the reader was not
        # looking at.
        anchored_turn = turns[quoted[0].parent_turn_index] if quoted else None
        title = request.title or _side_chat_title(anchors, anchored_turn)
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


def _anchored_sources(
    connection,
    anchors: Sequence[Anchor],
    *,
    owner_id: UUID,
) -> tuple[AnchoredSource, ...]:
    """Resolve this side chat's source anchors into pinnable context.

    The two-step shape is deliberate. `study.anchors` knows about books, pages
    and chunks; `study.side_context` knows about budgets and priority and stays
    surface-neutral so the lecture side can reuse it. This is the seam, and it
    is the only place that has to know both.
    """

    documents = [
        anchor
        for anchor in anchors
        if isinstance(
            anchor,
            (DocumentPageAnchor, DocumentPassageAnchor, DocumentSectionAnchor),
        )
    ]
    if not documents:
        return ()
    return tuple(
        resolved.as_source()
        for resolved in resolve_document_anchors(
            connection,
            documents,
            owner_id=owner_id,
        )
    )


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
        anchors = parse_anchors(record["anchors_json"])
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
        # Resolved on every turn rather than stored with the anchor. A page
        # points at whatever the book's current chunks say is on it, so a
        # re-ingest moves the pins with the book instead of leaving them
        # pointing at chunks that no longer exist.
        sources = _anchored_sources(connection, anchors, owner_id=owner_id)

    book_ids = list(record["book_ids"])
    _require_ready_books(owner_id, book_ids)
    # A side chat anchored to a page of a book is a source-first turn: that
    # book is what the reader has open, and the rest of the conversation's
    # selection is what the ladder may widen to if the page does not answer.
    # Anchored to an answer instead, there is no open source and the ladder
    # stays out of the way.
    anchored_books = {
        anchor.book_id
        for anchor in anchors
        if isinstance(
            anchor,
            (DocumentPageAnchor, DocumentPassageAnchor, DocumentSectionAnchor),
        )
    }
    result, updated = execute_conversation_turn(
        question,
        _side_chat_state(record),
        owner_id=owner_id,
        retrieval_mode=record["retrieval_mode"],
        book_ids=book_ids,
        grounding_policy=(
            GroundingPolicy.for_source(
                anchored_books,
                library_book_ids=book_ids,
                allow_model_knowledge=not request.stay_in_source,
            )
            if anchored_books
            else None
        ),
        token_callback=token_callback,
        prompt_profile=_stored_profile(record["prompt_profile_json"]),
        response_depth=request.response_depth,
        side_context=build_side_context(
            [anchor for anchor in anchors if isinstance(anchor, QuoteAnchor)],
            parent_turns,
            sources=sources,
        ),
    )
    updated = updated.model_copy(update={"conversation_id": str(side_chat_id)})
    turn_index = _persist_turn(owner_id, side_chat_id, question, result, updated)
    return ChatResponse(result=result, state=updated, turn_index=turn_index)


READING_SESSION_NOT_FOUND = HTTPException(
    status_code=404,
    detail="reading session not found",
)


def _reading_session(record: dict, *, book: dict, question_count: int) -> ReadingSession:
    stored = record.get("source_position") or None
    return ReadingSession(
        conversation_id=record["id"],
        book_id=record["book_ids"][0],
        title=book["title"],
        document_type=book.get("document_type") or "book",
        position=SourcePosition.model_validate(stored) if stored else None,
        question_count=question_count,
        updated_at=record["updated_at"],
    )


def _open_reading_session(
    owner_id: UUID,
    request: OpenReadingSessionRequest,
) -> ReadingSession:
    """Open the reader's session for a source, or resume the one they have.

    Resuming rather than creating is the whole point: the questions a reader
    asked in the margins of this book last week are side chats of this
    session, and a second session would leave them behind while looking
    identical. The unique index enforces it; this only has to prefer it.
    """

    with database_connection() as connection:
        book = ready_book(connection, request.book_id, owner_id=owner_id)
        if book is None:
            raise BOOK_NOT_FOUND
        existing = reading_session(
            connection,
            owner_id=owner_id,
            book_id=request.book_id,
        )
        if existing is not None:
            counted = connection.execute(
                """
                select count(*) as question_count
                from conversations
                where owner_id = %s and parent_conversation_id = %s
                """,
                (owner_id, existing["id"]),
            ).fetchone()
            return _reading_session(
                existing,
                book=book,
                question_count=counted["question_count"],
            )
        created = create_conversation(
            connection,
            owner_id=owner_id,
            book_ids=[request.book_id],
            retrieval_mode="hybrid_rerank",
            # Named after the source rather than after a question, because a
            # reading session has no first question — the book is the subject,
            # and the questions are its margins.
            title=book["title"],
            document_type=book.get("document_type"),
            prompt_profile=load_prompt_profile(connection, owner_id=owner_id),
            session_kind="read",
        )
    return _reading_session(created, book=book, question_count=0)


@app.post("/api/reading-sessions", response_model=ReadingSession, status_code=201)
async def open_reading_session(
    request: OpenReadingSessionRequest,
    owner_id: UUID = Depends(current_owner),
) -> ReadingSession:
    """Start reading a source, or pick up where this reader left off."""

    return await run_in_threadpool(_open_reading_session, owner_id, request)


@app.get("/api/reading-sessions", response_model=ReadingSessionListResponse)
async def reading_sessions(
    owner_id: UUID = Depends(current_owner),
) -> ReadingSessionListResponse:
    """Sources this reader has started, most recently read first."""

    def load() -> list[ReadingSession]:
        with database_connection(readonly=True) as connection:
            sessions = []
            for row in list_reading_sessions(connection, owner_id=owner_id):
                book = ready_book(connection, row["book_ids"][0], owner_id=owner_id)
                if book is None:
                    # The book was deleted or is being re-ingested. Its session
                    # survives with its questions intact; it just cannot be
                    # offered as somewhere to continue right now.
                    continue
                sessions.append(
                    _reading_session(
                        row,
                        book=book,
                        question_count=row["question_count"],
                    )
                )
            return sessions

    return ReadingSessionListResponse(sessions=await run_in_threadpool(load))


def _resolve_anchor(
    owner_id: UUID,
    conversation_id: UUID,
    request: ResolveAnchorRequest,
) -> ResolvedAnchorResponse:
    with database_connection(readonly=True) as connection:
        record = load_conversation(connection, conversation_id, owner_id=owner_id)
        if record is None or record["session_kind"] != "read":
            raise READING_SESSION_NOT_FOUND
        if request.anchor.book_id not in set(record["book_ids"]):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"book {request.anchor.book_id} is not what this session "
                    "is reading"
                ),
            )
        # Through the stored contract rather than the input model, so what the
        # popover previews is resolved by exactly the code that will resolve
        # the anchor when the question is asked.
        anchor = parse_anchor(
            {**request.anchor.model_dump(mode="json"), "anchor_id": "preview"}
        )
        (resolved,) = resolve_document_anchors(
            connection,
            [anchor],
            owner_id=owner_id,
        )
    return ResolvedAnchorResponse(
        matched=resolved.matched,
        label=resolved.label,
        passage_count=len(resolved.chunk_ids),
    )


@app.post(
    "/api/reading-sessions/{conversation_id}/anchors/resolve",
    response_model=ResolvedAnchorResponse,
)
async def resolve_anchor(
    conversation_id: UUID,
    request: ResolveAnchorRequest,
    owner_id: UUID = Depends(current_owner),
) -> ResolvedAnchorResponse:
    """Say what a selection resolves to, before a question is asked about it."""

    return await run_in_threadpool(
        _resolve_anchor,
        owner_id,
        conversation_id,
        request,
    )


@app.patch(
    "/api/reading-sessions/{conversation_id}/position",
    response_model=ReadingSession,
)
async def update_reading_position(
    conversation_id: UUID,
    request: UpdatePositionRequest,
    owner_id: UUID = Depends(current_owner),
) -> ReadingSession:
    """Record where the reader is, so the next visit opens there.

    Fire-and-forget from the client's point of view, and deliberately does not
    touch `updated_at`: turning a page is not using the session, and letting it
    reorder history would put an idly scrolled book above the one being worked
    in.
    """

    def record() -> ReadingSession:
        with database_connection() as connection:
            updated = set_source_position(
                connection,
                conversation_id,
                owner_id=owner_id,
                position=request.position.model_dump(mode="json"),
            )
            if updated is None:
                raise READING_SESSION_NOT_FOUND
            book = ready_book(
                connection,
                updated["book_ids"][0],
                owner_id=owner_id,
            )
            if book is None:
                raise BOOK_NOT_FOUND
            counted = connection.execute(
                """
                select count(*) as question_count
                from conversations
                where owner_id = %s and parent_conversation_id = %s
                """,
                (owner_id, conversation_id),
            ).fetchone()
            return _reading_session(
                updated,
                book=book,
                question_count=counted["question_count"],
            )

    return await run_in_threadpool(record)


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
                        existing=parse_anchors(record["anchors_json"]),
                        turn_indexes=turn_indexes,
                        book_ids=record["book_ids"],
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


@app.post("/api/transcriptions", response_model=TranscriptionResponse)
async def transcribe(
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> TranscriptionResponse:
    """Transcribe a dictated question so the composer can be spoken into.

    Signed-in callers only, and not because the audio belongs to a book: the
    request spends provider money, so it may not be anonymous. Nothing is
    stored, and the words are handed straight back for the reader to edit
    before they ask anything.
    """

    del owner_id
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if audio_extension(media_type) is None:
        raise HTTPException(
            status_code=415, detail="dictation must be recorded audio"
        )

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAXIMUM_QUESTION_BYTES:
            raise HTTPException(status_code=413, detail="that recording is too long")
    if not payload:
        raise HTTPException(status_code=422, detail="the recording was empty")

    try:
        text = await run_in_threadpool(
            transcribe_spoken_question, bytes(payload), media_type=media_type
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DictationError as error:
        logger.warning("Dictation failed: %s", error)
        raise HTTPException(
            status_code=502, detail="dictation is unavailable; type the question"
        ) from error
    if not text:
        raise HTTPException(status_code=422, detail="no speech was recorded")
    return TranscriptionResponse(text=text)


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
    document_type: str | None = Query(None),
    limit: int = 50,
) -> ConversationListResponse:
    """List the caller's conversations, most recently used first."""

    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    if document_type and document_type not in ("book", "paper"):
        raise HTTPException(status_code=422, detail="document_type must be 'book' or 'paper'")

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
                    connection,
                    owner_id=owner_id,
                    document_type=document_type,
                    limit=limit,
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
                select image_blocks.mime_type, image_blocks.base64_content,
                       image_blocks.storage_key, image_blocks.owner_id
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
            payload = load_figure(row)
        except MediaStoreError as error:
            logger.warning("Figure %s could not be read: %s", block_id, error)
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
