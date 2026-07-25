"""Durable ingestion jobs in Postgres.

Postgres is the queue, the checkpoint store, and the progress source of truth.
That keeps one moving part instead of a database plus a broker, and it means a
browser that reconnects days later still sees the real state of its job.

Every function takes the owner it is acting for. The API passes the verified
token subject; the worker passes the owner recorded on the job it claimed.
Only :func:`claim_next_job` and :func:`reclaim_expired_leases` run without an
owner, because a worker legitimately serves every owner's queue.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from .config import IngestionLimits, load_limits
from .errors import ErrorCode, IngestionError, classify_failure, safe_message
from .states import (
    CLAIMABLE_STATUSES,
    PROCESSING_STATUSES,
    Stage,
    Status,
    is_terminal,
    resume_step,
    validate_transition,
)


COLUMNS = """
    id, owner_id, idempotency_key, status, stage, document_class,
    storage_bucket, storage_path, original_filename, declared_content_type,
    declared_size_bytes, verified_size_bytes, file_hash, page_count, book_id,
    progress_completed, progress_total, progress_unit, attempt_count,
    max_attempts, next_attempt_at, lease_owner, lease_expires_at,
    heartbeat_at, cancellation_requested_at, last_error_code,
    last_error_message, last_error_retryable, provenance_json,
    created_at, started_at, updated_at, completed_at
"""

MAXIMUM_FILENAME_LENGTH = 255


class JobNotFoundError(LookupError):
    """No such job for this owner.

    Callers turn this into 404 whether the job is missing or belongs to
    somebody else, so job IDs cannot be probed.
    """


class JobConflictError(RuntimeError):
    """The job moved on before this write landed."""


@dataclass(frozen=True)
class IngestionJob:
    """One row of ``ingestion_jobs``."""

    id: UUID
    owner_id: UUID
    idempotency_key: UUID
    status: Status
    stage: Stage | None
    document_class: str | None
    storage_bucket: str
    storage_path: str
    original_filename: str
    declared_content_type: str | None
    declared_size_bytes: int | None
    verified_size_bytes: int | None
    file_hash: str | None
    page_count: int | None
    book_id: int | None
    progress_completed: int
    progress_total: int | None
    progress_unit: str | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    cancellation_requested_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    last_error_retryable: bool | None
    provenance: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "IngestionJob":
        return cls(
            id=row["id"],
            owner_id=row["owner_id"],
            idempotency_key=row["idempotency_key"],
            status=Status(row["status"]),
            stage=Stage(row["stage"]) if row["stage"] else None,
            document_class=row["document_class"],
            storage_bucket=row["storage_bucket"],
            storage_path=row["storage_path"],
            original_filename=row["original_filename"],
            declared_content_type=row["declared_content_type"],
            declared_size_bytes=row["declared_size_bytes"],
            verified_size_bytes=row["verified_size_bytes"],
            file_hash=row["file_hash"],
            page_count=row["page_count"],
            book_id=row["book_id"],
            progress_completed=row["progress_completed"],
            progress_total=row["progress_total"],
            progress_unit=row["progress_unit"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            next_attempt_at=row["next_attempt_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            heartbeat_at=row["heartbeat_at"],
            cancellation_requested_at=row["cancellation_requested_at"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            last_error_retryable=row["last_error_retryable"],
            provenance=row["provenance_json"] or {},
            created_at=row["created_at"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    @property
    def cancellation_requested(self) -> bool:
        return self.cancellation_requested_at is not None

    @property
    def progress_percent(self) -> float | None:
        if not self.progress_total:
            return None
        return round(100.0 * self.progress_completed / self.progress_total, 2)


def display_filename(value: str) -> str:
    """Return a filename safe to store and echo back.

    The original name is display metadata only: it never becomes part of a
    Storage path, so stripping directory components here is about not showing
    a user a confusing string, not about path traversal.
    """

    cleaned = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(character for character in cleaned if character.isprintable())
    if not cleaned:
        raise IngestionError(
            ErrorCode.UNSUPPORTED_CONTENT_TYPE, detail="empty original filename"
        )
    return cleaned[:MAXIMUM_FILENAME_LENGTH]


def _fetch(connection: Connection, statement: str, parameters: tuple) -> IngestionJob:
    row = connection.execute(statement, parameters).fetchone()
    if row is None:
        raise JobNotFoundError("ingestion job does not exist")
    return IngestionJob.from_row(row)


def append_event(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    event_type: str,
    status: Status | str | None = None,
    stage: Stage | str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record one important transition.

    Important means status changes, stage boundaries, and terminal outcomes,
    not one row per page, block, or percentage point.
    """

    connection.execute(
        """
        insert into ingestion_job_events (
            job_id, owner_id, event_type, status, stage, message, metadata_json
        ) values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            UUID(str(job_id)),
            parse_owner_id(owner_id),
            event_type,
            str(status) if status else None,
            str(stage) if stage else None,
            message,
            Jsonb(metadata or {}),
        ),
    )


def list_events(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select id, event_type, status, stage, message, metadata_json, created_at
        from ingestion_job_events
        where owner_id = %s and job_id = %s
        order by id
        limit %s
        """,
        (parse_owner_id(owner_id), UUID(str(job_id)), limit),
    ).fetchall()


