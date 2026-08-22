"""Per-source Cards eligibility and idempotent initial generation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from study.scope import list_chapters

from . import jobs
from .contracts import DeckSourcePreference
from .topics import book_scope_key, paper_scope_key, video_scope_key


class SourceActivationConflict(ValueError):
    """The source is not safely activatable from the supplied preview."""


@dataclass(frozen=True)
class InitialScope:
    source_kind: str
    source_id: str
    scope_key: str
    book_id: int | None = None
    node_id: int | None = None
    video_id: UUID | None = None


def missing_initial_scopes(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str | None = None,
    source_id: str | int | UUID | None = None,
) -> tuple[InitialScope, ...]:
    """Return every generated Set 1 scope not already durably accounted for."""

    owner = parse_owner_id(owner_id)
    source_identifier = str(source_id) if source_id is not None else None
    rows = connection.execute(
        """
        with candidate_scopes as (
            select 'book'::text as source_kind, book.id::text as source_id,
                   'book:' || book.id || ':node:' || chapter.id as scope_key,
                   book.id as book_id, chapter.id as node_id,
                   null::uuid as video_id
            from public.books as book
            join public.nodes as chapter
              on chapter.book_id = book.id
             and chapter.owner_id = book.owner_id
             and chapter.node_type = 'chapter'
            where book.owner_id = %s and book.document_type = 'book'

            union all

            select 'book', book.id::text, 'paper:' || book.id,
                   book.id, null::bigint, null::uuid
            from public.books as book
            where book.owner_id = %s and book.document_type = 'paper'

            union all

            select 'video', video.id::text, 'video:' || video.id,
                   null::bigint, null::bigint, video.id
            from video.videos as video
            where video.owner_id = %s
        )
        select candidate.*
        from candidate_scopes as candidate
        where (%s::text is null or candidate.source_kind = %s)
          and (%s::text is null or candidate.source_id = %s)
          and not exists (
              select 1 from public.decks as deck
              where deck.owner_id = %s
                and deck.scope_key = candidate.scope_key
                and deck.status in ('ready', 'partial')
          )
          and not exists (
              select 1 from public.deck_jobs as job
              where job.owner_id = %s
                and job.scope_key = candidate.scope_key
                and (
                    (job.automatic_key is not null and job.status <> 'cancelled')
                    or job.status in ('queued', 'running', 'succeeded')
                )
          )
        order by candidate.source_kind, candidate.source_id, candidate.scope_key
        """,
        (
            owner,
            owner,
            owner,
            source_kind,
            source_kind,
            source_identifier,
            source_identifier,
            owner,
            owner,
        ),
    ).fetchall()
    return tuple(
        InitialScope(
            source_kind=row["source_kind"],
            source_id=row["source_id"],
            scope_key=row["scope_key"],
            book_id=row["book_id"],
            node_id=row["node_id"],
            video_id=row["video_id"],
        )
        for row in rows
    )


def list_sources(
    connection: Connection, *, owner_id: str | UUID
) -> list[DeckSourcePreference]:
    """All book and video sources, including ones still being processed."""

    owner = parse_owner_id(owner_id)
    missing_counts: dict[tuple[str, str], int] = {}
    for scope in missing_initial_scopes(connection, owner_id=owner):
        key = (scope.source_kind, scope.source_id)
        missing_counts[key] = missing_counts.get(key, 0) + 1

    rows = connection.execute(
        """
        select 'book' as source_kind, book.id::text as source_id,
               book.title, book.document_type, book.status,
               book.cards_enabled,
               book.cards_automation_eligible_at is not null
                   as automatic_cards_activated,
               exists (
                   select 1 from public.deck_jobs as job
                   where job.owner_id = book.owner_id
                     and (
                         (
                             book.document_type = 'book'
                             and job.automatic_key like
                                 'auto:set1:book:' || book.id || ':%%'
                         )
                         or (
                             book.document_type = 'paper'
                             and job.automatic_key =
                                 'auto:set1:paper:' || book.id
                         )
                     )
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
               video.cards_automation_eligible_at is not null
                   as automatic_cards_activated,
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
    sources: list[DeckSourcePreference] = []
    for row in rows:
        key = (row["source_kind"], row["source_id"])
        missing = missing_counts.get(key, 0)
        ready = row["status"] in {"ready", "degraded"}
        sources.append(
            DeckSourcePreference(
                source_kind=row["source_kind"],
                source_id=row["source_id"],
                title=row["title"] or "Untitled source",
                document_type=row["document_type"],
                status=row["status"],
                cards_enabled=row["cards_enabled"],
                automatic_cards_queued=row["automatic_cards_queued"],
                automatic_cards_activated=row["automatic_cards_activated"],
                missing_automatic_set_count=missing,
                can_activate_automatic_cards=(
                    ready
                    and row["cards_enabled"]
                    and not row["automatic_cards_activated"]
                    and missing > 0
                ),
            )
        )
    return sources


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
            set cards_enabled = %s
            where id = %s and owner_id = %s
            returning id
            """,
            (cards_enabled, identifier, owner),
        ).fetchone()
    elif source_kind == "video":
        identifier = UUID(str(source_id))
        row = connection.execute(
            """
            update video.videos
            set cards_enabled = %s
            where id = %s and owner_id = %s
            returning id
            """,
            (cards_enabled, identifier, owner),
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


def activate_initial_sets(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    source_id: str | int | UUID,
    expected_missing_set_count: int,
) -> int:
    """Explicitly consent to and queue legacy initial cards once."""

    owner = parse_owner_id(owner_id)
    if source_kind == "book":
        try:
            identifier: int | UUID = int(source_id)
        except (TypeError, ValueError) as error:
            raise LookupError("no such source") from error
        source = connection.execute(
            """
            select status, cards_enabled,
                   cards_automation_eligible_at is not null as activated
            from public.books
            where id = %s and owner_id = %s
            for update
            """,
            (identifier, owner),
        ).fetchone()
        ready = source is not None and source["status"] == "ready"
    elif source_kind == "video":
        try:
            identifier = UUID(str(source_id))
        except (TypeError, ValueError) as error:
            raise LookupError("no such source") from error
        source = connection.execute(
            """
            select readiness_status, cards_enabled,
                   cards_automation_eligible_at is not null as activated
            from video.videos
            where id = %s and owner_id = %s
            for update
            """,
            (identifier, owner),
        ).fetchone()
        ready = (
            source is not None
            and source["readiness_status"] in {"ready", "degraded"}
        )
    else:
        raise LookupError("no such source")

    if source is None:
        raise LookupError("no such source")
    if source["activated"]:
        return 0
    if not ready or not source["cards_enabled"]:
        raise SourceActivationConflict("source must be ready and included")

    missing = missing_initial_scopes(
        connection,
        owner_id=owner,
        source_kind=source_kind,
        source_id=identifier,
    )
    if not missing:
        raise SourceActivationConflict("no initial sets are missing")
    if len(missing) != expected_missing_set_count:
        raise SourceActivationConflict("the missing-set preview changed")

    if source_kind == "book":
        connection.execute(
            """
            update public.books
            set cards_automation_eligible_at = now()
            where id = %s and owner_id = %s
            """,
            (identifier, owner),
        )
        return len(
            enqueue_initial_for_book(
                connection, owner_id=owner, book_id=int(identifier)
            )
        )

    connection.execute(
        """
        update video.videos
        set cards_automation_eligible_at = now()
        where id = %s and owner_id = %s
        """,
        (identifier, owner),
    )
    return len(
        enqueue_initial_for_video(
            connection, owner_id=owner, video_id=identifier
        )
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
    """Queue one paper Set 1 or one Set 1 per book chapter."""

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
    ):
        return []

    if source["document_type"] == "paper":
        scope_key = paper_scope_key(book_id)
        if _has_initial_set(connection, owner_id=owner, scope_key=scope_key):
            return []
        return [
            jobs.enqueue(
                connection,
                owner_id=owner,
                source_kind="book",
                scope_key=scope_key,
                book_id=book_id,
                node_id=None,
                automatic_key=f"auto:set1:{scope_key}",
            )
        ]
    if source["document_type"] != "book":
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

    documents = connection.execute(
        """
        select id, owner_id from public.books
        where status = 'ready' and cards_enabled
          and cards_automation_eligible_at is not null
          and (
              (
                  document_type = 'paper'
                  and not exists (
                      select 1 from public.decks as deck
                      where deck.owner_id = books.owner_id
                        and deck.scope_key = 'paper:' || books.id
                        and deck.status in ('ready', 'partial')
                  )
                  and not exists (
                      select 1 from public.deck_jobs as job
                      where job.owner_id = books.owner_id
                        and job.scope_key = 'paper:' || books.id
                        and (
                            (job.automatic_key is not null
                             and job.status <> 'cancelled')
                            or job.status in ('queued', 'running', 'succeeded')
                        )
                  )
              )
              or (
                  document_type = 'book'
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
                                  or job.status in
                                      ('queued', 'running', 'succeeded')
                              )
                        )
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
    for document in documents:
        queued += len(
            enqueue_initial_for_book(
                connection,
                owner_id=document["owner_id"],
                book_id=document["id"],
            )
        )
    for video in videos:
        queued += len(
            enqueue_initial_for_video(
                connection, owner_id=video["owner_id"], video_id=video["id"]
            )
        )
    return queued
