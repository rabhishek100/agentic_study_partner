"""Per-source Cards eligibility and idempotent initial generation."""

from __future__ import annotations

from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from study.scope import list_chapters

from . import jobs
from .contracts import DeckSourcePreference
from .topics import book_scope_key, video_scope_key


def list_sources(
    connection: Connection, *, owner_id: str | UUID
) -> list[DeckSourcePreference]:
    """All book and video sources, including ones still being processed."""

    owner = parse_owner_id(owner_id)
    rows = connection.execute(
        """
        select 'book' as source_kind, book.id::text as source_id,
               book.title, book.document_type, book.status,
               book.cards_enabled,
               exists (
                   select 1 from public.deck_jobs as job
                   where job.owner_id = book.owner_id
                     and job.automatic_key like 'auto:set1:book:' || book.id || ':%%'
                     and job.status in ('queued', 'running')
               ) as automatic_cards_queued,
               coalesce(book.ready_at, book.parsed_at) as source_date
        from public.books as book
        where book.owner_id = %s

        union all

        select 'video' as source_kind, video.id::text as source_id,
               video.title, 'video' as document_type,
               video.readiness_status as status,
               video.cards_enabled,
               exists (
                   select 1 from public.deck_jobs as job
                   where job.owner_id = video.owner_id
                     and job.automatic_key = 'auto:set1:video:' || video.id
                     and job.status in ('queued', 'running')
               ) as automatic_cards_queued,
               coalesce(video.ready_at, video.created_at) as source_date
        from video.videos as video
        where video.owner_id = %s
        order by source_date desc, title
        """,
        (owner, owner),
    ).fetchall()
    return [
        DeckSourcePreference(
            source_kind=row["source_kind"],
            source_id=row["source_id"],
            title=row["title"] or "Untitled source",
            document_type=row["document_type"],
            status=row["status"],
            cards_enabled=row["cards_enabled"],
            automatic_cards_queued=row["automatic_cards_queued"],
        )
        for row in rows
    ]


def save_source(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    source_id: str | int | UUID,
    cards_enabled: bool,
) -> DeckSourcePreference:
    """Persist one owner-scoped setting, refusing unknown foreign sources."""

    owner = parse_owner_id(owner_id)
    if source_kind == "book":
        identifier: int | UUID = int(source_id)
        row = connection.execute(
            """
            update public.books
            set cards_enabled = %s,
                cards_automation_eligible_at = case
                    when %s and status = 'ready'
                    then coalesce(cards_automation_eligible_at, now())
                    else cards_automation_eligible_at
                end
            where id = %s and owner_id = %s
            returning id
            """,
            (cards_enabled, cards_enabled, identifier, owner),
        ).fetchone()
    elif source_kind == "video":
        identifier = UUID(str(source_id))
        row = connection.execute(
            """
            update video.videos
            set cards_enabled = %s,
                cards_automation_eligible_at = case
                    when %s and readiness_status in ('ready', 'degraded')
                    then coalesce(cards_automation_eligible_at, now())
                    else cards_automation_eligible_at
                end
            where id = %s and owner_id = %s
            returning id
            """,
            (cards_enabled, cards_enabled, identifier, owner),
        ).fetchone()
    else:
        raise ValueError("source_kind must be book or video")
    if row is None:
        raise LookupError("no such source")
    if not cards_enabled:
        if source_kind == "book":
            connection.execute(
                """
                update public.deck_jobs
                set cancellation_requested = true,
                    status = case
                        when status = 'queued' then 'cancelled'
                        else status
                    end,
                    updated_at = now()
                where owner_id = %s and book_id = %s
                  and automatic_key is not null
                  and status in ('queued', 'running')
                """,
                (owner, identifier),
            )
        else:
            connection.execute(
                """
                update public.deck_jobs
                set cancellation_requested = true,
                    status = case
                        when status = 'queued' then 'cancelled'
                        else status
                    end,
                    updated_at = now()
                where owner_id = %s and video_id = %s
                  and automatic_key is not null
                  and status in ('queued', 'running')
                """,
                (owner, identifier),
            )
    return next(
        item
        for item in list_sources(connection, owner_id=owner)
        if item.source_kind == source_kind and item.source_id == str(identifier)
    )


def _has_initial_set(
    connection: Connection, *, owner_id: UUID, scope_key: str
) -> bool:
    """Whether automation is already satisfied or permanently accounted for.

    A failed/cancelled manual attempt is not a Set 1 and must not suppress the
    first automatic run.  An automatic failure does count: its durable key is
    retained for diagnosis and its built-in retries have already been spent.
    """

    return connection.execute(
        """
        select exists (
            select 1 from public.decks
            where owner_id = %s and scope_key = %s
              and status in ('ready', 'partial')
            union all
            select 1 from public.deck_jobs
            where owner_id = %s and scope_key = %s
              and (
                  (automatic_key is not null and status <> 'cancelled')
                  or status in ('queued', 'running', 'succeeded')
              )
        ) as found
        """,
        (owner_id, scope_key, owner_id, scope_key),
    ).fetchone()["found"]


