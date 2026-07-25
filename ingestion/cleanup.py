"""Retention for abandoned uploads and finished-job sources.

Ready books keep their original PDFs for recovery and rebuild. Everything
else ages out on a configurable schedule that the worker runs between polls:

- A job still ``awaiting_upload`` after the abandonment window is cancelled
  and its reserved path is released, so quota is not consumed forever by an
  upload that never finished.
- A failed or cancelled job keeps its source object through the retention
  window, so a manual retry can reuse it, then loses the object while the
  job row itself is kept as history.

Every deletion is recorded on the job, both as an event and as a provenance
marker, because a missing object must be explainable afterwards.
"""

from dataclasses import dataclass
import logging

from psycopg import Connection
from psycopg.types.json import Jsonb

from .config import IngestionLimits, load_limits
from .errors import ErrorCode, IngestionError
from .jobs import append_event
from .states import Status
from .storage_objects import delete_object


logger = logging.getLogger("study_partner.ingestion.cleanup")


@dataclass(frozen=True)
class CleanupSummary:
    """What one cleanup pass did."""

    abandoned_uploads_cancelled: int = 0
    expired_sources_deleted: int = 0
    deletions_failed: int = 0

    @property
    def total(self) -> int:
        return self.abandoned_uploads_cancelled + self.expired_sources_deleted


def _delete_source(connection: Connection, row) -> bool:
    """Remove one job's source object and record that it happened."""

    try:
        delete_object(row["storage_bucket"], row["storage_path"])
    except IngestionError as error:
        # Storage being down must not fail the pass; the job stays eligible
        # and the next pass tries again.
        logger.warning(
            "source deletion failed for job %s: %s", row["id"], error.code
        )
        return False

    connection.execute(
        """
        update ingestion_jobs
        set provenance_json = provenance_json
            || jsonb_build_object('source_deleted_at', now()::text)
        where id = %s
        """,
        (row["id"],),
    )
    append_event(
        connection,
        owner_id=row["owner_id"],
        job_id=row["id"],
        event_type="source_deleted",
        status=row["status"],
        message="The uploaded file was removed by the retention policy.",
    )
    return True


def cancel_abandoned_uploads(
    connection: Connection,
    *,
    limits: IngestionLimits,
) -> tuple[int, int]:
    """Cancel reservations whose upload never completed.

    Returns cancelled count and failed-deletion count. The object may or may
    not exist (the browser can vanish before or after sending bytes), so a
    missing object is fine while an unreachable Storage keeps the job alive
    for the next pass.
    """

    rows = connection.execute(
        """
        select id, owner_id, status, storage_bucket, storage_path
        from ingestion_jobs
        where status = %s
          and created_at < now() - make_interval(hours => %s)
        order by created_at
        for update skip locked
        """,
        (str(Status.AWAITING_UPLOAD), limits.abandoned_upload_hours),
    ).fetchall()

    cancelled = 0
    failed = 0
    for row in rows:
        try:
            delete_object(row["storage_bucket"], row["storage_path"])
        except IngestionError:
            failed += 1
            continue
        connection.execute(
            """
            update ingestion_jobs
            set status = %s, completed_at = now(),
                cancellation_requested_at = coalesce(
                    cancellation_requested_at, now()
                ),
                last_error_code = %s, last_error_message = %s,
                last_error_retryable = false,
                provenance_json = provenance_json
                    || jsonb_build_object('source_deleted_at', now()::text)
            where id = %s and status = %s
            """,
            (
                str(Status.CANCELLED),
                str(ErrorCode.CANCELLED),
                "This upload was never completed and has been cancelled.",
                row["id"],
                str(Status.AWAITING_UPLOAD),
            ),
        )
        append_event(
            connection,
            owner_id=row["owner_id"],
            job_id=row["id"],
            event_type="abandoned_upload_cancelled",
            status=Status.CANCELLED,
            message="The upload was never completed.",
        )
        cancelled += 1
    return cancelled, failed


def delete_expired_sources(
    connection: Connection,
    *,
    limits: IngestionLimits,
) -> tuple[int, int]:
    """Remove source objects of failed and cancelled jobs past retention.

    Returns deleted count and failed-deletion count. Ready jobs are never
    touched: a ready book's source stays for recovery and rebuild.
    """

    rows = connection.execute(
        """
        select id, owner_id, status, storage_bucket, storage_path
        from ingestion_jobs
        where status in (%s, %s)
          and completed_at < now() - make_interval(days => %s)
          and not provenance_json ? 'source_deleted_at'
        order by completed_at
        for update skip locked
        """,
        (
            str(Status.FAILED),
            str(Status.CANCELLED),
            limits.source_retention_days,
        ),
    ).fetchall()

    deleted = 0
    failed = 0
    for row in rows:
        if _delete_source(connection, row):
            deleted += 1
        else:
            failed += 1
    return deleted, failed


def run_cleanup(
    connection: Connection,
    *,
    limits: IngestionLimits | None = None,
) -> CleanupSummary:
    """Run one full retention pass. Safe to repeat and safe to interrupt."""

    limits = limits or load_limits()
    with connection.transaction():
        cancelled, cancel_failures = cancel_abandoned_uploads(
            connection, limits=limits
        )
    with connection.transaction():
        deleted, delete_failures = delete_expired_sources(
            connection, limits=limits
        )

    summary = CleanupSummary(
        abandoned_uploads_cancelled=cancelled,
        expired_sources_deleted=deleted,
        deletions_failed=cancel_failures + delete_failures,
    )
    if summary.total or summary.deletions_failed:
        logger.info(
            "cleanup pass: %s abandoned uploads cancelled, %s sources deleted, "
            "%s deletions failed",
            summary.abandoned_uploads_cancelled,
            summary.expired_sources_deleted,
            summary.deletions_failed,
        )
    return summary
