"""Postgres queue operations for video ingestion.

This module is deliberately separate from the PDF worker: the two domains
have different stages, checkpoints, and publication rules.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.states import Stage, Status, TERMINAL


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


def _event(
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
    connection: Connection, *, worker_id: str, lease_seconds: int = 300
) -> VideoIngestionJob | None:
    if not worker_id.strip() or lease_seconds <= 0:
        raise ValueError("worker_id and a positive lease are required")
    with connection.transaction():
        candidate = connection.execute(
            """
            select id, owner_id, status, stage
            from video.ingestion_jobs
            where status in ('queued', 'retry_scheduled')
              and (next_attempt_at is null or next_attempt_at <= now())
              and cancellation_requested_at is null
            order by next_attempt_at nulls first, created_at, id
            for update skip locked limit 1
            """
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
        _event(
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
              and lease_expires_at >= now()
            """,
            (lease_seconds, UUID(str(job_id)), worker_id),
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
        _event(
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
        _event(
            connection,
            owner_id=owner,
            job_id=identifier,
            event_type="retry_requested",
            status=job.status,
            stage=job.stage,
        )
        return job