def create_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    idempotency_key: str | UUID,
    original_filename: str,
    content_type: str | None,
    content_length: int | None,
    limits: IngestionLimits | None = None,
) -> tuple[IngestionJob, bool]:
    """Create an ingestion job and reserve its immutable Storage path.

    Returns the job and whether this call created it. Repeating a request with
    the same owner and idempotency key returns the original job instead of
    reserving a second path, so a retried POST cannot orphan an upload.
    """

    limits = limits or load_limits()
    owner = parse_owner_id(owner_id)
    key = UUID(str(idempotency_key))
    filename = display_filename(original_filename)

    if content_type is not None and content_type not in limits.allowed_content_types:
        raise IngestionError(
            ErrorCode.UNSUPPORTED_CONTENT_TYPE,
            detail=f"declared content type {content_type!r}",
        )
    if content_length is not None:
        if content_length <= 0:
            raise IngestionError(
                ErrorCode.INVALID_PDF, detail="declared content length must be positive"
            )
        if content_length > limits.max_source_bytes:
            raise IngestionError(
                ErrorCode.SOURCE_TOO_LARGE,
                detail=f"declared {content_length} bytes",
            )

    with connection.transaction():
        existing = connection.execute(
            f"select {COLUMNS} from ingestion_jobs "
            "where owner_id = %s and idempotency_key = %s",
            (owner, key),
        ).fetchone()
        if existing is not None:
            return IngestionJob.from_row(existing), False

        # Everything not yet handed to the worker counts against the quota, so
        # abandoned uploads cannot accumulate unbounded reserved paths.
        pending = connection.execute(
            """
            select count(*) as pending from ingestion_jobs
            where owner_id = %s
              and status in ('awaiting_upload', 'queued', 'retry_scheduled')
            """,
            (owner,),
        ).fetchone()["pending"]
        if pending >= limits.max_queued_jobs_per_owner:
            raise IngestionError(
                ErrorCode.QUOTA_EXCEEDED,
                detail=f"{pending} pending jobs for this owner",
            )

        job_id = uuid4()
        row = connection.execute(
            f"""
            insert into ingestion_jobs (
                id, owner_id, idempotency_key, status, storage_bucket,
                storage_path, original_filename, declared_content_type,
                declared_size_bytes, max_attempts
            ) values (%s, %s, %s, 'awaiting_upload', %s, %s, %s, %s, %s, %s)
            returning {COLUMNS}
            """,
            (
                job_id,
                owner,
                key,
                limits.source_bucket,
                limits.storage_path(owner, job_id),
                filename,
                content_type,
                content_length,
                limits.max_attempts,
            ),
        ).fetchone()
        append_event(
            connection,
            owner_id=owner,
            job_id=job_id,
            event_type="created",
            status=Status.AWAITING_UPLOAD,
            metadata={"declared_size_bytes": content_length},
        )
    return IngestionJob.from_row(row), True


