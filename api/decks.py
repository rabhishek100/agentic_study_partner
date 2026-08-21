"""Owner-scoped HTTP surface for flashcard decks.

Generation is queued, never run inline: a chapter takes several model calls and
a minute or two, which is long enough that holding a request open would tie up
a connection and lose the run on a dropped socket. Everything else here is a
read or a small write, so it answers directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field, model_validator
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from decks import jobs as deck_jobs
from decks import store
from decks.contracts import (
    ContractModel,
    DeckCard,
    DeckPreferences,
    DeckSummary,
    QueueCard,
    ReviewQueue,
    ReviewState,
)
from decks.conversation import card_turn_result, seeded_state
from decks.pipeline import DeckSourceError, load_inventory
from decks.progress import estimate
from decks.scheduler import build_queue
from decks.store import DeckNotFoundError
from decks.topics import book_scope_key, video_scope_key
from storage.conversations import append_turn, create_conversation, derive_title
from storage.database import connection as database_connection
from study.contracts import MAXIMUM_QUOTE_CHARS, QuoteAnchor
from study.scope import ScopeNotFoundError, resolve_node

logger = logging.getLogger("study_partner.api.decks")

router = APIRouter(prefix="/api/decks", tags=["decks"])


class GenerateDeckRequest(ContractModel):
    source_kind: str = Field(pattern="^(book|video)$")
    generation_mode: str = Field(
        default="topic_generated", pattern="^(topic_generated|book_extracted)$"
    )
    book_id: int | None = None
    node_id: int | None = None
    video_id: str | None = None

    @model_validator(mode="after")
    def extracted_questions_are_book_only(self) -> GenerateDeckRequest:
        if self.source_kind != "book" and self.generation_mode == "book_extracted":
            raise ValueError("book-extracted questions require a book chapter")
        return self


class DeckJobResponse(ContractModel):
    job_id: str
    source_kind: str
    status: str
    stage: str
    scope_key: str
    generation_mode: str = "topic_generated"
    book_id: int | None = None
    node_id: int | None = None
    video_id: str | None = None
    deck_id: str | None = None
    topics_total: int = 0
    topics_done: int = 0
    progress: float = 0.0
    attempt_count: int = 0
    error_code: str | None = None
    error_detail: str | None = None
    title: str = "Deck generation"
    source_title: str = "Source"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    timing: "DeckJobTiming"


class DeckJobStage(ContractModel):
    stage: str
    label: str
    state: Literal["done", "active", "pending"]
    expected_seconds: float
    elapsed_seconds: float | None = None


class DeckJobTiming(ContractModel):
    percent: float
    elapsed_seconds: float
    estimated_total_seconds: float
    estimated_remaining_seconds: float | None
    overrunning: bool
    stages: list[DeckJobStage]


class DeckListResponse(ContractModel):
    decks: list[DeckSummary]
    jobs: list[DeckJobResponse]


class DeckDetailResponse(ContractModel):
    deck: DeckSummary
    cards: list[QueueCard]


class GradeRequest(ContractModel):
    rating: int = Field(ge=1, le=4)
    elapsed_ms: int | None = Field(default=None, ge=0)
    # Set only for an MCQ, where the application knows whether the selection
    # was right. For every other type correctness is the reader's own call,
    # which is exactly what the four ratings are for.
    answered_correctly: bool | None = None


class GradeResponse(ContractModel):
    card_id: str
    review: ReviewState


def _job_response(job: deck_jobs.DeckJob) -> DeckJobResponse:
    timing = estimate(
        status=job.status,
        stage=job.stage,
        generation_mode=job.generation_mode,
        topics_completed=job.topics_done,
        topics_total=job.topics_total,
        created_at=job.created_at,
    )
    return DeckJobResponse(
        job_id=str(job.id),
        source_kind=job.source_kind,
        status=job.status,
        stage=job.stage,
        scope_key=job.scope_key,
        generation_mode=job.generation_mode,
        book_id=job.book_id,
        node_id=job.node_id,
        video_id=str(job.video_id) if job.video_id else None,
        deck_id=str(job.deck_id) if job.deck_id else None,
        topics_total=job.topics_total,
        topics_done=job.topics_done,
        progress=round(timing.percent / 100, 3),
        attempt_count=job.attempt_count,
        error_code=job.error_code,
        error_detail=job.error_detail,
        title=job.title,
        source_title=job.source_title,
        created_at=job.created_at,
        updated_at=job.updated_at,
        timing=DeckJobTiming(
            percent=timing.percent,
            elapsed_seconds=timing.elapsed_seconds,
            estimated_total_seconds=timing.estimated_total_seconds,
            estimated_remaining_seconds=timing.estimated_remaining_seconds,
            overrunning=timing.overrunning,
            stages=[
                DeckJobStage(
                    stage=view.stage,
                    label=view.label,
                    state=view.state,
                    expected_seconds=view.expected_seconds,
                    elapsed_seconds=view.elapsed_seconds,
                )
                for view in timing.stages
            ],
        ),
    )


@router.post("", response_model=DeckJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_deck(
    request: GenerateDeckRequest,
    owner_id: UUID = Depends(current_owner),
) -> DeckJobResponse:
    """Queue a deck for one chapter or one lecture.

    The scope is validated here rather than in the worker, so a chapter that
    has no readable content fails immediately with something the reader can
    act on instead of after a minute in a queue.
    """

    def run() -> DeckJobResponse:
        with database_connection() as connection:
            if request.source_kind == "book":
                if request.node_id is None:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="a book deck needs a chapter or section",
                    )
                try:
                    scope = resolve_node(
                        connection, request.node_id, owner_id=owner_id
                    )
                except ScopeNotFoundError as error:
                    raise HTTPException(
                        status.HTTP_404_NOT_FOUND, detail=str(error)
                    ) from error
                scope_key = book_scope_key(
                    scope.book_id,
                    request.node_id,
                    generation_mode=request.generation_mode,
                )
                book_id: int | None = scope.book_id
                video_id = None
            else:
                if not request.video_id:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="a lecture deck needs a video",
                    )
                scope_key = video_scope_key(request.video_id)
                book_id = None
                video_id = request.video_id

            try:
                load_inventory(
                    connection,
                    owner_id=owner_id,
                    source_kind=request.source_kind,
                    book_id=book_id,
                    node_id=request.node_id,
                    video_id=video_id,
                )
            except DeckSourceError as error:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
                ) from error

            job = deck_jobs.enqueue(
                connection,
                owner_id=owner_id,
                source_kind=request.source_kind,
                scope_key=scope_key,
                generation_mode=request.generation_mode,
                book_id=book_id,
                node_id=request.node_id,
                video_id=video_id,
            )
            return _job_response(job)

    return await run_in_threadpool(run)


@router.get("", response_model=DeckListResponse)
async def list_decks(owner_id: UUID = Depends(current_owner)) -> DeckListResponse:
    def run() -> DeckListResponse:
        with database_connection(readonly=True) as connection:
            return DeckListResponse(
                decks=store.list_decks(connection, owner_id=owner_id),
                jobs=[
                    _job_response(job)
                    for job in deck_jobs.list_jobs(connection, owner_id=owner_id)
                ],
            )

    return await run_in_threadpool(run)


@router.get("/queue", response_model=ReviewQueue)
async def review_queue(
    deck_id: str | None = Query(default=None),
    owner_id: UUID = Depends(current_owner),
) -> ReviewQueue:
    """Everything due today, plus a capped number of new cards.

    With no `deck_id` this spans every deck, which is the point: a daily habit
    should not require choosing which chapter to revise.
    """

    def run() -> ReviewQueue:
        with database_connection(readonly=True) as connection:
            preferences = store.get_preferences(connection, owner_id=owner_id)
            due = store.due_cards(connection, owner_id=owner_id, deck_id=deck_id)
            fresh = store.new_cards(connection, owner_id=owner_id, deck_id=deck_id)
            reviewed, introduced = store.counts_today(connection, owner_id=owner_id)
            return ReviewQueue(
                cards=build_queue(
                    due=due,
                    fresh=fresh,
                    preferences=preferences,
                    reviewed_today=reviewed,
                    new_introduced_today=introduced,
                ),
                due_total=len(due),
                new_total=len(fresh),
                reviewed_today=reviewed,
                new_cards_per_day=preferences.new_cards_per_day,
                max_reviews_per_day=preferences.max_reviews_per_day,
            )

    return await run_in_threadpool(run)


@router.get("/preferences", response_model=DeckPreferences)
async def preferences(owner_id: UUID = Depends(current_owner)) -> DeckPreferences:
    def run() -> DeckPreferences:
        with database_connection(readonly=True) as connection:
            return store.get_preferences(connection, owner_id=owner_id)

    return await run_in_threadpool(run)


@router.patch("/preferences", response_model=DeckPreferences)
async def update_preferences(
    request: DeckPreferences,
    owner_id: UUID = Depends(current_owner),
) -> DeckPreferences:
    def run() -> DeckPreferences:
        with database_connection() as connection:
            return store.save_preferences(
                connection, owner_id=owner_id, preferences=request
            )

    return await run_in_threadpool(run)


@router.get("/jobs/{job_id}", response_model=DeckJobResponse)
async def deck_job(
    job_id: str,
    owner_id: UUID = Depends(current_owner),
) -> DeckJobResponse:
    def run() -> DeckJobResponse:
        with database_connection(readonly=True) as connection:
            try:
                return _job_response(
                    deck_jobs.get_job(connection, owner_id=owner_id, job_id=job_id)
                )
            except LookupError as error:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="no such deck job"
                ) from error

    return await run_in_threadpool(run)


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_deck_job(
    job_id: str,
    owner_id: UUID = Depends(current_owner),
) -> None:
    def run() -> None:
        with database_connection() as connection:
            deck_jobs.request_cancellation(
                connection, owner_id=owner_id, job_id=job_id
            )

    await run_in_threadpool(run)


@router.post("/jobs/{job_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_deck_job(
    job_id: str,
    owner_id: UUID = Depends(current_owner),
) -> None:
    """Dismiss a failed generation alert while retaining its diagnostic row."""

    def run() -> None:
        with database_connection() as connection:
            try:
                job = deck_jobs.get_job(
                    connection, owner_id=owner_id, job_id=job_id
                )
            except (LookupError, ValueError) as error:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="no such deck job"
                ) from error
            if job.status != "failed":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="only failed deck jobs can be dismissed",
                )
            if not deck_jobs.dismiss_failed_job(
                connection, owner_id=owner_id, job_id=job_id
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="this deck job is no longer dismissible",
                )

    await run_in_threadpool(run)


@router.get("/{deck_id}", response_model=DeckDetailResponse)
async def deck_detail(
    deck_id: str,
    owner_id: UUID = Depends(current_owner),
) -> DeckDetailResponse:
    def run() -> DeckDetailResponse:
        with database_connection(readonly=True) as connection:
            try:
                deck = store.get_deck(connection, owner_id=owner_id, deck_id=deck_id)
            except (DeckNotFoundError, ValueError) as error:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="no such deck"
                ) from error
            return DeckDetailResponse(
                deck=deck,
                cards=store.deck_cards(
                    connection, owner_id=owner_id, deck_id=deck_id
                ),
            )

    return await run_in_threadpool(run)


@router.post("/cards/{card_id}/review", response_model=GradeResponse)
async def grade_card(
    card_id: str,
    request: GradeRequest,
    owner_id: UUID = Depends(current_owner),
) -> GradeResponse:
    def run() -> GradeResponse:
        with database_connection() as connection:
            try:
                state = store.record_review(
                    connection,
                    owner_id=owner_id,
                    card_id=card_id,
                    rating=request.rating,
                    elapsed_ms=request.elapsed_ms,
                    answered_correctly=request.answered_correctly,
                )
            except (DeckNotFoundError, ValueError) as error:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="no such card"
                ) from error
            return GradeResponse(card_id=card_id, review=state)

    return await run_in_threadpool(run)


class CardSideChatRequest(ContractModel):
    quoted_text: str = Field(min_length=1, max_length=MAXIMUM_QUOTE_CHARS)


class CardSideChatResponse(ContractModel):
    """The same shape the conversation side-chat endpoints return.

    Deliberately identical: the interface's side-chat hook, window chrome, and
    streaming path all take this, so a card side chat is opened by the code
    that already opens every other one.
    """

    conversation_id: str
    parent_conversation_id: str
    title: str
    anchors: list[QuoteAnchor]
    turn_count: int
    created_at: str
    updated_at: str


class DeckConversationResponse(ContractModel):
    conversation_id: str


@router.post("/cards/{card_id}/side-chats", response_model=CardSideChatResponse)
async def open_card_side_chat(
    card_id: str,
    request: CardSideChatRequest,
    owner_id: UUID = Depends(current_owner),
) -> CardSideChatResponse:
    """Ask about a passage of a card, in a side chat anchored to it.

    The card is recorded as a turn of the deck's own conversation the first
    time it is asked about, and reused afterwards. That is what lets the
    existing side-chat machinery — pinning, seeding, the streaming endpoint,
    reopening a closed window — work over a card without a second
    implementation of any of it.
    """

    def run() -> CardSideChatResponse:
        with database_connection() as connection:
            card, deck = _card_with_deck(connection, owner_id=owner_id, card_id=card_id)
            if deck.source_kind != "book" or deck.book_id is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "lecture cards cannot open a side chat yet; use "
                        "'Ask about this' to continue in the lecture chat"
                    ),
                )

            parent = _deck_conversation(
                connection, owner_id=owner_id, deck=deck
            )
            result = card_turn_result(
                connection,
                owner_id=owner_id,
                card=card,
                book_id=deck.book_id,
                book_title=deck.source_title,
                deck_title=deck.title,
            )
            turn_index = _card_turn_index(
                connection,
                owner_id=owner_id,
                conversation_id=parent["id"],
                card=card,
                result=result,
            )

            anchor = QuoteAnchor(
                anchor_id=uuid4().hex,
                parent_turn_index=turn_index,
                quoted_text=request.quoted_text.strip(),
            )
            side_chat = create_conversation(
                connection,
                owner_id=owner_id,
                book_ids=list(parent["book_ids"]),
                retrieval_mode=parent["retrieval_mode"],
                title=derive_title(card.front),
                prompt_profile=parent["prompt_profile_json"],
                parent_conversation_id=parent["id"],
                anchors=[anchor.model_dump(mode="json")],
                state=seeded_state(
                    parent["id"],
                    book_ids=list(parent["book_ids"]),
                    result=result,
                ).model_dump(mode="json"),
            )
            return CardSideChatResponse(
                conversation_id=str(side_chat["id"]),
                parent_conversation_id=str(parent["id"]),
                title=side_chat["title"],
                anchors=[anchor],
                turn_count=0,
                created_at=str(side_chat["created_at"]),
                updated_at=str(side_chat["updated_at"]),
            )

    return await run_in_threadpool(run)


@router.get("/{deck_id}/conversation", response_model=DeckConversationResponse)
async def deck_conversation(
    deck_id: str,
    owner_id: UUID = Depends(current_owner),
) -> DeckConversationResponse:
    """The conversation that holds this deck's cards, created if absent.

    The interface needs it before the first highlight, because listing a
    deck's existing side chats is what restores the windows a reader left
    open.
    """

    def run() -> DeckConversationResponse:
        with database_connection() as connection:
            try:
                deck = store.get_deck(
                    connection, owner_id=owner_id, deck_id=deck_id
                )
            except (DeckNotFoundError, ValueError) as error:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="no such deck"
                ) from error
            if deck.source_kind != "book" or deck.book_id is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="lecture decks do not have a card conversation yet",
                )
            parent = _deck_conversation(connection, owner_id=owner_id, deck=deck)
            return DeckConversationResponse(conversation_id=str(parent["id"]))

    return await run_in_threadpool(run)


def _card_with_deck(
    connection, *, owner_id: UUID, card_id: str
) -> tuple[DeckCard, DeckSummary]:
    row = connection.execute(
        "select deck_id from public.deck_cards where id = %s and owner_id = %s",
        (UUID(str(card_id)), owner_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such card")
    deck = store.get_deck(connection, owner_id=owner_id, deck_id=row["deck_id"])
    card = next(
        (
            item.card
            for item in store.deck_cards(
                connection, owner_id=owner_id, deck_id=row["deck_id"]
            )
            if item.card.card_id == str(card_id)
        ),
        None,
    )
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such card")
    return card, deck


def _deck_conversation(connection, *, owner_id: UUID, deck: DeckSummary) -> dict:
    """The one conversation that holds this deck's cards, created on demand.

    Created in a single statement rather than select-then-insert. Opening a
    side chat fires two requests at once — the window layer asks for the
    conversation so it can restore any open windows, while the highlight
    itself posts — and both raced to create it, so the second one's insert
    violated the one-per-deck index and returned a 500 on the reader's first
    ever highlight.
    """

    created = connection.execute(
        """
        insert into public.conversations (
            owner_id, title, book_ids, retrieval_mode, prompt_profile_json, deck_id
        )
        values (%s, %s, %s, %s, '{}'::jsonb, %s)
        on conflict (owner_id, deck_id) where deck_id is not null do nothing
        returning id, book_ids, retrieval_mode, prompt_profile_json
        """,
        (
            owner_id,
            f"Cards: {deck.title}",
            [deck.book_id],
            "hybrid_rerank",
            UUID(deck.deck_id),
        ),
    ).fetchone()
    if created is not None:
        return dict(created)

    existing = connection.execute(
        """
        select id, book_ids, retrieval_mode, prompt_profile_json
        from public.conversations
        where owner_id = %s and deck_id = %s
        """,
        (owner_id, UUID(deck.deck_id)),
    ).fetchone()
    if existing is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="this deck's conversation could not be opened; try again",
        )
    return dict(existing)


def _card_turn_index(
    connection, *, owner_id: UUID, conversation_id: UUID, card: DeckCard, result
) -> int:
    """The turn this card occupies, writing it the first time it is asked about."""

    existing = connection.execute(
        """
        select turn_index from public.conversation_turns
        where conversation_id = %s and owner_id = %s and deck_card_id = %s
        """,
        (conversation_id, owner_id, UUID(str(card.card_id))),
    ).fetchone()
    if existing is not None:
        return existing["turn_index"]

    turn_index = append_turn(
        connection,
        conversation_id,
        owner_id=owner_id,
        question=result.question,
        answer=result.answer,
        result=result.model_dump(mode="json"),
        state={},
    )
    connection.execute(
        """
        update public.conversation_turns
        set deck_card_id = %s
        where conversation_id = %s and owner_id = %s and turn_index = %s
        """,
        (UUID(str(card.card_id)), conversation_id, owner_id, turn_index),
    )
    return turn_index


@router.post("/{deck_id}/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_deck(
    deck_id: str,
    owner_id: UUID = Depends(current_owner),
) -> None:
    """Send every card in one deck back to new, keeping the review log."""

    def run() -> None:
        with database_connection() as connection:
            try:
                store.reset_deck_progress(
                    connection, owner_id=owner_id, deck_id=deck_id
                )
            except ValueError as error:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="no such deck"
                ) from error

    await run_in_threadpool(run)
