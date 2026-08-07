"""Postgres queue operations for video ingestion.

This module is deliberately separate from the PDF worker: the two domains
have different stages, checkpoints, and publication rules.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
import re
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.errors import SAFE_MESSAGES, VideoBudgetExceeded, VideoErrorCode
from video.states import PIPELINE, Stage, Status, TERMINAL


JOB_COLUMNS = """
    id, owner_id, video_id, target_version_id, idempotency_key, status, stage,
    progress_completed, progress_total, progress_unit, attempt_count,
    max_attempts, next_attempt_at, lease_owner, lease_expires_at, heartbeat_at,
    cancellation_requested_at, last_error_code, last_error_message,
    last_error_retryable, staging_storage_backend, staging_storage_key,
    declared_size_bytes, declared_media_type, staging_size_bytes,
    staging_content_hash, upload_completed_at, cost_cap_usd, actual_cost_usd,
    provenance_json, created_at, started_at, updated_at, completed_at
"""


class VideoJobNotFoundError(LookupError):
    pass


class VideoJobConflictError(RuntimeError):
    pass


CHECKPOINT_HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StageCheckpoint:
    id: UUID
    ingestion_version_id: UUID
    stage: Stage
    status: str
    dependency_hash: str
    output_manifest: dict[str, Any]
    actual_cost_usd: Decimal
    attempt_count: int
    reused: bool = False


def _checkpoint(row: dict[str, Any], *, reused: bool = False) -> StageCheckpoint:
    return StageCheckpoint(
        id=row["id"],
        ingestion_version_id=row["ingestion_version_id"],
        stage=Stage(row["stage"]),
        status=row["status"],
        dependency_hash=row["dependency_hash"],
        output_manifest=row["output_manifest_json"] or {},
        actual_cost_usd=row["actual_cost_usd"],
        attempt_count=row["attempt_count"],
        reused=reused,
    )


@dataclass(frozen=True)
class VideoIngestionJob:
    id: UUID
    owner_id: UUID
    video_id: UUID
    target_version_id: UUID
    idempotency_key: UUID
    status: Status
    stage: Stage | None
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
    staging_storage_backend: str | None
    staging_storage_key: str | None
    declared_size_bytes: int | None
    declared_media_type: str | None
    staging_size_bytes: int | None
    staging_content_hash: str | None
    upload_completed_at: datetime | None
    cost_cap_usd: Decimal
    actual_cost_usd: Decimal
    provenance: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "VideoIngestionJob":
        values = dict(row)
        values["status"] = Status(values["status"])
        values["stage"] = Stage(values["stage"]) if values["stage"] else None
        values["provenance"] = values.pop("provenance_json") or {}
        return cls(**values)

    @property
    def cancellation_requested(self) -> bool:
        return self.cancellation_requested_at is not None


def append_event(
    connection: Connection,
    *,
    owner_id: UUID,
    job_id: UUID,
    event_type: str,
    status: Status,
    stage: Stage | None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        insert into video.ingestion_job_events (
            owner_id, job_id, event_type, status, stage, message, metadata_json
        ) values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            owner_id,
            job_id,
            event_type,
            str(status),
            str(stage) if stage else None,
            message,
            Jsonb(metadata or {}),
        ),
    )


def get_job(
    connection: Connection, *, owner_id: str | UUID, job_id: str | UUID
) -> VideoIngestionJob:
    row = connection.execute(
        f"select {JOB_COLUMNS} from video.ingestion_jobs where owner_id = %s and id = %s",
        (parse_owner_id(owner_id), UUID(str(job_id))),
    ).fetchone()
    if row is None:
        raise VideoJobNotFoundError("video ingestion job not found")
    return VideoIngestionJob.from_row(row)


def claim_next_job(
    connection: Connection,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    supported_stages: Collection[Stage] | None = None,
) -> VideoIngestionJob | None:
    if not worker_id.strip() or lease_seconds <= 0:
        raise ValueError("worker_id and a positive lease are required")
    stages = (
        None
        if supported_stages is None
        else tuple(str(Stage(stage)) for stage in supported_stages)
    )
    if stages == ():
        return None
    stage_filter = "" if stages is None else "and stage = any(%s)"
    parameters: tuple[Any, ...] = () if stages is None else (list(stages),)
    with connection.transaction():
        candidate = connection.execute(
            f"""
            select id, owner_id, status, stage
            from video.ingestion_jobs
            where status in ('queued', 'retry_scheduled')
              and (next_attempt_at is null or next_attempt_at <= now())
              and cancellation_requested_at is null
              {stage_filter}
            order by next_attempt_at nulls first, created_at, id
            for update skip locked limit 1
            """,
            parameters,
        ).fetchone()
        if candidate is None:
            return None
        row = connection.execute(
            f"""
            update video.ingestion_jobs
            set status = 'running', attempt_count = attempt_count + 1,
                lease_owner = %s,
                lease_expires_at = now() + make_interval(secs => %s),
                heartbeat_at = now(), started_at = coalesce(started_at, now()),
                next_attempt_at = null
            where id = %s and owner_id = %s and status = %s
            returning {JOB_COLUMNS}
            """,
            (
                worker_id,
                lease_seconds,
                candidate["id"],
                candidate["owner_id"],
                candidate["status"],
            ),
        ).fetchone()
        if row is None:
            return None
        job = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            event_type="claimed",
            status=job.status,
            stage=job.stage,
            metadata={"attempt": job.attempt_count, "worker": worker_id},
        )
        return job


def renew_lease(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    lease_seconds: int = 300,
) -> bool:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    return bool(
        connection.execute(
            """
            update video.ingestion_jobs
            set lease_expires_at = now() + make_interval(secs => %s),
                heartbeat_at = now()
            where id = %s and status = 'running' and lease_owner = %s
              and attempt_count = %s
              and lease_expires_at >= now()
            """,
            (lease_seconds, UUID(str(job_id)), worker_id, attempt_count),
        ).rowcount
    )


def request_cancellation(
    connection: Connection, *, owner_id: str | UUID, job_id: str | UUID
) -> VideoIngestionJob:
    owner, identifier = parse_owner_id(owner_id), UUID(str(job_id))
    with connection.transaction():
        current = get_job(connection, owner_id=owner, job_id=identifier)
        if current.status in TERMINAL:
            if current.status is Status.CANCELLED:
                return current
            raise VideoJobConflictError("completed video ingestion cannot be cancelled")
        if current.status is Status.RUNNING:
            row = connection.execute(
                f"""
                update video.ingestion_jobs
                set cancellation_requested_at = coalesce(
                    cancellation_requested_at, now()
                )
                where owner_id = %s and id = %s and status = 'running'
                returning {JOB_COLUMNS}
                """,
                (owner, identifier),
            ).fetchone()
            event_type = "cancellation_requested"
        else:
            row = connection.execute(
                f"""
                update video.ingestion_jobs
                set status = 'cancelled', completed_at = now(),
                    cancellation_requested_at = coalesce(
                        cancellation_requested_at, now()
                    ), next_attempt_at = null, lease_owner = null,
                    lease_expires_at = null
                where owner_id = %s and id = %s
                  and status in ('awaiting_upload', 'queued', 'retry_scheduled')
                returning {JOB_COLUMNS}
                """,
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise VideoJobConflictError("video ingestion moved before cancellation")
            connection.execute(
                """
                update video.ingestion_versions
                set status = 'cancelled', completed_at = now(),
                    error_code = 'cancelled', error_message = 'Video ingestion cancelled'
                where owner_id = %s and id = %s and status = 'building'
                """,
                (owner, current.target_version_id),
            )
            connection.execute(
                """
                update video.videos
                set readiness_status = 'failed'
                where owner_id = %s and id = %s
                  and current_ingestion_version_id is null
                """,
                (owner, current.video_id),
            )
            event_type = "cancelled"
        job = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type=event_type,
            status=job.status,
            stage=job.stage,
        )
        return job


def retry_job(
    connection: Connection, *, owner_id: str | UUID, job_id: str | UUID
) -> VideoIngestionJob:
    owner, identifier = parse_owner_id(owner_id), UUID(str(job_id))
    with connection.transaction():
        current = get_job(connection, owner_id=owner, job_id=identifier)
        if current.status is not Status.FAILED or not current.last_error_retryable:
            raise VideoJobConflictError("only a retryable failed video ingestion can retry")
        connection.execute(
            """
            update video.ingestion_versions
            set status = 'building', completed_at = null, published_at = null,
                error_code = null, error_message = null
            where owner_id = %s and id = %s and status = 'failed'
            """,
            (owner, current.target_version_id),
        )
        row = connection.execute(
            f"""
            update video.ingestion_jobs
            set status = 'retry_scheduled', attempt_count = 0,
                next_attempt_at = now(), completed_at = null,
                lease_owner = null, lease_expires_at = null, heartbeat_at = null,
                cancellation_requested_at = null, last_error_code = null,
                last_error_message = null, last_error_retryable = null
            where owner_id = %s and id = %s and status = 'failed'
            returning {JOB_COLUMNS}
            """,
            (owner, identifier),
        ).fetchone()
        if row is None:
            raise VideoJobConflictError("video ingestion moved before retry")
        job = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="retry_requested",
            status=job.status,
            stage=job.stage,
        )
        return job


def _locked_running_job(
    connection: Connection, *, job_id: UUID, worker_id: str, attempt_count: int
) -> VideoIngestionJob:
    row = connection.execute(
        f"""
        select {JOB_COLUMNS} from video.ingestion_jobs
        where id = %s and status = 'running' and lease_owner = %s
          and attempt_count = %s
          and lease_expires_at >= now()
        for update
        """,
        (job_id, worker_id, attempt_count),
    ).fetchone()
    if row is None:
        raise VideoJobConflictError("video worker no longer owns this job")
    return VideoIngestionJob.from_row(row)


def begin_stage_checkpoint(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    stage: Stage,
    dependency_hash: str,
) -> StageCheckpoint:
    """Begin a stage or reuse its exact completed dependency checkpoint."""

    stage = Stage(stage)
    if not CHECKPOINT_HASH.fullmatch(dependency_hash):
        raise ValueError("dependency_hash must be a SHA-256 hex digest")
    identifier = UUID(str(job_id))
    with connection.transaction():
        job = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if job.stage is not stage:
            raise VideoJobConflictError("video job is not at the requested stage")
        existing = connection.execute(
            """
            select * from video.ingestion_stage_checkpoints
            where owner_id = %s and ingestion_version_id = %s and stage = %s
            for update
            """,
            (job.owner_id, job.target_version_id, str(stage)),
        ).fetchone()
        if (
            existing is not None
            and existing["status"] == "complete"
            and existing["dependency_hash"] == dependency_hash
        ):
            return _checkpoint(existing, reused=True)
        if existing is None:
            row = connection.execute(
                """
                insert into video.ingestion_stage_checkpoints (
                    owner_id, video_id, ingestion_version_id, stage, status,
                    dependency_hash, attempt_count, started_at
                ) values (%s, %s, %s, %s, 'running', %s, 1, now())
                returning *
                """,
                (
                    job.owner_id,
                    job.video_id,
                    job.target_version_id,
                    str(stage),
                    dependency_hash,
                ),
            ).fetchone()
        else:
            row = connection.execute(
                """
                update video.ingestion_stage_checkpoints
                set status = 'running', dependency_hash = %s,
                    output_manifest_json = '{}'::jsonb,
                    provenance_json = '{}'::jsonb, actual_cost_usd = 0,
                    attempt_count = attempt_count + 1,
                    reused_from_checkpoint_id = null, started_at = now(),
                    completed_at = null, error_code = null, error_message = null
                where id = %s returning *
                """,
                (dependency_hash, existing["id"]),
            ).fetchone()
        return _checkpoint(row)


def complete_stage_checkpoint(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    stage: Stage,
    dependency_hash: str,
    output_manifest: dict[str, Any],
    cost_usd: Decimal | str | float = 0,
) -> StageCheckpoint:
    """Commit one stage and charge its provider-reported cost exactly once."""

    stage, identifier = Stage(stage), UUID(str(job_id))
    cost = Decimal(str(cost_usd))
    if cost < 0:
        raise ValueError("stage cost cannot be negative")
    with connection.transaction():
        job = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if job.stage is not stage:
            raise VideoJobConflictError("video job is not at the requested stage")
        checkpoint = connection.execute(
            """
            select * from video.ingestion_stage_checkpoints
            where owner_id = %s and ingestion_version_id = %s and stage = %s
            for update
            """,
            (job.owner_id, job.target_version_id, str(stage)),
        ).fetchone()
        if checkpoint is None or checkpoint["dependency_hash"] != dependency_hash:
            raise VideoJobConflictError("video stage checkpoint dependency changed")
        if checkpoint["status"] == "complete":
            return _checkpoint(checkpoint, reused=True)
        if checkpoint["status"] != "running":
            raise VideoJobConflictError("video stage checkpoint is not running")
        charged_job = connection.execute(
            """
            update video.ingestion_jobs
            set actual_cost_usd = actual_cost_usd + %s
            where id = %s and owner_id = %s
              and actual_cost_usd + %s <= cost_cap_usd
            returning id
            """,
            (cost, identifier, job.owner_id, cost),
        ).fetchone()
        charged_version = connection.execute(
            """
            update video.ingestion_versions
            set actual_cost_usd = actual_cost_usd + %s
            where id = %s and owner_id = %s
              and actual_cost_usd + %s <= cost_cap_usd
            returning id
            """,
            (cost, job.target_version_id, job.owner_id, cost),
        ).fetchone()
        if charged_job is None or charged_version is None:
            raise VideoBudgetExceeded()
        row = connection.execute(
            """
            update video.ingestion_stage_checkpoints
            set status = 'complete', output_manifest_json = %s,
                actual_cost_usd = %s, completed_at = now(),
                error_code = null, error_message = null
            where id = %s returning *
            """,
            (Jsonb(output_manifest), cost, checkpoint["id"]),
        ).fetchone()
        append_event(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            event_type="stage_completed",
            status=job.status,
            stage=stage,
        )
        return _checkpoint(row)


def advance_stage(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    next_stage: Stage,
) -> VideoIngestionJob:
    next_stage, identifier = Stage(next_stage), UUID(str(job_id))
    with connection.transaction():
        job = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if job.stage is None:
            raise VideoJobConflictError("video job has no active stage")
        position = PIPELINE.index(job.stage)
        if position + 1 >= len(PIPELINE) or PIPELINE[position + 1] is not next_stage:
            raise VideoJobConflictError("video stages must advance in order")
        completed = connection.execute(
            """
            select 1 from video.ingestion_stage_checkpoints
            where owner_id = %s and ingestion_version_id = %s
              and stage = %s and status = 'complete'
            """,
            (job.owner_id, job.target_version_id, str(job.stage)),
        ).fetchone()
        if completed is None:
            raise VideoJobConflictError("current video stage is not complete")
        row = connection.execute(
            f"""
            update video.ingestion_jobs
            set stage = %s, progress_completed = 0,
                progress_total = null, progress_unit = null
            where id = %s and owner_id = %s and status = 'running'
              and lease_owner = %s and lease_expires_at >= now()
              and attempt_count = %s
            returning {JOB_COLUMNS}
            """,
            (str(next_stage), identifier, job.owner_id, worker_id, attempt_count),
        ).fetchone()
        advanced = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            event_type="stage_started",
            status=advanced.status,
            stage=next_stage,
        )
        return advanced


def release_claim(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
) -> VideoIngestionJob:
    """Return an advanced job to the queue without weakening its lease fence."""

    identifier = UUID(str(job_id))
    with connection.transaction():
        current = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if current.cancellation_requested:
            raise VideoJobConflictError("video ingestion cancellation is pending")
        row = connection.execute(
            f"""
            update video.ingestion_jobs
            set status = 'queued', next_attempt_at = now(),
                lease_owner = null, lease_expires_at = null,
                heartbeat_at = null
            where id = %s and owner_id = %s and status = 'running'
              and lease_owner = %s and attempt_count = %s
              and lease_expires_at >= now()
              and cancellation_requested_at is null
            returning {JOB_COLUMNS}
            """,
            (
                identifier,
                current.owner_id,
                worker_id,
                attempt_count,
            ),
        ).fetchone()
        if row is None:
            raise VideoJobConflictError("video worker no longer owns this job")
        released = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=released.owner_id,
            job_id=released.id,
            event_type="stage_queued",
            status=released.status,
            stage=released.stage,
        )
        return released


def finish_running_cancellation(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
) -> VideoIngestionJob:
    """Cooperatively finish a cancellation requested on a running job."""

    identifier = UUID(str(job_id))
    with connection.transaction():
        current = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if not current.cancellation_requested:
            raise VideoJobConflictError("video ingestion was not cancelled")
        connection.execute(
            """
            update video.ingestion_stage_checkpoints
            set status = 'failed', completed_at = now(),
                error_code = 'cancelled',
                error_message = 'Video ingestion cancelled'
            where owner_id = %s and ingestion_version_id = %s
              and stage = %s and status = 'running'
            """,
            (current.owner_id, current.target_version_id, str(current.stage)),
        )
        connection.execute(
            """
            update video.ingestion_versions
            set status = 'cancelled', completed_at = now(),
                error_code = 'cancelled',
                error_message = 'Video ingestion cancelled'
            where owner_id = %s and id = %s and status = 'building'
            """,
            (current.owner_id, current.target_version_id),
        )
        connection.execute(
            """
            update video.videos set readiness_status = 'failed'
            where owner_id = %s and id = %s
              and current_ingestion_version_id is null
            """,
            (current.owner_id, current.video_id),
        )
        row = connection.execute(
            f"""
            update video.ingestion_jobs
            set status = 'cancelled', completed_at = now(),
                next_attempt_at = null, lease_owner = null,
                lease_expires_at = null, heartbeat_at = null,
                last_error_code = 'cancelled',
                last_error_message = 'Video ingestion cancelled',
                last_error_retryable = false
            where id = %s and owner_id = %s and status = 'running'
              and lease_owner = %s and attempt_count = %s
            returning {JOB_COLUMNS}
            """,
            (identifier, current.owner_id, worker_id, attempt_count),
        ).fetchone()
        cancelled = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=cancelled.owner_id,
            job_id=cancelled.id,
            event_type="cancelled",
            status=cancelled.status,
            stage=cancelled.stage,
        )
        return cancelled


def record_stage_failure(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    code: VideoErrorCode,
    retryable: bool,
    retry_delay_seconds: float = 30,
) -> VideoIngestionJob:
    """Persist a safe stage failure and either retry or terminate the job."""

    if retry_delay_seconds < 0:
        raise ValueError("retry delay cannot be negative")
    identifier, cause = UUID(str(job_id)), VideoErrorCode(code)
    with connection.transaction():
        current = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if current.cancellation_requested:
            raise VideoJobConflictError("video ingestion cancellation is pending")
        will_retry = retryable and current.attempt_count < current.max_attempts
        recorded_code = (
            cause
            if will_retry or not retryable
            else VideoErrorCode.ATTEMPTS_EXHAUSTED
        )
        safe_message = SAFE_MESSAGES[recorded_code]
        connection.execute(
            """
            update video.ingestion_stage_checkpoints
            set status = 'failed', completed_at = now(),
                error_code = %s, error_message = %s
            where owner_id = %s and ingestion_version_id = %s
              and stage = %s and status = 'running'
            """,
            (
                str(recorded_code),
                safe_message,
                current.owner_id,
                current.target_version_id,
                str(current.stage),
            ),
        )
        if will_retry:
            row = connection.execute(
                f"""
                update video.ingestion_jobs
                set status = 'retry_scheduled',
                    next_attempt_at = now() + make_interval(secs => %s),
                    lease_owner = null, lease_expires_at = null,
                    heartbeat_at = null, last_error_code = %s,
                    last_error_message = %s, last_error_retryable = true
                where id = %s and owner_id = %s and status = 'running'
                  and lease_owner = %s and attempt_count = %s
                returning {JOB_COLUMNS}
                """,
                (
                    retry_delay_seconds,
                    str(recorded_code),
                    safe_message,
                    identifier,
                    current.owner_id,
                    worker_id,
                    attempt_count,
                ),
            ).fetchone()
            event_type = "retry_scheduled"
        else:
            row = connection.execute(
                f"""
                update video.ingestion_jobs
                set status = 'failed', completed_at = now(),
                    next_attempt_at = null, lease_owner = null,
                    lease_expires_at = null, heartbeat_at = null,
                    last_error_code = %s, last_error_message = %s,
                    last_error_retryable = false
                where id = %s and owner_id = %s and status = 'running'
                  and lease_owner = %s and attempt_count = %s
                returning {JOB_COLUMNS}
                """,
                (
                    str(recorded_code),
                    safe_message,
                    identifier,
                    current.owner_id,
                    worker_id,
                    attempt_count,
                ),
            ).fetchone()
            connection.execute(
                """
                update video.ingestion_versions
                set status = 'failed', completed_at = now(),
                    error_code = %s, error_message = %s
                where owner_id = %s and id = %s and status = 'building'
                """,
                (
                    str(recorded_code),
                    safe_message,
                    current.owner_id,
                    current.target_version_id,
                ),
            )
            connection.execute(
                """
                update video.videos set readiness_status = 'failed'
                where owner_id = %s and id = %s
                  and current_ingestion_version_id is null
                """,
                (current.owner_id, current.video_id),
            )
            event_type = "failed"
        failed = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=failed.owner_id,
            job_id=failed.id,
            event_type=event_type,
            status=failed.status,
            stage=failed.stage,
            message=safe_message,
            metadata={"cause": str(cause)},
        )
        return failed


def publish_job(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    readiness: str,
) -> VideoIngestionJob:
    """Atomically make a quality-gated ingestion version queryable."""

    if readiness not in {"ready", "degraded"}:
        raise ValueError("published video readiness must be ready or degraded")
    identifier = UUID(str(job_id))
    with connection.transaction():
        current = _locked_running_job(
            connection,
            job_id=identifier,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        if current.stage is not Stage.PUBLISH:
            raise VideoJobConflictError("video job is not at the publish stage")
        checkpoint = connection.execute(
            """
            select status from video.ingestion_stage_checkpoints
            where owner_id = %s and ingestion_version_id = %s
              and stage = 'publish'
            """,
            (current.owner_id, current.target_version_id),
        ).fetchone()
        if checkpoint is None or checkpoint["status"] != "complete":
            raise VideoJobConflictError("video publish checkpoint is incomplete")
        version = connection.execute(
            """
            update video.ingestion_versions
            set status = %s, completed_at = now(), published_at = now(),
                error_code = null, error_message = null
            where id = %s and owner_id = %s and status = 'building'
              and quality_gates_json <> '{}'::jsonb
            returning id
            """,
            (readiness, current.target_version_id, current.owner_id),
        ).fetchone()
        if version is None:
            raise VideoJobConflictError("video version cannot be published")
        connection.execute(
            """
            update video.videos
            set readiness_status = %s, current_ingestion_version_id = %s,
                ready_at = now()
            where id = %s and owner_id = %s
            """,
            (
                readiness,
                current.target_version_id,
                current.video_id,
                current.owner_id,
            ),
        )
        row = connection.execute(
            f"""
            update video.ingestion_jobs
            set status = 'ready', stage = null, completed_at = now(),
                next_attempt_at = null, lease_owner = null,
                lease_expires_at = null, heartbeat_at = null,
                last_error_code = null, last_error_message = null,
                last_error_retryable = null
            where id = %s and owner_id = %s and status = 'running'
              and lease_owner = %s and attempt_count = %s
            returning {JOB_COLUMNS}
            """,
            (identifier, current.owner_id, worker_id, attempt_count),
        ).fetchone()
        published = VideoIngestionJob.from_row(row)
        append_event(
            connection,
            owner_id=published.owner_id,
            job_id=published.id,
            event_type="published",
            status=published.status,
            stage=None,
            metadata={
                "readiness": readiness,
                "ingestion_version_id": str(published.target_version_id),
            },
        )
        return published


def reclaim_expired_leases(
    connection: Connection, *, retry_delay_seconds: float = 30
) -> int:
    """Retry abandoned work until the attempt budget is exhausted."""

    if retry_delay_seconds < 0:
        raise ValueError("retry delay cannot be negative")
    with connection.transaction():
        expired = connection.execute(
            """
            select * from video.ingestion_jobs
            where status = 'running' and lease_expires_at < now()
            for update skip locked
            """
        ).fetchall()
        for row in expired:
            job = VideoIngestionJob.from_row(row)
            connection.execute(
                """
                update video.ingestion_stage_checkpoints
                set status = 'failed', completed_at = now(),
                    error_code = 'lease_expired',
                    error_message = 'Video processing lease expired'
                where owner_id = %s and ingestion_version_id = %s
                  and stage = %s and status = 'running'
                """,
                (job.owner_id, job.target_version_id, str(job.stage)),
            )
            if job.attempt_count < job.max_attempts:
                connection.execute(
                    """
                    update video.ingestion_jobs
                    set status = 'retry_scheduled',
                        next_attempt_at = now() + make_interval(secs => %s),
                        lease_owner = null, lease_expires_at = null,
                        last_error_code = %s, last_error_message = %s,
                        last_error_retryable = true
                    where id = %s and owner_id = %s
                    """,
                    (
                        retry_delay_seconds,
                        str(VideoErrorCode.LEASE_EXPIRED),
                        "Video processing stalled and will resume.",
                        job.id,
                        job.owner_id,
                    ),
                )
                event_type, target = "retry_scheduled", Status.RETRY_SCHEDULED
            else:
                connection.execute(
                    """
                    update video.ingestion_jobs
                    set status = 'failed', completed_at = now(),
                        next_attempt_at = null,
                        lease_owner = null, lease_expires_at = null,
                        last_error_code = %s, last_error_message = %s,
                        last_error_retryable = false
                    where id = %s and owner_id = %s
                    """,
                    (
                        str(VideoErrorCode.ATTEMPTS_EXHAUSTED),
                        "Video processing failed repeatedly.",
                        job.id,
                        job.owner_id,
                    ),
                )
                connection.execute(
                    """
                    update video.ingestion_versions
                    set status = 'failed', completed_at = now(),
                        error_code = %s, error_message = %s
                    where id = %s and owner_id = %s and status = 'building'
                    """,
                    (
                        str(VideoErrorCode.ATTEMPTS_EXHAUSTED),
                        "Video processing failed repeatedly.",
                        job.target_version_id,
                        job.owner_id,
                    ),
                )
                connection.execute(
                    """
                    update video.videos set readiness_status = 'failed'
                    where id = %s and owner_id = %s
                      and current_ingestion_version_id is null
                    """,
                    (job.video_id, job.owner_id),
                )
                event_type, target = "failed", Status.FAILED
            append_event(
                connection,
                owner_id=job.owner_id,
                job_id=job.id,
                event_type=event_type,
                status=target,
                stage=job.stage,
            )
        return len(expired)
