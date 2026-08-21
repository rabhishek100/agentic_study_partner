"""The deck generation queue.

Same shape as the ingestion and video queues, for the same reasons: the row is
the queue, the lease, the checkpoint, and the progress source of truth. A
worker that dies leaves a leased row that expires, not a lost deck.

Generating a chapter takes a handful of model calls and a minute or two, which
is long enough that doing it inside a request would tie up a connection and
lose the run on a dropped socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id

DEFAULT_LEASE_SECONDS = 300
# Three attempts, then stop. A deck that fails three times is failing for a
# reason retrying will not fix, and the reader is better served by an error
# they can see than by a queue that churns.
DEFAULT_MAX_ATTEMPTS = 3
# Backoff between attempts. Generation failures are almost always provider
# rate limits, which clear in seconds, not minutes.
RETRY_BACKOFF_SECONDS = 30

LIVE_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class DeckJob:
    id: UUID
    owner_id: UUID
    deck_id: UUID | None
    source_kind: str
    book_id: int | None
    node_id: int | None
    video_id: UUID | None
    scope_key: str
    status: str
    stage: str
    topics_total: int
    topics_done: int
    attempt_count: int
    max_attempts: int
    automatic_key: str | None = None
    generation_mode: str = "topic_generated"
    error_code: str | None = None
    error_detail: str | None = None
    cancellation_requested: bool = False
    title: str = "Deck generation"
    source_title: str = "Source"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def progress_ratio(self) -> float:
        if not self.topics_total:
            return 0.0
        return min(1.0, self.topics_done / self.topics_total)


def _job(row: Any) -> DeckJob:
    return DeckJob(
        id=row["id"],
        owner_id=row["owner_id"],
        deck_id=row["deck_id"],
        source_kind=row["source_kind"],
        book_id=row["book_id"],
        node_id=row["node_id"],
        video_id=row["video_id"],
        scope_key=row["scope_key"],
        generation_mode=row.get("generation_mode", "topic_generated"),
        status=row["status"],
        stage=row["stage"],
        topics_total=row["topics_total"],
        topics_done=row["topics_done"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        automatic_key=row.get("automatic_key"),
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        cancellation_requested=row["cancellation_requested"],
        title=row.get("title") or "Deck generation",
        source_title=row.get("source_title") or "Source",
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


_SELECT = """
    select job.id, job.owner_id, job.deck_id, job.source_kind, job.book_id,
           job.node_id, job.video_id, job.scope_key, job.generation_mode,
           job.status, job.stage, job.topics_total, job.topics_done,
           job.attempt_count, job.max_attempts, job.error_code,
           job.error_detail, job.cancellation_requested, job.automatic_key,
           job.created_at,
           job.updated_at,
           coalesce(deck.title, node.title, video.title, 'Deck generation') as title,
           coalesce(deck.source_title, book.title, video.title, 'Source') as source_title
    from public.deck_jobs as job
    left join public.decks as deck
      on deck.id = job.deck_id and deck.owner_id = job.owner_id
    left join public.nodes as node
      on node.id = job.node_id and node.owner_id = job.owner_id
    left join public.books as book
      on book.id = job.book_id and book.owner_id = job.owner_id
    left join video.videos as video
      on video.id = job.video_id and video.owner_id = job.owner_id