def get_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
) -> IngestionJob:
    return _fetch(
        connection,
        f"select {COLUMNS} from ingestion_jobs where id = %s and owner_id = %s",
        (UUID(str(job_id)), parse_owner_id(owner_id)),
    )


def list_jobs(
    connection: Connection,
    *,
    owner_id: str | UUID,
    limit: int = 20,
) -> list[IngestionJob]:
    rows = connection.execute(
        f"""
        select {COLUMNS} from ingestion_jobs
        where owner_id = %s
        order by created_at desc, id
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()
    return [IngestionJob.from_row(row) for row in rows]


def _transition(
    connection: Connection,
    *,
    owner_id: UUID,
    job_id: UUID,
    expected: Status,
    target: Status,
    assignments: str = "",
    parameters: tuple = (),
) -> IngestionJob:
    """Apply one validated status change with optimistic concurrency.

    The ``where status = expected`` clause is what makes a worker that lost its
    lease unable to overwrite the decision of whoever took over.
    """

    validate_transition(expected, target)
    clause = f", {assignments}" if assignments else ""
    row = connection.execute(
        f"""
        update ingestion_jobs
        set status = %s{clause}
        where id = %s and owner_id = %s and status = %s
        returning {COLUMNS}
        """,
        (str(target), *parameters, job_id, owner_id, str(expected)),
    ).fetchone()
    if row is None:
        raise JobConflictError(
            f"ingestion job {job_id} was not in status {expected}"
        )
    return IngestionJob.from_row(row)


def mark_upload_complete(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    verified_size_bytes: int,
    limits: IngestionLimits | None = None,
) -> IngestionJob:
    """Queue a job whose source object has been verified in Storage.

    Idempotent: repeating it for a job that is already queued, running, or
    finished returns the current job rather than failing, because a client
    retrying a dropped response must not break its own ingestion.
    """

    limits = limits or load_limits()
    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))

    with connection.transaction():
        job = get_job(connection, owner_id=owner, job_id=identifier)
        if job.status is not Status.AWAITING_UPLOAD:
            return job
        if verified_size_bytes > limits.max_source_bytes:
            raise IngestionError(
                ErrorCode.SOURCE_TOO_LARGE,
                detail=f"stored object is {verified_size_bytes} bytes",
            )
        queued = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=Status.AWAITING_UPLOAD,
            target=Status.QUEUED,
            assignments="verified_size_bytes = %s, next_attempt_at = now()",
            parameters=(verified_size_bytes,),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="queued",
            status=Status.QUEUED,
            metadata={"verified_size_bytes": verified_size_bytes},
        )
    return queued


def request_cancellation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
) -> IngestionJob:
    """Cancel a job, cooperatively when it is already running.

    A job that has not reached the worker is cancelled immediately. A running
    job is only flagged; the worker stops at its next safe boundary so it never
    abandons a half-written canonical import.
    """

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))

    with connection.transaction():
        job = get_job(connection, owner_id=owner, job_id=identifier)
        if is_terminal(job.status):
            return job

        if job.status in PROCESSING_STATUSES:
            row = connection.execute(
                f"""
                update ingestion_jobs
                set cancellation_requested_at = coalesce(
                    cancellation_requested_at, now()
                )
                where id = %s and owner_id = %s
                returning {COLUMNS}
                """,
                (identifier, owner),
            ).fetchone()
            append_event(
                connection,
                owner_id=owner,
                job_id=identifier,
                event_type="cancellation_requested",
                status=job.status,
                stage=job.stage,
            )
            return IngestionJob.from_row(row)

        cancelled = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=job.status,
            target=Status.CANCELLED,
            assignments=(
                "cancellation_requested_at = coalesce("
                "cancellation_requested_at, now()), "
                "completed_at = now(), last_error_code = %s, "
                "last_error_message = %s, last_error_retryable = false"
            ),
            parameters=(
                str(ErrorCode.CANCELLED),
                safe_message(ErrorCode.CANCELLED),
            ),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="cancelled",
            status=Status.CANCELLED,
        )
    return cancelled


def retry_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    limits: IngestionLimits | None = None,
) -> IngestionJob:
    """Re-queue a failed job whose failure was marked retryable.

    The attempt budget is reset because a person decided to try again; the
    original Storage object is reused, and the worker resumes from whatever
    checkpoints are still valid.
    """

    limits = limits or load_limits()
    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))

    with connection.transaction():
        job = get_job(connection, owner_id=owner, job_id=identifier)
        if job.status is not Status.FAILED:
            raise JobConflictError("only a failed ingestion job can be retried")
        if not job.last_error_retryable:
            raise IngestionError(
                ErrorCode.ATTEMPTS_EXHAUSTED,
                detail=f"failure {job.last_error_code} is not retryable",
            )
        queued = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=Status.FAILED,
            target=Status.QUEUED,
            assignments=(
                "attempt_count = 0, max_attempts = %s, next_attempt_at = now(), "
                "completed_at = null, last_error_code = null, "
                "last_error_message = null, last_error_retryable = null"
            ),
            parameters=(limits.max_attempts,),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="retry_requested",
            status=Status.QUEUED,
            stage=job.stage,
        )
    return queued


def claim_next_job(
    connection: Connection,
    *,
    worker_id: str,
    limits: IngestionLimits | None = None,
) -> IngestionJob | None:
    """Claim one eligible job, or return None when the queue is empty.

    The claim is a short transaction that commits before any download, parse,
    or provider call, so a crashed worker leaves a leased row rather than an
    open transaction. ``FOR UPDATE SKIP LOCKED`` means a second worker can be
    added later without changing any job semantics.
    """

    limits = limits or load_limits()
    with connection.transaction():
        candidate = connection.execute(
            f"""
            select id, owner_id, status, stage, attempt_count
            from ingestion_jobs as job
            where status in ({",".join("%s" for _ in CLAIMABLE_STATUSES)})
              and (next_attempt_at is null or next_attempt_at <= now())
              and cancellation_requested_at is null
              -- One job per owner may occupy the worker at a time; the same
              -- rule the partial unique index enforces on writes.
              and not exists (
                  select 1 from ingestion_jobs as active
                  where active.owner_id = job.owner_id
                    and active.status in (
                        {",".join("%s" for _ in PROCESSING_STATUSES)}
                    )
              )
            order by next_attempt_at nulls first, created_at, id
            for update skip locked
            limit 1
            """,
            (
                *(str(status) for status in sorted(CLAIMABLE_STATUSES)),
                *(str(status) for status in sorted(PROCESSING_STATUSES)),
            ),
        ).fetchone()
        if candidate is None:
            return None

        status, stage = resume_step(candidate["stage"])
        claimed = _transition(
            connection,
            owner_id=candidate["owner_id"],
            job_id=candidate["id"],
            expected=Status(candidate["status"]),
            target=status,
            assignments=(
                "stage = %s, lease_owner = %s, "
                "lease_expires_at = now() + make_interval(secs => %s), "
                "heartbeat_at = now(), attempt_count = attempt_count + 1, "
                "started_at = coalesce(started_at, now()), next_attempt_at = null"
            ),
            parameters=(str(stage), worker_id, limits.lease_seconds),
        )
        append_event(
            connection,
            owner_id=claimed.owner_id,
            job_id=claimed.id,
            event_type="claimed",
            status=status,
            stage=stage,
            metadata={"attempt": claimed.attempt_count, "worker": worker_id},
        )
    return claimed


def renew_lease(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    limits: IngestionLimits | None = None,
) -> bool:
    """Extend this worker's lease. False means the job was taken away."""

    limits = limits or load_limits()
    updated = connection.execute(
        """
        update ingestion_jobs
        set lease_expires_at = now() + make_interval(secs => %s),
            heartbeat_at = now()
        where id = %s and lease_owner = %s
        """,
        (limits.lease_seconds, UUID(str(job_id)), worker_id),
    ).rowcount
    return bool(updated)


def reclaim_expired_leases(
    connection: Connection,
    *,
    limits: IngestionLimits | None = None,
) -> int:
    """Return crashed jobs to the queue.

    Reclamation never assumes the previous attempt did nothing. Stages are
    idempotent and guarded by unique constraints and content hashes, so the
    retried attempt re-runs its stage and converges on the same book.
    """

    limits = limits or load_limits()
    expired = connection.execute(
        f"""
        select id, owner_id, status, stage, attempt_count, max_attempts
        from ingestion_jobs
        where lease_expires_at is not null
          and lease_expires_at < now()
          and status in ({",".join("%s" for _ in PROCESSING_STATUSES)})
        for update skip locked
        """,
        tuple(str(status) for status in sorted(PROCESSING_STATUSES)),
    ).fetchall()

    for row in expired:
        error = IngestionError(
            ErrorCode.LEASE_EXPIRED, detail=f"lease lost during {row['stage']}"
        )
        decision = classify_failure(
            error,
            attempt=row["attempt_count"],
            max_attempts=row["max_attempts"],
            job_id=str(row["id"]),
        )
        if decision.retry:
            schedule_retry(
                connection,
                owner_id=row["owner_id"],
                job_id=row["id"],
                current_status=Status(row["status"]),
                error=error,
                delay_seconds=decision.delay_seconds,
            )
        else:
            fail_job(
                connection,
                owner_id=row["owner_id"],
                job_id=row["id"],
                current_status=Status(row["status"]),
                error=error,
            )
    return len(expired)


def record_progress(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    completed: int,
    total: int | None = None,
    unit: str | None = None,
) -> None:
    """Publish progress and a heartbeat without an event row per tick."""

    connection.execute(
        """
        update ingestion_jobs
        set progress_completed = %s,
            progress_total = coalesce(%s, progress_total),
            progress_unit = coalesce(%s, progress_unit),
            heartbeat_at = now()
        where id = %s and owner_id = %s
        """,
        (
            max(0, completed),
            total,
            unit,
            UUID(str(job_id)),
            parse_owner_id(owner_id),
        ),
    )


def set_stage(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    current_status: Status,
    stage: Stage,
    provenance: dict[str, Any] | None = None,
    **columns: Any,
) -> IngestionJob:
    """Move within a status, for stages that share one lifecycle state.

    Validation covers verifying the upload, downloading it, and preflight.
    Those are separate units of work but one lifecycle state, so this records
    the stage without pretending a status transition happened.
    """

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))
    assignments = ["stage = %s"]
    parameters: list[Any] = [str(stage)]

    if provenance:
        assignments.append("provenance_json = provenance_json || %s")
        parameters.append(Jsonb(provenance))
    for column, value in columns.items():
        assignments.append(f"{column} = %s")
        parameters.append(value)

    row = connection.execute(
        f"""
        update ingestion_jobs
        set {", ".join(assignments)}
        where id = %s and owner_id = %s and status = %s
        returning {COLUMNS}
        """,
        (*parameters, identifier, owner, str(current_status)),
    ).fetchone()
    if row is None:
        raise JobConflictError(
            f"ingestion job {identifier} was not in status {current_status}"
        )
    return IngestionJob.from_row(row)


