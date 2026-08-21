"""Run one deck generation job: load evidence, inventory, generate, store.

This is the only place the book path and the lecture path meet. Everything
above it — batching, validation, coverage, scheduling — is written against
`ScopeInventory` and does not know which kind of source it came from, which is
why adding lectures cost an evidence loader rather than a second feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from storage.postgres import ready_book
from study.content import load_scope_content
from study.scope import ScopeNotFoundError, resolve_book, resolve_node
from video.errors import VideoIngestionError
from video.lecture import (
    NoTranscriptError,
    coverage_units,
    load_chapters,
    load_lecture_scope,
)

from . import jobs, store
from .generate import (
    CardModel,
    DeckGenerationError,
    GeneratedDeck,
    GenerationConfig,
    generate_deck,
)
from .topics import (
    ScopeInventory,
    book_inventory,
    lecture_inventory,
    paper_inventory,
)

logger = logging.getLogger("study_partner.decks")


class DeckCancellationRequested(RuntimeError):
    """The owner paused a source while its automatic deck was running."""


class DeckSourceError(RuntimeError):
    """The requested scope cannot produce a deck."""


@dataclass(frozen=True)
class DeckRun:
    deck_id: UUID
    generated: GeneratedDeck


def load_inventory(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    book_id: int | None = None,
    node_id: int | None = None,
    video_id: str | UUID | None = None,
) -> tuple[ScopeInventory, UUID | None]:
    """Build the coverage contract for one scope, and its source version.

    The version is the published video ingestion version a lecture deck was
    built from; a book deck has none, because canonical book content is not
    versioned that way.
    """

    owner = parse_owner_id(owner_id)
    if source_kind == "book":
        if node_id is None:
            if book_id is None:
                raise DeckSourceError(
                    "a document deck needs a paper or chapter scope"
                )
            source = ready_book(connection, book_id, owner_id=owner)
            if source is None:
                raise DeckSourceError("no such ready paper")
            if (source.get("document_type") or "book") != "paper":
                raise DeckSourceError(
                    "a whole-document deck is supported only for papers"
                )
            try:
                scope = resolve_book(
                    connection, owner_id=owner, book_id=book_id
                )
            except ScopeNotFoundError as error:
                raise DeckSourceError(str(error)) from error
        else:
            try:
                scope = resolve_node(connection, node_id, owner_id=owner)
            except ScopeNotFoundError as error:
                raise DeckSourceError(str(error)) from error
            if scope.document_type == "paper":
                raise DeckSourceError(
                    "paper cards cover the complete paper, not one section"
                )
            if book_id is not None and scope.book_id != book_id:
                raise DeckSourceError("that node does not belong to this book")
        bundle = load_scope_content(connection, scope, owner_id=owner)
        inventory = (
            paper_inventory(bundle, connection=connection, owner_id=owner)
            if scope.document_type == "paper"
            else book_inventory(bundle, connection=connection, owner_id=owner)
        )
        if not inventory.topics:
            raise DeckSourceError(
                "this scope has no readable content to make cards from"
            )
        return inventory, None

    if video_id is None:
        raise DeckSourceError("a lecture deck needs a video")
    try:
        scope = load_lecture_scope(connection, owner_id=owner, video_id=video_id)
    except (NoTranscriptError, VideoIngestionError) as error:
        raise DeckSourceError(str(error)) from error

    chapters = load_chapters(connection, owner_id=owner, video_id=video_id)
    title = _video_title(connection, owner_id=owner, video_id=video_id)
    inventory = lecture_inventory(
        scope,
        coverage_units(scope, chapters),
        video_id=video_id,
        title=title,
    )
    if not inventory.topics:
        raise DeckSourceError("this lecture has no transcript to make cards from")
    return inventory, scope.version_id


def _video_title(
    connection: Connection, *, owner_id: UUID, video_id: str | UUID
) -> str:
    row = connection.execute(
        "select title from video.videos where id = %s and owner_id = %s",
        (UUID(str(video_id)), owner_id),
    ).fetchone()
    if row is None:
        raise DeckSourceError("no such lecture")
    return row["title"] or "Lecture"


def run_deck_job(
    connection: Connection,
    *,
    job: jobs.DeckJob,
    model: CardModel | None = None,
    config: GenerationConfig | None = None,
) -> DeckRun:
    """Generate and store one deck for a claimed job.

    Progress is written per generation batch so a long chapter shows movement
    rather than a spinner. Nothing becomes visible in the library until the
    whole deck lands, in one transaction, inside `store_deck`.
    """

    def ensure_not_cancelled() -> None:
        if jobs.cancellation_requested(connection, job_id=job.id):
            raise DeckCancellationRequested("deck generation was cancelled")

    ensure_not_cancelled()
    jobs.record_progress(connection, job_id=job.id, stage="inventory")
    inventory, version_id = load_inventory(
        connection,
        owner_id=job.owner_id,
        source_kind=job.source_kind,
        book_id=job.book_id,
        node_id=job.node_id,
        video_id=job.video_id,
    )
    previous_fronts = (
        store.generated_fronts(
            connection,
            owner_id=job.owner_id,
            scope_key=job.scope_key,
        )
        if job.generation_mode == "topic_generated"
        else ()
    )

    deck_id, version = store.create_deck(
        connection,
        owner_id=job.owner_id,
        source_kind=job.source_kind,
        scope_key=job.scope_key,
        title=inventory.title,
        source_title=inventory.source_title,
        generation_mode=job.generation_mode,
        book_id=job.book_id,
        node_id=job.node_id,
        video_id=job.video_id,
    )
    jobs.attach_deck(connection, job_id=job.id, deck_id=deck_id)
    jobs.record_progress(
        connection,
        job_id=job.id,
        stage="generation",
        topics_total=len(inventory.topics),
        topics_done=0,
    )
    logger.info(
        "deck generation started",
        extra={
            "job_id": str(job.id),
            "owner_id": str(job.owner_id),
            "scope_key": job.scope_key,
            "generation_mode": job.generation_mode,
            "topics": len(inventory.topics),
            "version": version,
        },
    )

    def progress(done: int, total: int) -> None:
        ensure_not_cancelled()
        jobs.record_progress(
            connection,
            job_id=job.id,
            stage="generation",
            topics_total=total,
            topics_done=done,
        )

    try:
        if job.generation_mode == "book_extracted":
            from .extraction import extract_and_generate_deck

            generated = extract_and_generate_deck(
                inventory,
                connection=connection,
                owner_id=str(job.owner_id),
                book_id=job.book_id,
                progress=progress,
            )
        else:
            generated = generate_deck(
                inventory,
                model=model,
                config=config,
                progress=progress,
                previous_fronts=previous_fronts,
            )
    except Exception:
        # Every attempt creates a version before making a provider call.  Do
        # not leave that version stuck in `generating` when an unexpected
        # schema, provider, or persistence error escapes the generation path.
        store.fail_deck(connection, owner_id=job.owner_id, deck_id=deck_id)
        raise

    if not generated.cards and job.generation_mode != "book_extracted":
        store.fail_deck(connection, owner_id=job.owner_id, deck_id=deck_id)
        raise DeckGenerationError(
            "no card survived validation for this scope; nothing was stored"
        )

    ensure_not_cancelled()
    jobs.record_progress(connection, job_id=job.id, stage="storing")
    store.store_deck(
        connection,
        owner_id=job.owner_id,
        deck_id=deck_id,
        topics=inventory.topics,
        generated=generated,
        ingestion_version_id=version_id,
    )
    jobs.finish_job(connection, job_id=job.id, deck_id=deck_id)
    logger.info(
        "deck generation finished",
        extra={
            "job_id": str(job.id),
            "owner_id": str(job.owner_id),
            "scope_key": inventory.scope_key,
            "cards": len(generated.cards),
            "coverage": round(generated.metrics.coverage_ratio, 3),
        },
    )
    return DeckRun(deck_id=deck_id, generated=generated)