"""


def enqueue(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    scope_key: str,
    generation_mode: str = "topic_generated",
    book_id: int | None = None,
    node_id: int | None = None,
    video_id: str | UUID | None = None,
    automatic_key: str | None = None,
) -> DeckJob:
    """Queue a generation, or return the run already in flight for this scope."""

    owner = parse_owner_id(owner_id)
    # The live-job index protects one scope once a row exists, but automation
    # can race between the publish hook and the periodic reconciler before
    # either insert commits.  A transaction-scoped advisory lock serializes
    # that short check/insert path without holding a table lock.
    connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"deck:{owner}:{scope_key}",),
    )
    if automatic_key is not None:
        automatic = connection.execute(
            _SELECT
            + " where job.owner_id = %s and job.automatic_key = %s limit 1",
            (owner, automatic_key),
        ).fetchone()
        if automatic is not None:
            if automatic["status"] == "cancelled" or automatic[
                "cancellation_requested"
            ]:
                connection.execute(
                    """
                    update public.deck_jobs
                    set status = case
                            when status = 'cancelled' then 'queued'
                            else status
                        end,
                        stage = case
                            when status = 'cancelled' then 'pending'
                            else stage
                        end,
                        cancellation_requested = false,
                        available_at = now(), updated_at = now()
                    where id = %s
                    """,
                    (automatic["id"],),
                )
                return get_job(
                    connection, owner_id=owner, job_id=automatic["id"]
                )
            return _job(automatic)
    # Starting or attaching to a new attempt resolves the old alert for this
    # scope. Preserve every failed row for diagnostics, but do not make a
    # successful retry resurrect its predecessor on the next library refresh.
    connection.execute(
        """
        update public.deck_jobs
        set dismissed_at = coalesce(dismissed_at, now()), updated_at = now()
        where owner_id = %s and scope_key = %s and status = 'failed'
        """,
        (owner, scope_key),
    )
    existing = connection.execute(
        _SELECT
        + """
        where job.owner_id = %s and job.scope_key = %s
          and job.status in ('queued', 'running')
        limit 1
        """,
        (owner, scope_key),
    ).fetchone()
    if existing is not None:
        return _job(existing)

    row = connection.execute(
        """
        insert into public.deck_jobs (
            owner_id, source_kind, book_id, node_id, video_id, scope_key,
            generation_mode, max_attempts, automatic_key
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            owner,
            source_kind,
            book_id,
            node_id,
            UUID(str(video_id)) if video_id else None,
            scope_key,
            generation_mode,
            DEFAULT_MAX_ATTEMPTS,
            automatic_key,
        ),
    ).fetchone()
    return get_job(connection, owner_id=owner, job_id=row["id"])


def get_job(
    connection: Connection, *, owner_id: str | UUID, job_id: str | UUID
) -> DeckJob:
    row = connection.execute(
        _SELECT + " where job.id = %s and job.owner_id = %s",
        (UUID(str(job_id)), parse_owner_id(owner_id)),
    ).fetchone()
    if row is None:
        raise LookupError(f"no deck job {job_id}")
    return _job(row)