def advance_stage(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    current_status: Status,
    status: Status,
    stage: Stage,
    provenance: dict[str, Any] | None = None,
    reset_progress: bool = True,
    **columns: Any,
) -> IngestionJob:
    """Move a running job to its next stage and record stage provenance."""

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))
    assignments = ["stage = %s"]
    parameters: list[Any] = [str(stage)]

    if reset_progress:
        assignments.append("progress_completed = 0, progress_total = null")
    if provenance:
        assignments.append("provenance_json = provenance_json || %s")
        parameters.append(Jsonb(provenance))
    for column, value in columns.items():
        assignments.append(f"{column} = %s")
        parameters.append(value)

    with connection.transaction():
        job = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=current_status,
            target=status,
            assignments=", ".join(assignments),
            parameters=tuple(parameters),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="stage_started",
            status=status,
            stage=stage,
        )
    return job


def complete_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    current_status: Status,
    book_id: int,
    duplicate_of: int | None = None,
) -> IngestionJob:
    """Publish a verified book and finish the job."""

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))
    with connection.transaction():
        job = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=current_status,
            target=Status.READY,
            assignments=(
                "book_id = %s, stage = null, completed_at = now(), "
                "lease_owner = null, lease_expires_at = null, "
                "last_error_code = null, last_error_message = null, "
                "last_error_retryable = null"
            ),
            parameters=(book_id,),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="ready",
            status=Status.READY,
            metadata={"book_id": book_id, "duplicate_of": duplicate_of},
        )
    return job


