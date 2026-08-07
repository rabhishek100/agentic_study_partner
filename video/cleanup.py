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

Two things make the orphan pass safe to run unattended, which is how it runs:
its reference set includes objects named only inside a stage's own manifest —
see `CHECKPOINT_MEDIA_KEYS`, without which it would delete a YouTube
download's metadata and its unselected caption tracks — and
`VIDEO_CLEANUP_DRY_RUN` makes a pass report what it would remove and remove
nothing, so a volume can be inspected before it is pruned rather than after.
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
# Directories a mounted filesystem puts at its own root. They are not owner
# prefixes and never will be, and warning about them every hour trains a
# reader to skim the one log line this sweep has for a real surprise.
FILESYSTEM_ENTRIES = frozenset({"lost+found"})


def dry_run_requested() -> bool:
    """Whether this deployment wants the sweep to report instead of delete.

    The first pass on an existing volume is the one nobody can preview: it
    runs unattended, minutes after a deploy, against bytes no test fixture
    stands in for. `VIDEO_CLEANUP_DRY_RUN=1` makes that pass log exactly what
    it would remove and remove nothing, so the answer arrives before the
    deletions do rather than after.
    """

    return os.getenv("VIDEO_CLEANUP_DRY_RUN", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


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
    dry_run: bool = False

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
    dry_run: bool,
    already_released: bool = False,
) -> tuple[bool, int]:
    """Unlink one job's staging object and record that it happened.

    Returns whether the row was settled and how many bytes came back. A store
    that is unreachable must not fail the pass: the job keeps its marker-free
    provenance and the next pass tries again.

    `already_released` is for the second and later jobs naming one object: the
    bytes are gone, the row still needs its marker.
    """

    size = 0 if already_released else _size_of(
        store, owner_id=row["owner_id"], storage_key=row["staging_storage_key"]
    )
    if dry_run:
        logger.info(
            "would release staging object %s for job %s (%s bytes)",
            row["staging_storage_key"],
            row["id"],
            size,
        )
        return True, size
    try:
        if not already_released:
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
    dry_run: bool = False,
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
        if dry_run:
            logger.info(
                "would cancel abandoned upload %s and release %s (%s bytes)",
                row["id"],
                row["staging_storage_key"],
                size,
            )
            cancelled += 1
            reclaimed += size
            continue
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
    *,
    limits: VideoRetentionLimits,
    released: set[str] | None = None,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Remove staging copies whose bytes now live in canonical storage.

    The acquisition stage imports an uploaded file into content-addressed
    canonical storage and leaves the staging copy where it was, so every
    uploaded lecture has occupied the volume twice. The canonical object is the
    retained copy — it is what the reader plays and what every rebuild reads —
    so the staging duplicate has no remaining purpose once it exists.

    A job reaching ``ready`` is not on its own enough, because a staging key is
    not a job's private property: re-ingesting a lecture makes a new job
    against the *same* reserved path, so one object can be named by several
    jobs at once. The production lecture has four naming one upload, three
    ready and one failed inside its retry window — and that failed job's retry
    reads the staging object, not the canonical one. Releasing on the strength
    of a ready job alone would delete the file out from under it, which is
    precisely what `delete_expired_staging`'s window exists to prevent.

    So the unit here is the object, not the job. A key is released once every
    job naming it is done with it, every job naming it is then marked, and it
    is counted once however many jobs pointed at it.

    Deleted only after confirming the canonical object is actually on the
    volume. The source row naming it is not enough: a row can outlive its bytes
    if a previous sweep or a manual mistake removed them, and in that case the
    staging copy is the only surviving version of the file.

    Returns objects deleted, bytes reclaimed, and failed-deletion count.
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
          and job.status = %(ready)s
          and not job.provenance_json ? 'staging_deleted_at'
          -- No other job may still have a use for these bytes: one waiting to
          -- receive them, one about to read them, or one whose retry would.
          and not exists (
              select 1 from video.ingestion_jobs as other
              where other.owner_id = job.owner_id
                and other.staging_storage_key = job.staging_storage_key
                and other.id <> job.id
                and (
                    other.status in (
                        %(awaiting)s, %(queued)s, %(running)s, %(retrying)s
                    )
                    or (
                        other.status in (%(failed)s, %(cancelled)s)
                        and (
                            other.completed_at is null
                            or other.completed_at
                               > now() - make_interval(days => %(days)s)
                        )
                    )
                )
          )
        order by job.created_at
        for update of job skip locked
        """,
        {
            "ready": str(Status.READY),
            "awaiting": str(Status.AWAITING_UPLOAD),
            "queued": str(Status.QUEUED),
            "running": str(Status.RUNNING),
            "retrying": str(Status.RETRY_SCHEDULED),
            "failed": str(Status.FAILED),
            "cancelled": str(Status.CANCELLED),
            "days": limits.staging_retention_days,
        },
    ).fetchall()

    deleted = 0
    reclaimed = 0
    failed = 0
    released = set() if released is None else released
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
        # A key already released by an earlier row in this pass. Its remaining
        # jobs still need their marker, or every future pass reports the same
        # object again — but the object and its bytes are counted once.
        repeat = row["staging_storage_key"] in released
        settled, size = _release_staging(
            connection,
            store,
            row,
            event_type="staging_source_deleted",
            message=(
                "The uploaded file was released after it was stored "
                "canonically."
            ),
            dry_run=dry_run,
            already_released=repeat,
        )
        if not settled:
            failed += 1
            continue
        if not repeat:
            released.add(row["staging_storage_key"])
            deleted += 1
            reclaimed += size
    return deleted, reclaimed, failed


def delete_expired_staging(
    connection: Connection,
    store: FilesystemMediaStore,
    *,
    limits: VideoRetentionLimits,
    released: set[str] | None = None,
    dry_run: bool = False,
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
    released = set() if released is None else released
    for row in rows:
        # A key an earlier pass already let go of. Several jobs can name one
        # upload, so its remaining rows still need their marker while the
        # object itself has already been counted.
        repeat = row["staging_storage_key"] in released
        settled, size = _release_staging(
            connection,
            store,
            row,
            event_type="staging_source_deleted",
            message="The uploaded file was removed by the retention policy.",
            dry_run=dry_run,
            already_released=repeat,
        )
        if not settled:
            failed += 1
            continue
        if not repeat:
            released.add(row["staging_storage_key"])
            deleted += 1
            reclaimed += size
    return deleted, reclaimed, failed


# Objects a stage recorded producing, named nowhere else. The acquisition
# stage stores a YouTube download's `info.json` and *every* caption track it
# found, then writes a row only for the caption the transcript stage went on
# to select — so the metadata file and the rejected caption tracks are named
# only here, in the manifest of the stage that made them.
#
# They are not spare copies. `CARRIED_STAGES` reuses `acquire_source` on a
# re-ingest, so a later version reads this manifest and expects the objects to
# still be there, and re-fetching them means going back to YouTube for a video
# that may no longer be available.
#
# `$.**` rather than a fixed path: a manifest is a stage's own shape, and a
# reference set that had to be updated whenever a stage added an object would
# fail by deleting rather than by erroring.
CHECKPOINT_MEDIA_KEYS = """
    select distinct jsonb_array_elements_text(
        jsonb_path_query_array(output_manifest_json, 'lax $.**.storage_key')
    ) as key
    from video.ingestion_stage_checkpoints
    where owner_id = %(owner)s
