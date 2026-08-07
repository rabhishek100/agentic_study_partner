"""Retention for video media that nothing points at any more.

Books have had this since their first release; video has had nothing, which is
survivable at one lecture and a liability at ten. Video is also the worse case
of the two: a book's source is one PDF in Storage, while a lecture is a video
file, its metadata, its captions, and hundreds of frames and crops on a volume
sized for a handful of lectures. Nothing here reclaimed any of it.

Four passes, in the order their evidence becomes reliable:

- A job still ``awaiting_upload`` after the abandonment window is cancelled and
  its reserved staging object released, so a browser that vanished mid-upload
  does not hold the volume forever.
- A staging object whose bytes have already been promoted into canonical
  storage is a duplicate of a file that is now content-addressed, and is
  removed once the canonical object is confirmed present. This is the one that
  matters most today: every uploaded lecture has been stored twice since the
  acquisition stage was written, and neither copy was ever released.
- A failed or cancelled job keeps its staging object through the retention
  window, so a manual retry can reuse it, then loses the object while the job
  row itself is kept as history.
- Anything on the volume that no surviving row names is deleted, because
  deleting a video, a version, or an account removes rows and leaves bytes.

Every deletion a job can be attached to is recorded on that job, as an event
and as a provenance marker, because a missing object must be explainable
afterwards. The orphan sweep has no job to attach to and logs each key
individually instead, for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import time
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from video.jobs import append_event
from video.media_store import FilesystemMediaStore, MediaStoreError
from video.repository import OWNER_MEDIA_KEYS
from video.states import Stage, Status


logger = logging.getLogger("study_partner.video.cleanup")

DEFAULT_ABANDONED_UPLOAD_HOURS = 24
DEFAULT_STAGING_RETENTION_DAYS = 7
# How long an unreferenced object must have sat still before the orphan sweep
# will touch it. Media is written to the volume *before* the row that names it
# commits, so without a grace window the sweep would race every ingest and
# delete files a stage was about to reference. Anything this pipeline writes
# reaches its row within seconds; a day of slack costs nothing but removes the
# race entirely.
DEFAULT_ORPHAN_GRACE_HOURS = 24
# A bound on one sweep, so a misconfiguration — a worker pointed at the wrong
# database, or at a volume belonging to another environment — deletes a
# bounded amount and says so, rather than emptying the volume before anyone
# notices. A sweep that hits this reports what it left behind.
MAXIMUM_ORPHAN_DELETIONS_PER_SWEEP = 500
# Partial uploads written by MediaWriter. They are named so they cannot
# collide with a storage key, and they are abandoned rather than cleaned up
# when a worker is killed mid-write.
PARTIAL_SUFFIX = ".part"
PARTIAL_PREFIX = "."


def _hours(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class VideoRetentionLimits:
    """One immutable snapshot of the configured video retention windows."""

    abandoned_upload_hours: int = DEFAULT_ABANDONED_UPLOAD_HOURS
    staging_retention_days: int = DEFAULT_STAGING_RETENTION_DAYS
    orphan_grace_hours: int = DEFAULT_ORPHAN_GRACE_HOURS


def load_retention_limits() -> VideoRetentionLimits:
    return VideoRetentionLimits(
        abandoned_upload_hours=_hours(
            "VIDEO_ABANDONED_UPLOAD_HOURS", DEFAULT_ABANDONED_UPLOAD_HOURS
        ),
        staging_retention_days=_hours(
            "VIDEO_STAGING_RETENTION_DAYS", DEFAULT_STAGING_RETENTION_DAYS
        ),
        orphan_grace_hours=_hours(
            "VIDEO_MEDIA_ORPHAN_GRACE_HOURS", DEFAULT_ORPHAN_GRACE_HOURS
        ),
    )


@dataclass(frozen=True)
class VideoCleanupSummary:
    """What one video retention pass did."""

    abandoned_uploads_cancelled: int = 0
    promoted_staging_deleted: int = 0
    expired_staging_deleted: int = 0
    orphaned_objects_deleted: int = 0
    partial_uploads_deleted: int = 0
    bytes_reclaimed: int = 0
    deletions_failed: int = 0

    @property
    def total(self) -> int:
        return (
            self.abandoned_uploads_cancelled
            + self.promoted_staging_deleted
            + self.expired_staging_deleted
            + self.orphaned_objects_deleted
            + self.partial_uploads_deleted
        )


def _release_staging(
    connection: Connection,
    store: FilesystemMediaStore,
    row,
    *,
    event_type: str,
    message: str,
) -> tuple[bool, int]:
    """Unlink one job's staging object and record that it happened.

    Returns whether the row was settled and how many bytes came back. A store
    that is unreachable must not fail the pass: the job keeps its marker-free
    provenance and the next pass tries again.
    """

    size = _size_of(
        store, owner_id=row["owner_id"], storage_key=row["staging_storage_key"]
    )
    try:
        store.remove(
            owner_id=row["owner_id"], storage_key=row["staging_storage_key"]
        )
    except MediaStoreError as error:
        logger.warning(
            "video staging deletion failed for job %s at %s: %s",
            row["id"],
            row["staging_storage_key"],
            error,
        )
        return False, 0
    connection.execute(
        """
        update video.ingestion_jobs
        set provenance_json = provenance_json
            || jsonb_build_object('staging_deleted_at', now()::text)
        where id = %s and owner_id = %s
        """,
        (row["id"], row["owner_id"]),
    )
    append_event(
        connection,
        owner_id=row["owner_id"],
        job_id=row["id"],
        event_type=event_type,
        status=Status(row["status"]),
        stage=Stage(row["stage"]) if row["stage"] else None,
        message=message,
    )
    return True, size


def _size_of(
    store: FilesystemMediaStore, *, owner_id: UUID, storage_key: str
) -> int:
    """Bytes an object is about to give back, or zero if it is already gone."""

    try:
        path = store.open_path(owner_id=owner_id, storage_key=storage_key)
        return path.stat().st_size
    except (FileNotFoundError, MediaStoreError, OSError):
        return 0


def cancel_abandoned_uploads(
    connection: Connection,
    store: FilesystemMediaStore,
    *,
    limits: VideoRetentionLimits,
) -> tuple[int, int, int]:
    """Cancel reservations whose upload never completed.

    Returns cancelled count, bytes reclaimed, and failed-deletion count. The
    object may or may not exist — a browser can vanish before or after sending
    bytes — so a missing object is fine, while an unwritable volume keeps the
    job alive for the next pass.
    """

    rows = connection.execute(
        """
        select id, owner_id, video_id, status, stage, staging_storage_key
        from video.ingestion_jobs
        where status = %s
          and staging_storage_key is not null
          and created_at < now() - make_interval(hours => %s)
        order by created_at
        for update skip locked
        """,
        (str(Status.AWAITING_UPLOAD), limits.abandoned_upload_hours),
    ).fetchall()

    cancelled = 0
    reclaimed = 0
    failed = 0
    for row in rows:
        size = _size_of(
            store, owner_id=row["owner_id"], storage_key=row["staging_storage_key"]
        )
        try:
            store.remove(
                owner_id=row["owner_id"], storage_key=row["staging_storage_key"]
            )
        except MediaStoreError as error:
            failed += 1
            logger.warning(
                "abandoned video upload %s could not be released at %s: %s",
                row["id"],
                row["staging_storage_key"],
                error,
            )
            continue
        connection.execute(
            """
            update video.ingestion_jobs
            set status = %s, completed_at = now(),
                cancellation_requested_at = coalesce(
                    cancellation_requested_at, now()
                ),
                last_error_code = %s, last_error_message = %s,
                last_error_retryable = false,
                provenance_json = provenance_json
                    || jsonb_build_object('staging_deleted_at', now()::text)
            where id = %s and status = %s
            """,
            (
                str(Status.CANCELLED),
                "cancelled",
                "This upload was never completed and has been cancelled.",
                row["id"],
                str(Status.AWAITING_UPLOAD),
            ),
        )
        # Mirrors the running-job cancellation: a video whose only ingestion
        # never started has no version to fall back to, and leaving it at
        # `pending` shows the reader a card that will never finish loading.
        connection.execute(
            """
            update video.videos set readiness_status = 'failed'
            where owner_id = %s and id = %s
              and current_ingestion_version_id is null
            """,
            (row["owner_id"], row["video_id"]),
        )
        append_event(
            connection,
            owner_id=row["owner_id"],
            job_id=row["id"],
            event_type="abandoned_upload_cancelled",
            status=Status.CANCELLED,
            stage=Stage(row["stage"]) if row["stage"] else None,
            message="The upload was never completed.",
        )
        cancelled += 1
        reclaimed += size
    return cancelled, reclaimed, failed


def delete_promoted_staging(
    connection: Connection,
    store: FilesystemMediaStore,
) -> tuple[int, int, int]:
    """Remove staging copies whose bytes now live in canonical storage.

    The acquisition stage imports an uploaded file into content-addressed
    canonical storage and leaves the staging copy where it was, so every
    uploaded lecture has occupied the volume twice. The canonical object is the
    retained copy — it is what the reader plays and what every rebuild reads —
    so the staging duplicate has no remaining purpose once it exists.

    Only a job that reached ``ready`` qualifies, and there is no window: a
    finished job will not read its staging object again. A failed job is left
    to `delete_expired_staging` instead, because its retry re-runs the
    acquisition stage, and that stage reads the staging object rather than the
    canonical one.

    Deleted only after confirming the canonical object is actually on the
    volume. The source row naming it is not enough: a row can outlive its bytes
    if a previous sweep or a manual mistake removed them, and in that case the
    staging copy is the only surviving version of the file.

    Returns deleted count, bytes reclaimed, and failed-deletion count.
    """

    rows = connection.execute(
        """
        select job.id, job.owner_id, job.status, job.stage,
               job.staging_storage_key, source.storage_key as canonical_key
        from video.ingestion_jobs as job
        join video.video_sources as source
          on source.owner_id = job.owner_id and source.video_id = job.video_id
         and source.is_primary
        where job.staging_storage_key is not null
          and source.storage_key is not null
          and source.storage_key <> job.staging_storage_key
          and job.status = %s
          and not job.provenance_json ? 'staging_deleted_at'
        order by job.created_at
        for update of job skip locked
        """,
        (str(Status.READY),),
    ).fetchall()

    deleted = 0
    reclaimed = 0
    failed = 0
    for row in rows:
        try:
            store.open_path(
                owner_id=row["owner_id"], storage_key=row["canonical_key"]
            )
        except (FileNotFoundError, MediaStoreError):
            # The row says the file was promoted and the volume disagrees.
            # The staging copy is then the only copy, and deleting it would
            # lose the source outright.
            logger.warning(
                "video job %s keeps its staging object: canonical %s is missing",
                row["id"],
                row["canonical_key"],
            )
            continue
        settled, size = _release_staging(
            connection,
            store,
            row,
            event_type="staging_source_deleted",
            message=(
                "The uploaded file was released after it was stored "
                "canonically."
            ),
        )
        if settled:
            deleted += 1
            reclaimed += size
        else:
            failed += 1
    return deleted, reclaimed, failed


def delete_expired_staging(
    connection: Connection,
    store: FilesystemMediaStore,
    *,
    limits: VideoRetentionLimits,
) -> tuple[int, int, int]:
    """Remove staging objects of failed and cancelled jobs past retention.

    A failed job keeps its upload through the window so a retry can reuse it
    without asking the reader to send gigabytes a second time. Returns deleted
    count, bytes reclaimed, and failed-deletion count.
    """

    rows = connection.execute(
        """
        select id, owner_id, status, stage, staging_storage_key
        from video.ingestion_jobs
        where status in (%s, %s)
          and staging_storage_key is not null
          and completed_at < now() - make_interval(days => %s)
          and not provenance_json ? 'staging_deleted_at'
        order by completed_at
        for update skip locked
        """,
        (
            str(Status.FAILED),
            str(Status.CANCELLED),
            limits.staging_retention_days,
        ),
    ).fetchall()

    deleted = 0
    reclaimed = 0
    failed = 0
    for row in rows:
        settled, size = _release_staging(
            connection,
            store,
            row,
            event_type="staging_source_deleted",
            message="The uploaded file was removed by the retention policy.",
        )
        if settled:
            deleted += 1
            reclaimed += size
        else:
            failed += 1
    return deleted, reclaimed, failed


def _referenced_keys(connection: Connection, *, owner_id: UUID) -> set[str]:
    """Every storage key this owner still has a row for."""

    return {
        row["key"]
        for row in connection.execute(
            OWNER_MEDIA_KEYS, {"owner": owner_id}
        ).fetchall()
        if row["key"]
    }


def _owner_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    directories = []
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            parse_owner_id(entry.name)
        except ValueError:
            # Not an owner prefix. This root belongs to video media and
            # nothing else writes here, but a stray directory is not this
            # sweep's to delete.
            logger.warning("video media root holds a non-owner entry: %s", entry.name)
            continue
        directories.append(entry)
    return directories


def delete_orphaned_media(
    connection: Connection,
    store: FilesystemMediaStore,
    *,
    limits: VideoRetentionLimits,
) -> tuple[int, int, int, int]:
    """Remove volume objects that no surviving row names.

    Deleting a video cascades its rows and reports the keys it stranded, but
    that report is only useful to a caller that is still running: an unlink
    that fails, a worker killed between the commit and the unlink, or an
    account deleted straight out of the database all leave bytes with nothing
    pointing at them. On a volume sized for video, nothing else would ever
    reclaim them.

    Returns orphans deleted, partial uploads deleted, bytes reclaimed, and
    failed-deletion count.
    """

    root = store.root
    deleted = 0
    partials = 0
    reclaimed = 0
    failed = 0
    budget = MAXIMUM_ORPHAN_DELETIONS_PER_SWEEP
    skipped = 0
    cutoff = limits.orphan_grace_hours * 3600

    for owner_directory in _owner_directories(root):
        owner = parse_owner_id(owner_directory.name)
        referenced = _referenced_keys(connection, owner_id=owner)
        for path in sorted(owner_directory.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                age = _age_seconds(path)
            except OSError:
                continue
            if age < cutoff:
                # Written too recently to be sure its row has committed.
                continue
            is_partial = path.name.startswith(PARTIAL_PREFIX) and path.name.endswith(
                PARTIAL_SUFFIX
            )
            key = path.relative_to(root).as_posix()
            if not is_partial and key in referenced:
                continue
            if budget <= 0:
                skipped += 1
                continue
            size = path.stat().st_size if path.exists() else 0
            if is_partial:
                # Not a storage key, so the store cannot address it. An
                # interrupted write leaves one of these behind and nothing
                # else ever looks at it again.
                try:
                    path.unlink()
                except OSError as error:
                    failed += 1
                    logger.warning(
                        "could not delete abandoned partial upload %s: %s", key, error
                    )
                    continue
                partials += 1
                reclaimed += size
                budget -= 1
                continue
            try:
                removed = store.remove(owner_id=owner, storage_key=key)
            except MediaStoreError as error:
                # Named, not counted. A deletion failing every hour cannot be
                # diagnosed from a total, and this sweep is the one pass with
                # no job row to attach the failure to.
                failed += 1
                logger.warning("orphan sweep could not delete %s: %s", key, error)
                continue
            if not removed:
                continue
            deleted += 1
            reclaimed += size
            budget -= 1
            # Logged individually: this is the one deletion path with no job
            # row to attach an event to, so the log is the only record that it
            # happened.
            logger.warning(
                "deleted orphaned video object %s (%s bytes; no row references it)",
                key,
                size,
            )

    if skipped:
        logger.warning(
            "orphan sweep stopped after %s deletions; %s more objects remain "
            "unreferenced and will be reclaimed by the next pass",
            MAXIMUM_ORPHAN_DELETIONS_PER_SWEEP,
            skipped,
        )
    return deleted, partials, reclaimed, failed


def _age_seconds(path: Path) -> float:
    return max(0.0, time.time() - path.stat().st_mtime)


def run_video_cleanup(
    connection: Connection,
    *,
    store: FilesystemMediaStore | None = None,
    limits: VideoRetentionLimits | None = None,
) -> VideoCleanupSummary:
    """Run one full video retention pass. Safe to repeat and to interrupt."""

    limits = limits or load_retention_limits()
    store = store or FilesystemMediaStore()

    with connection.transaction():
        cancelled, cancelled_bytes, cancel_failures = cancel_abandoned_uploads(
            connection, store, limits=limits
        )
    with connection.transaction():
        promoted, promoted_bytes, promoted_failures = delete_promoted_staging(
            connection, store
        )
    with connection.transaction():
        expired, expired_bytes, expired_failures = delete_expired_staging(
            connection, store, limits=limits
        )
    # Runs last so anything the job-driven passes just released is already
    # absent from the rows this sweep compares the volume against.
    orphans, partials, orphan_bytes, orphan_failures = delete_orphaned_media(
        connection, store, limits=limits
    )

    summary = VideoCleanupSummary(
        abandoned_uploads_cancelled=cancelled,
        promoted_staging_deleted=promoted,
        expired_staging_deleted=expired,
        orphaned_objects_deleted=orphans,
        partial_uploads_deleted=partials,
        bytes_reclaimed=(
            cancelled_bytes + promoted_bytes + expired_bytes + orphan_bytes
        ),
        deletions_failed=(
            cancel_failures
            + promoted_failures
            + expired_failures
            + orphan_failures
        ),
    )
    if summary.total or summary.deletions_failed:
        logger.log(
            logging.WARNING if summary.deletions_failed else logging.INFO,
            "video cleanup pass: %s abandoned uploads cancelled, %s promoted "
            "staging objects deleted, %s expired staging objects deleted, %s "
            "orphaned objects deleted, %s partial uploads deleted, %s bytes "
            "reclaimed, %s deletions failed",
            summary.abandoned_uploads_cancelled,
            summary.promoted_staging_deleted,
            summary.expired_staging_deleted,
            summary.orphaned_objects_deleted,
            summary.partial_uploads_deleted,
            summary.bytes_reclaimed,
            summary.deletions_failed,
        )
    return summary