def schedule_retry(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    current_status: Status,
    error: IngestionError,
    delay_seconds: float,
) -> IngestionJob:
    """Park a job until its backoff expires, remembering where it stopped."""

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))
    with connection.transaction():
        job = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=current_status,
            target=Status.RETRY_SCHEDULED,
            assignments=(
                # The backoff deadline is computed by the database, because the
                # claim query compares it against the database clock. A worker
                # host whose clock drifts must not stall or jump the queue.
                "next_attempt_at = now() + make_interval(secs => %s), "
                "lease_owner = null, lease_expires_at = null, "
                "last_error_code = %s, last_error_message = %s, "
                "last_error_retryable = true"
            ),
            parameters=(
                max(0.0, float(delay_seconds)),
                str(error.code),
                error.safe_message,
            ),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="retry_scheduled",
            status=Status.RETRY_SCHEDULED,
            stage=job.stage,
            message=error.safe_message,
            metadata={"code": str(error.code), "delay_seconds": round(delay_seconds)},
        )
    return job


def fail_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    current_status: Status,
    error: IngestionError,
) -> IngestionJob:
    """Finish a job with a stable code and a message that is safe to show."""

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))
    with connection.transaction():
        job = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=current_status,
            target=Status.FAILED,
            assignments=(
                "completed_at = now(), lease_owner = null, "
                "lease_expires_at = null, last_error_code = %s, "
                "last_error_message = %s, last_error_retryable = %s"
            ),
            parameters=(str(error.code), error.safe_message, error.retryable),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="failed",
            status=Status.FAILED,
            stage=job.stage,
            message=error.safe_message,
            metadata={"code": str(error.code), "retryable": error.retryable},
        )
    return job


def cancel_running_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    current_status: Status,
) -> IngestionJob:
    """Finish a job the worker stopped because cancellation was requested."""

    owner = parse_owner_id(owner_id)
    identifier = UUID(str(job_id))
    with connection.transaction():
        job = _transition(
            connection,
            owner_id=owner,
            job_id=identifier,
            expected=current_status,
            target=Status.CANCELLED,
            assignments=(
                "completed_at = now(), lease_owner = null, "
                "lease_expires_at = null, last_error_code = %s, "
                "last_error_message = %s, last_error_retryable = false"
            ),
            parameters=(
                str(ErrorCode.CANCELLED),
                safe_message(ErrorCode.CANCELLED),
            ),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="cancelled",
            status=Status.CANCELLED,
            stage=job.stage,
        )
    return job