def enqueue_initial_for_book(
    connection: Connection, *, owner_id: str | UUID, book_id: int
) -> list[jobs.DeckJob]:
    """Queue Set 1 for every chapter once, if the source is enabled and ready."""

    owner = parse_owner_id(owner_id)
    source = connection.execute(
        """
        select status, document_type, cards_enabled,
               cards_automation_eligible_at is not null as automation_eligible
        from public.books where id = %s and owner_id = %s
        """,
        (book_id, owner),
    ).fetchone()
    if (
        source is None
        or source["status"] != "ready"
        or not source["cards_enabled"]
        or not source["automation_eligible"]
        or source["document_type"] != "book"
    ):
        return []

    queued: list[jobs.DeckJob] = []
    for chapter in list_chapters(connection, owner_id=owner, book_id=book_id):
        scope_key = book_scope_key(book_id, chapter.id)
        if _has_initial_set(connection, owner_id=owner, scope_key=scope_key):
            continue
        queued.append(
            jobs.enqueue(
                connection,
                owner_id=owner,
                source_kind="book",
                scope_key=scope_key,
                book_id=book_id,
                node_id=chapter.id,
                automatic_key=f"auto:set1:{scope_key}",
            )
        )
    return queued


def enqueue_initial_for_video(
    connection: Connection, *, owner_id: str | UUID, video_id: str | UUID
) -> list[jobs.DeckJob]:
    """Queue the one full-lecture Set 1 once an enabled video publishes."""

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(video_id))
    source = connection.execute(
        """
        select readiness_status, cards_enabled,
               cards_automation_eligible_at is not null as automation_eligible
        from video.videos where id = %s and owner_id = %s
        """,
        (identifier, owner),
    ).fetchone()
    if (
        source is None
        or source["readiness_status"] not in {"ready", "degraded"}
        or not source["cards_enabled"]
        or not source["automation_eligible"]
    ):
        return []
    scope_key = video_scope_key(identifier)
    if _has_initial_set(connection, owner_id=owner, scope_key=scope_key):
        return []
    return [
        jobs.enqueue(
            connection,
            owner_id=owner,
            source_kind="video",
            scope_key=scope_key,
            video_id=identifier,
            automatic_key=f"auto:set1:{scope_key}",
        )
    ]


def reconcile_missing_initial_sets(
    connection: Connection, *, limit: int = 20
) -> int:
    """Recover the publish-to-enqueue crash gap without backfilling old data."""

    books = connection.execute(
        """
        select id, owner_id from public.books
        where status = 'ready' and document_type = 'book' and cards_enabled
          and cards_automation_eligible_at is not null
          and exists (
              select 1 from public.nodes as chapter
              where chapter.book_id = books.id
                and chapter.owner_id = books.owner_id
                and chapter.node_type = 'chapter'
                and not exists (
                    select 1 from public.decks as deck
                    where deck.owner_id = books.owner_id
                      and deck.scope_key =
                          'book:' || books.id || ':node:' || chapter.id
                      and deck.status in ('ready', 'partial')
                )
                and not exists (
                    select 1 from public.deck_jobs as job
                    where job.owner_id = books.owner_id
                      and job.scope_key =
                          'book:' || books.id || ':node:' || chapter.id
                      and (
                          (job.automatic_key is not null
                           and job.status <> 'cancelled')
                          or job.status in ('queued', 'running', 'succeeded')
                      )
                )
          )
        order by cards_automation_eligible_at
        limit %s
        """,
        (limit,),
    ).fetchall()
    videos = connection.execute(
        """
        select id, owner_id from video.videos
        where readiness_status in ('ready', 'degraded') and cards_enabled
          and cards_automation_eligible_at is not null
          and not exists (
              select 1 from public.decks as deck
              where deck.owner_id = videos.owner_id
                and deck.scope_key = 'video:' || videos.id
                and deck.status in ('ready', 'partial')
          )
          and not exists (
              select 1 from public.deck_jobs as job
              where job.owner_id = videos.owner_id
                and job.scope_key = 'video:' || videos.id
                and (
                    (job.automatic_key is not null
                     and job.status <> 'cancelled')
                    or job.status in ('queued', 'running', 'succeeded')
                )
          )
        order by cards_automation_eligible_at
        limit %s
        """,
        (limit,),
    ).fetchall()
    queued = 0
    for book in books:
        queued += len(
            enqueue_initial_for_book(
                connection, owner_id=book["owner_id"], book_id=book["id"]
            )
        )
    for video in videos:
        queued += len(
            enqueue_initial_for_video(
                connection, owner_id=video["owner_id"], video_id=video["id"]
            )
        )
    return queued