def list_jobs(
    connection: Connection, *, owner_id: str | UUID, limit: int = 20
) -> list[DeckJob]:
    """Return current activity and unresolved history.

    A failed attempt stops being actionable once the owner dismisses it or a
    newer attempt exists for the same scope.  The row remains in Postgres for
    diagnostics; it simply stops behaving like a permanent notification.
    """

    rows = connection.execute(
        _SELECT
        + """
        where job.owner_id = %s
          and job.dismissed_at is null
          and job.status in ('queued', 'running', 'failed')
          and (
              job.status <> 'failed'
              or not exists (
                  select 1
                  from public.deck_jobs as newer
                  where newer.owner_id = job.owner_id
                    and newer.scope_key = job.scope_key
                    and newer.created_at > job.created_at
              )
          )
        order by
            case when job.status in ('queued', 'running') then 0 else 1 end,
            job.created_at desc
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()
    return [_job(row) for row in rows]


def dismiss_failed_job(
    connection: Connection, *, owner_id: str | UUID, job_id: str | UUID
) -> bool:
    """Hide one failed job from activity without deleting diagnostic history."""

    row = connection.execute(
        """
        update public.deck_jobs
        set dismissed_at = coalesce(dismissed_at, now()), updated_at = now()
        where id = %s and owner_id = %s and status = 'failed'
        returning id
        """,
        (UUID(str(job_id)), parse_owner_id(owner_id)),
    ).fetchone()
    return row is not None


def live_job_for_scope(
    connection: Connection, *, owner_id: str | UUID, scope_key: str
) -> DeckJob | None:
    row = connection.execute(
        _SELECT
        + " where job.owner_id = %s and job.scope_key = %s"
        " and job.status in ('queued', 'running')",
        (parse_owner_id(owner_id), scope_key),
    ).fetchone()
    return _job(row) if row else None


def claim_next_job(
    connection: Connection,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> DeckJob | None:
    """Claim one queued job under a lease, or return None.

    `for update skip locked` so a second worker can be added later without
    changing any semantics, and the claim commits before the first model call
    so a crash leaves a leased row rather than an open transaction.
    """

    with connection.transaction():
        candidate = connection.execute(
            """
            select id
            from public.deck_jobs
            where status = 'queued'
              and available_at <= now()
              and cancellation_requested = false
            order by available_at, created_at
            for update skip locked
            limit 1
            """,
        ).fetchone()
        if candidate is None:
            return None

        row = connection.execute(
            """
            update public.deck_jobs
            set status = 'running',
                stage = 'inventory',
                worker_id = %s,
                attempt_count = attempt_count + 1,
                lease_expires_at = now() + %s,
                updated_at = now()
            where id = %s
            returning owner_id
            """,
            (worker_id, timedelta(seconds=lease_seconds), candidate["id"]),
        ).fetchone()

    return get_job(connection, owner_id=row["owner_id"], job_id=candidate["id"])


def renew_lease(
    connection: Connection,
    *,
    job_id: UUID,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    result = connection.execute(
        """
        update public.deck_jobs
        set lease_expires_at = now() + %s, updated_at = now()
        where id = %s and worker_id = %s and status = 'running'
        """,
        (timedelta(seconds=lease_seconds), job_id, worker_id),
    )
    return result.rowcount == 1


def reclaim_expired_leases(connection: Connection) -> int:
    """Return jobs whose worker died to the queue."""

    result = connection.execute(
        """
        update public.deck_jobs
        set status = 'queued',
            stage = 'pending',
            worker_id = null,
            lease_expires_at = null,
            available_at = now(),
            updated_at = now()
        where status = 'running'
          and lease_expires_at is not null
          and lease_expires_at < now()
          and attempt_count < max_attempts
        """
    )
    return result.rowcount


def attach_deck(connection: Connection, *, job_id: UUID, deck_id: UUID) -> None:
    connection.execute(
        "update public.deck_jobs set deck_id = %s, updated_at = now() where id = %s",
        (deck_id, job_id),
    )


def record_progress(
    connection: Connection,
    *,
    job_id: UUID,
    stage: str,
    topics_total: int | None = None,
    topics_done: int | None = None,
) -> None:
    connection.execute(
        """
        update public.deck_jobs
        set stage = %s,
            topics_total = coalesce(%s, topics_total),
            topics_done = coalesce(%s, topics_done),
            updated_at = now()
        where id = %s
        """,
        (stage, topics_total, topics_done, job_id),
    )


def finish_job(connection: Connection, *, job_id: UUID, deck_id: UUID) -> None:
    connection.execute(
        """
        update public.deck_jobs
        set status = 'succeeded', stage = 'done', deck_id = %s,
            lease_expires_at = null, updated_at = now()
        where id = %s
        """,
        (deck_id, job_id),
    )


def fail_job(
    connection: Connection,
    *,
    job_id: UUID,
    code: str,
    detail: str = "",
    retryable: bool = True,
) -> None:
    """Requeue with backoff, or give up once the attempts are spent."""

    connection.execute(
        """
        update public.deck_jobs
        set status = case
                when %s and attempt_count < max_attempts then 'queued'
                else 'failed'
            end,
            stage = case
                when %s and attempt_count < max_attempts then 'pending'
                else stage
            end,
            available_at = now() + %s,
            worker_id = null,
            lease_expires_at = null,
            error_code = %s,
            error_detail = %s,
            updated_at = now()
        where id = %s
        """,
        (
            retryable,
            retryable,
            timedelta(seconds=RETRY_BACKOFF_SECONDS),
            code,
            detail[:2_000],
            job_id,
        ),
    )


def request_cancellation(
    connection: Connection, *, owner_id: str | UUID, job_id: str | UUID
) -> None:
    connection.execute(
        """
        update public.deck_jobs
        set cancellation_requested = true,
            status = case when status = 'queued' then 'cancelled' else status end,
            updated_at = now()
        where id = %s and owner_id = %s and status in ('queued', 'running')
        """,
        (UUID(str(job_id)), parse_owner_id(owner_id)),
    )