"""


def _referenced_keys(connection: Connection, *, owner_id: UUID) -> set[str]:
    """Every storage key this owner still has a row for."""

    keys = {
        row["key"]
        for row in connection.execute(
            OWNER_MEDIA_KEYS, {"owner": owner_id}
        ).fetchall()
        if row["key"]
    }
    keys.update(
        row["key"]
        for row in connection.execute(
            CHECKPOINT_MEDIA_KEYS, {"owner": owner_id}
        ).fetchall()
        if row["key"]
    )
    return keys


def _owner_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    directories = []
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.name in FILESYSTEM_ENTRIES:
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
    dry_run: bool = False,
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
            if dry_run:
                logger.warning(
                    "would delete %s video object %s (%s bytes; no row "
                    "references it)",
                    "partial" if is_partial else "orphaned",
                    key,
                    size,
                )
                if is_partial:
                    partials += 1
                else:
                    deleted += 1
                reclaimed += size
                budget -= 1
                continue
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
    dry_run: bool | None = None,
) -> VideoCleanupSummary:
    """Run one full video retention pass. Safe to repeat and to interrupt.

    `dry_run` reports what every pass would do and changes nothing — see
    `dry_run_requested`. Left as None it comes from the environment, so a
    deployment can watch a pass before letting it delete without shipping a
    different build to do it.
    """

    limits = limits or load_retention_limits()
    store = store or FilesystemMediaStore()
    dry_run = dry_run_requested() if dry_run is None else dry_run
    # One upload can be named by several jobs, and both staging passes can
    # reach the same key. Shared so the object — and its bytes — are counted
    # once however many rows point at it.
    released: set[str] = set()

    with connection.transaction():
        cancelled, cancelled_bytes, cancel_failures = cancel_abandoned_uploads(
            connection, store, limits=limits, dry_run=dry_run
        )
    with connection.transaction():
        promoted, promoted_bytes, promoted_failures = delete_promoted_staging(
            connection, store, limits=limits, released=released, dry_run=dry_run
        )
    with connection.transaction():
        expired, expired_bytes, expired_failures = delete_expired_staging(
            connection, store, limits=limits, released=released, dry_run=dry_run
        )
    # Runs last so anything the job-driven passes just released is already
    # absent from the rows this sweep compares the volume against.
    orphans, partials, orphan_bytes, orphan_failures = delete_orphaned_media(
        connection, store, limits=limits, dry_run=dry_run
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
        dry_run=dry_run,
    )
    if summary.total or summary.deletions_failed:
        logger.log(
            logging.WARNING if summary.deletions_failed else logging.INFO,
            ("video cleanup pass (DRY RUN, nothing deleted): " if dry_run
             else "video cleanup pass: ")
            + "%s abandoned uploads cancelled, %s promoted "
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
