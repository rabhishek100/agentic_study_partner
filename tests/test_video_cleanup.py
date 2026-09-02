"""Video retention: staging duplicates, abandoned uploads, stranded bytes.

Books have had a retention sweep since their first release and video has had
none, on the one volume where the bytes are large enough to matter. These
tests pin the four things that were leaking: an upload that never completed, a
staging copy of a file already stored canonically, a failed job's upload past
its retry window, and objects whose rows are gone.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg.types.json import Jsonb

from storage.database import connection, resolve_database_url
from tests.test_video_media_store import FakeS3Client
from video.cleanup import (
    MAXIMUM_ORPHAN_DELETIONS_PER_SWEEP,
    dry_run_requested,
    VideoRetentionLimits,
    load_retention_limits,
    run_video_cleanup,
)
from video.jobs import get_job
from video.media_store import FilesystemMediaStore, S3MediaStore
from video.repository import initialize_video_upload, list_job_events
from video.states import Status


LIMITS = VideoRetentionLimits(
    abandoned_upload_hours=24, staging_retention_days=7, orphan_grace_hours=24
)


class VideoCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FilesystemMediaStore(Path(self.directory.name))
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@test.local"),
            )
        self.addCleanup(self._discard)

    def _discard(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s", (self.owner,)
            )

    # --- fixtures -----------------------------------------------------

    def upload_job(
        self,
        database,
        *,
        status: str = "awaiting_upload",
        created_interval: str = "1 hour",
        completed_interval: str | None = None,
        canonical_key: str | None = None,
    ):
        """One uploaded video, aged and settled however the case needs."""

        created = initialize_video_upload(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            original_filename="lecture.mp4",
            media_type="video/mp4",
            declared_size_bytes=1024,
        )
        database.execute(
            f"""
            update video.ingestion_jobs
            set status = %s,
                created_at = now() - interval '{created_interval}',
                completed_at = {
                    "now() - interval '" + completed_interval + "'"
                    if completed_interval
                    else "null"
                }
            where id = %s
            """,
            (status, created.job_id),
        )
        if canonical_key is not None:
            database.execute(
                """
                update video.video_sources
                set status = 'ready', storage_backend = 'filesystem',
                    storage_key = %s, content_hash = %s, size_bytes = 1024,
                    acquired_at = now()
                where id = %s
                """,
                (canonical_key, "a" * 64, created.source_id),
            )
        return created

    def write(self, storage_key: str, payload: bytes = b"video-bytes") -> str:
        writer = self.store.writer(
            owner_id=self.owner, storage_key=storage_key, maximum_bytes=4096
        )
        writer.write(payload)
        writer.finish(expected_size=len(payload))
        return storage_key

    def age(self, storage_key: str, *, days: int = 3) -> None:
        """Backdate an object past the orphan grace window."""

        path = self.store.open_path(owner_id=self.owner, storage_key=storage_key)
        stamp = path.stat().st_mtime - days * 86_400
        os.utime(path, (stamp, stamp))

    def exists(self, storage_key: str) -> bool:
        try:
            self.store.open_path(owner_id=self.owner, storage_key=storage_key)
        except (FileNotFoundError, ValueError):
            return False
        return True

    def sweep(
        self,
        database,
        *,
        limits: VideoRetentionLimits | None = None,
        dry_run: bool = False,
    ):
        return run_video_cleanup(
            database,
            store=self.store,
            limits=limits or LIMITS,
            dry_run=dry_run,
        )

    def checkpoint(self, database, *, version_id, manifest: dict) -> None:
        """One completed stage, recording the objects it produced."""

        database.execute(
            """
            insert into video.ingestion_stage_checkpoints (
                owner_id, video_id, ingestion_version_id, stage, status,
                dependency_hash, output_manifest_json, completed_at
            )
            select %s, v.video_id, v.id, 'acquire_source', 'complete', %s, %s,
                   now()
            from video.ingestion_versions v where v.id = %s
            """,
            (self.owner, "f" * 64, Jsonb(manifest), version_id),
        )

    # --- abandoned uploads --------------------------------------------

    def test_an_abandoned_upload_is_cancelled_and_its_bytes_released(self) -> None:
        with connection(self.database_url) as database:
            stale = self.upload_job(database, created_interval="2 days")
            fresh = self.upload_job(database, created_interval="1 hour")
            self.write(stale.upload_storage_key)
            self.write(fresh.upload_storage_key)

            summary = self.sweep(database)

            stale_job = get_job(database, owner_id=self.owner, job_id=stale.job_id)
            fresh_job = get_job(database, owner_id=self.owner, job_id=fresh.job_id)
            events = list_job_events(
                database, stale.job_id, owner_id=self.owner
            )
            readiness = database.execute(
                "select readiness_status from video.videos where id = %s",
                (stale.video_id,),
            ).fetchone()["readiness_status"]

        self.assertEqual(summary.abandoned_uploads_cancelled, 1)
        self.assertEqual(stale_job.status, Status.CANCELLED)
        self.assertFalse(stale_job.last_error_retryable)
        self.assertIn("staging_deleted_at", stale_job.provenance)
        self.assertFalse(self.exists(stale.upload_storage_key))
        # The reader's card must not sit at "pending" forever for an
        # ingestion that can no longer start.
        self.assertEqual(readiness, "failed")
        self.assertIn(
            "abandoned_upload_cancelled",
            [event["event_type"] for event in events],
        )
        # Still inside the window: untouched, bytes and all.
        self.assertEqual(fresh_job.status, Status.AWAITING_UPLOAD)
        self.assertTrue(self.exists(fresh.upload_storage_key))

    # --- the staging duplicate ----------------------------------------

    def test_a_ready_job_releases_the_staging_copy_of_its_source(self) -> None:
        """The leak that mattered: every upload was stored twice, forever."""

        canonical = f"{self.owner}/canonical/videos/sha256/ab/cd/abcd.mp4"
        with connection(self.database_url) as database:
            job = self.upload_job(
                database,
                status="ready",
                created_interval="5 days",
                completed_interval="5 days",
                canonical_key=canonical,
            )
            self.write(job.upload_storage_key)
            self.write(canonical)

            first = self.sweep(database)
            second = self.sweep(database)

            settled = get_job(database, owner_id=self.owner, job_id=job.job_id)
            events = [
                event["event_type"]
                for event in list_job_events(
                    database, job.job_id, owner_id=self.owner
                )
            ]

        self.assertEqual(first.promoted_staging_deleted, 1)
        self.assertEqual(first.bytes_reclaimed, len(b"video-bytes"))
        # The provenance marker makes a second pass a no-op.
        self.assertEqual(second.promoted_staging_deleted, 0)
        self.assertFalse(self.exists(job.upload_storage_key))
        self.assertTrue(self.exists(canonical))
        self.assertEqual(settled.status, Status.READY)
        self.assertIn("staging_source_deleted", events)

    def test_the_staging_copy_survives_a_canonical_object_that_is_missing(
        self,
    ) -> None:
        """A row can outlive its bytes; then staging is the only copy left."""

        canonical = f"{self.owner}/canonical/videos/sha256/ef/01/ef01.mp4"
        with connection(self.database_url) as database:
            job = self.upload_job(
                database,
                status="ready",
                created_interval="5 days",
                completed_interval="5 days",
                canonical_key=canonical,
            )
            self.write(job.upload_storage_key)
            # Canonical object deliberately never written.

            summary = self.sweep(database)
            settled = get_job(database, owner_id=self.owner, job_id=job.job_id)

        self.assertEqual(summary.promoted_staging_deleted, 0)
        self.assertTrue(self.exists(job.upload_storage_key))
        self.assertNotIn("staging_deleted_at", settled.provenance)

    def test_a_failed_job_keeps_its_upload_until_the_retry_window_closes(
        self,
    ) -> None:
        """A retry re-runs acquisition, and acquisition reads staging."""

        canonical = f"{self.owner}/canonical/videos/sha256/12/34/1234.mp4"
        with connection(self.database_url) as database:
            recent = self.upload_job(
                database,
                status="failed",
                created_interval="3 days",
                completed_interval="2 days",
                canonical_key=canonical,
            )
            expired = self.upload_job(
                database,
                status="failed",
                created_interval="30 days",
                completed_interval="10 days",
            )
            self.write(recent.upload_storage_key)
            self.write(expired.upload_storage_key)
            self.write(canonical)

            summary = self.sweep(database)

            kept = get_job(database, owner_id=self.owner, job_id=recent.job_id)
            dropped = get_job(
                database, owner_id=self.owner, job_id=expired.job_id
            )

        # Acquired canonically or not, a failed job inside the window keeps
        # the object its retry would read.
        self.assertEqual(summary.promoted_staging_deleted, 0)
        self.assertEqual(summary.expired_staging_deleted, 1)
        self.assertTrue(self.exists(recent.upload_storage_key))
        self.assertNotIn("staging_deleted_at", kept.provenance)
        self.assertFalse(self.exists(expired.upload_storage_key))
        self.assertIn("staging_deleted_at", dropped.provenance)
        # History is kept; only the bytes go.
        self.assertEqual(dropped.status, Status.FAILED)

    # --- orphans -------------------------------------------------------

    def test_objects_no_row_names_are_swept(self) -> None:
        """Deleting a video, a version, or an account leaves bytes behind.

        `delete_video` reports the keys it stranded, but only to a caller
        still running. A worker killed between the commit and the unlink, or
        an account deleted straight out of the database, leaves objects
        nothing will ever look at again.
        """

        orphan = f"{self.owner}/canonical/videos/sha256/aa/bb/aabb.mp4"
        with connection(self.database_url) as database:
            live = self.upload_job(database, created_interval="1 hour")
            self.write(live.upload_storage_key)
            self.age(live.upload_storage_key)
            self.write(orphan, payload=b"stranded")
            self.age(orphan)

            summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, 1)
        self.assertEqual(summary.bytes_reclaimed, len(b"stranded"))
        self.assertFalse(self.exists(orphan))
        # A reserved upload is named by a live job row and is not an orphan.
        self.assertTrue(self.exists(live.upload_storage_key))

    def test_a_recently_written_object_is_left_for_the_next_pass(self) -> None:
        """The sweep must not race the ingest that is still writing rows.

        Media reaches the volume before the row naming it commits, so without
        a grace window every sweep would delete files a running stage was
        about to reference.
        """

        fresh = f"{self.owner}/canonical/frames/sha256/cc/dd/ccdd.webp"
        with connection(self.database_url) as database:
            self.write(fresh)

            summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertTrue(self.exists(fresh))

    def test_an_interrupted_write_leaves_a_partial_nothing_else_reclaims(
        self,
    ) -> None:
        directory = (
            self.store.root / str(self.owner) / "canonical" / "videos"
        )
        directory.mkdir(parents=True, exist_ok=True)
        partial = directory / ".original.mp4.deadbeef.part"
        partial.write_bytes(b"half a file")
        stamp = partial.stat().st_mtime - 3 * 86_400
        os.utime(partial, (stamp, stamp))

        with connection(self.database_url) as database:
            summary = self.sweep(database)

        self.assertEqual(summary.partial_uploads_deleted, 1)
        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertFalse(partial.exists())

    def orphan_frames(self, count: int, *, start: int = 0) -> list[str]:
        """`count` aged frame objects no row will ever name."""

        keys = []
        for index in range(start, start + count):
            digest = f"{index:064x}"
            key = (
                f"{self.owner}/canonical/frames/sha256/"
                f"{digest[:2]}/{digest[2:4]}/{digest}.webp"
            )
            self.write(key, payload=b"x")
            self.age(key)
            keys.append(key)
        return keys

    def test_deleting_is_the_thing_an_operator_has_to_ask_for(self) -> None:
        """An unset environment variable must cost a log line, not a corpus.

        The old default deleted unless told not to, which is the wrong way
        round for an unattended pass over storage that has no undo.
        """

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(dry_run_requested())
        for value in ("1", "true", "yes", "", "  "):
            with patch.dict(os.environ, {"VIDEO_CLEANUP_DRY_RUN": value}, clear=False):
                self.assertTrue(dry_run_requested(), value)
        for value in ("0", "false", "no", "NO"):
            with patch.dict(os.environ, {"VIDEO_CLEANUP_DRY_RUN": value}, clear=False):
                self.assertFalse(dry_run_requested(), value)

    def test_a_mostly_unreferenced_volume_is_refused(self) -> None:
        """Most of an owner's media missing its rows means the rows are missing."""

        self.orphan_frames(30)
        with connection(self.database_url) as database:
            live = self.upload_job(database, created_interval="1 hour")
            self.write(live.upload_storage_key)
            self.age(live.upload_storage_key)
            summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertTrue(self.exists(live.upload_storage_key))

    def test_a_volume_no_row_explains_is_refused_rather_than_emptied(self) -> None:
        """A worker pointed at the wrong database must not empty a volume.

        This is the failure that cost 995 frame images on 2026-09-01: the
        sweep ran against a database that had been rolled back, found nothing
        referenced, and deleted objects R2 could not give back. A per-pass
        budget only made that take longer. The sweep now has to believe the
        database before it may act on it, and a database naming none of an
        owner's media has not earned that.
        """

        self.orphan_frames(MAXIMUM_ORPHAN_DELETIONS_PER_SWEEP + 3)

        with connection(self.database_url) as database:
            summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertEqual(summary.bytes_reclaimed, 0)

    def test_one_sweep_deletes_a_bounded_number_of_orphans(self) -> None:
        """Even a believed orphan set is drained a bounded amount at a time.

        The ceiling is lifted here on purpose so the budget is what the
        assertion is about; the guard's own refusal is covered above.
        """

        limit = MAXIMUM_ORPHAN_DELETIONS_PER_SWEEP
        self.orphan_frames(limit + 3)

        with connection(self.database_url) as database:
            # A live job row, so the database is not one that has simply
            # forgotten this owner.
            live = self.upload_job(database, created_interval="1 hour")
            self.write(live.upload_storage_key)
            with patch.dict(
                os.environ, {"VIDEO_CLEANUP_MAX_ORPHAN_FRACTION": "1.0"}, clear=False
            ):
                summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, limit)

    def test_another_owners_objects_are_never_reached(self) -> None:
        """Reference sets are read per owner; a sweep must stay inside one."""

        stranger = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (stranger, f"{stranger}@test.local"),
            )
            try:
                job = initialize_video_upload(
                    database,
                    owner_id=stranger,
                    idempotency_key=uuid4(),
                    original_filename="other.mp4",
                    media_type="video/mp4",
                    declared_size_bytes=1024,
                )
                writer = self.store.writer(
                    owner_id=stranger,
                    storage_key=job.upload_storage_key,
                    maximum_bytes=4096,
                )
                writer.write(b"theirs")
                writer.finish(expected_size=len(b"theirs"))

                summary = self.sweep(database)

                self.assertEqual(summary.orphaned_objects_deleted, 0)
                self.assertTrue(
                    self.store.open_path(
                        owner_id=stranger, storage_key=job.upload_storage_key
                    ).is_file()
                )
            finally:
                database.execute(
                    "delete from auth.users where id = %s", (stranger,)
                )

    def test_r2_orphan_sweep_is_grace_aware_and_preserves_references(self) -> None:
        client = FakeS3Client()
        store = S3MediaStore(
            bucket="private-video-media",
            client=client,
            cache_root=Path(self.directory.name) / "r2-cache",
        )
        orphan = f"{self.owner}/canonical/videos/sha256/aa/bb/orphan.mp4"
        recent = f"{self.owner}/canonical/videos/sha256/cc/dd/recent.mp4"
        with connection(self.database_url) as database:
            live = self.upload_job(database, created_interval="1 hour")
            for key in (live.upload_storage_key, orphan, recent):
                payload = key.encode()
                writer = store.writer(
                    owner_id=self.owner, storage_key=key, maximum_bytes=256
                )
                writer.write(payload)
                writer.finish(expected_size=len(payload))
            old = datetime.now(timezone.utc) - timedelta(days=3)
            client.modified[(store.bucket, live.upload_storage_key)] = old
            client.modified[(store.bucket, orphan)] = old

            summary = run_video_cleanup(
                database, store=store, limits=LIMITS, dry_run=False
            )

        self.assertEqual(summary.orphaned_objects_deleted, 1)
        self.assertNotIn((store.bucket, orphan), client.objects)
        self.assertIn((store.bucket, recent), client.objects)
        self.assertIn((store.bucket, live.upload_storage_key), client.objects)

    # --- one object, several jobs ---------------------------------------

    def test_a_shared_staging_object_waits_for_every_job_that_needs_it(self) -> None:
        """A staging key is not a job's private property.

        Re-ingesting a lecture makes a new job against the same reserved path,
        so one upload ends up named by several jobs. Production has four on one
        object — three ready and one failed inside its retry window — and that
        retry re-runs acquisition, which reads staging rather than canonical.
        """

        canonical = f"{self.owner}/canonical/videos/sha256/aa/11/aa11.mp4"
        with connection(self.database_url) as database:
            done = self.upload_job(
                database,
                status="ready",
                created_interval="5 days",
                completed_interval="5 days",
                canonical_key=canonical,
            )
            retryable = self.upload_job(
                database,
                status="failed",
                created_interval="3 days",
                completed_interval="2 days",
            )
            # Point the failed job at the same reserved upload.
            database.execute(
                """
                update video.ingestion_jobs set staging_storage_key = %s
                where id = %s
                """,
                (done.upload_storage_key, retryable.job_id),
            )
            self.write(done.upload_storage_key)
            self.write(canonical)

            held = self.sweep(database)

            # Once the failed job ages out, both let go together.
            database.execute(
                """
                update video.ingestion_jobs
                set created_at = now() - interval '40 days',
                    completed_at = now() - interval '30 days'
                where id = %s
                """,
                (retryable.job_id,),
            )
            released = self.sweep(database)

        self.assertEqual(held.promoted_staging_deleted, 0)
        self.assertEqual(held.bytes_reclaimed, 0)
        self.assertEqual(released.total, 1)
        self.assertFalse(self.exists(done.upload_storage_key))

    def test_one_object_named_by_several_jobs_is_counted_once(self) -> None:
        """Three ready jobs over one upload are 342 MB, not a gigabyte."""

        canonical = f"{self.owner}/canonical/videos/sha256/bb/22/bb22.mp4"
        with connection(self.database_url) as database:
            first = self.upload_job(
                database,
                status="ready",
                created_interval="5 days",
                completed_interval="5 days",
                canonical_key=canonical,
            )
            others = [
                self.upload_job(
                    database,
                    status="ready",
                    created_interval="5 days",
                    completed_interval="5 days",
                    canonical_key=canonical,
                )
                for _ in range(2)
            ]
            for job in others:
                database.execute(
                    """
                    update video.ingestion_jobs set staging_storage_key = %s
                    where id = %s
                    """,
                    (first.upload_storage_key, job.job_id),
                )
            self.write(first.upload_storage_key)
            self.write(canonical)

            summary = self.sweep(database)
            marked = [
                get_job(database, owner_id=self.owner, job_id=job.job_id).provenance
                for job in [first, *others]
            ]
            again = self.sweep(database)

        self.assertEqual(summary.promoted_staging_deleted, 1)
        self.assertEqual(summary.bytes_reclaimed, len(b"video-bytes"))
        # Every job naming it is marked, or each pass reports it again.
        for provenance in marked:
            self.assertIn("staging_deleted_at", provenance)
        self.assertEqual(again.total, 0)

    # --- objects a stage names and no table does ------------------------

    def test_media_named_only_in_a_stage_manifest_is_not_an_orphan(self) -> None:
        """The acquisition stage stores a YouTube download's metadata and every
        caption track it found, then writes a row only for the caption the
        transcript stage selected. The rest are named in that stage's manifest
        and nowhere else — and a re-ingest carries `acquire_source` forward and
        expects to find them, so deleting them means going back to YouTube for
        a video that may no longer be there.
        """

        info = f"{self.owner}/canonical/metadata/sha256/1a/2b/1a2b.json"
        rejected = f"{self.owner}/canonical/transcripts/sha256/3c/4d/3c4d.vtt"
        with connection(self.database_url) as database:
            job = self.upload_job(database, created_interval="1 hour")
            version = database.execute(
                "select target_version_id from video.ingestion_jobs where id = %s",
                (job.job_id,),
            ).fetchone()["target_version_id"]
            self.checkpoint(
                database,
                version_id=version,
                manifest={
                    "metadata": {"storage_key": info},
                    "captions": [{"storage_key": rejected}],
                },
            )
            for key in (info, rejected):
                self.write(key)
                self.age(key)

            summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertTrue(self.exists(info))
        self.assertTrue(self.exists(rejected))

    def test_media_a_deleted_version_named_becomes_reclaimable(self) -> None:
        """Checkpoints cascade with their version, so retiring one releases
        exactly the objects it was protecting and nothing else."""

        info = f"{self.owner}/canonical/metadata/sha256/5e/6f/5e6f.json"
        with connection(self.database_url) as database:
            job = self.upload_job(database, created_interval="1 hour")
            version = database.execute(
                "select target_version_id from video.ingestion_jobs where id = %s",
                (job.job_id,),
            ).fetchone()["target_version_id"]
            self.checkpoint(
                database,
                version_id=version,
                manifest={"metadata": {"storage_key": info}},
            )
            self.write(info)
            self.age(info)
            self.assertEqual(self.sweep(database).orphaned_objects_deleted, 0)

            database.execute(
                "delete from video.ingestion_stage_checkpoints"
                " where ingestion_version_id = %s",
                (version,),
            )
            summary = self.sweep(database)

        self.assertEqual(summary.orphaned_objects_deleted, 1)
        self.assertFalse(self.exists(info))

    # --- watching a pass before letting it delete ------------------------

    def test_a_dry_run_reports_everything_and_removes_nothing(self) -> None:
        """The first pass on an existing volume runs unattended minutes after
        a deploy. This is how its answer arrives before its deletions do."""

        canonical = f"{self.owner}/canonical/videos/sha256/7a/8b/7a8b.mp4"
        orphan = f"{self.owner}/canonical/frames/sha256/9c/0d/9c0d.webp"
        with connection(self.database_url) as database:
            ready = self.upload_job(
                database,
                status="ready",
                created_interval="5 days",
                completed_interval="5 days",
                canonical_key=canonical,
            )
            abandoned = self.upload_job(database, created_interval="2 days")
            self.write(ready.upload_storage_key)
            self.write(canonical)
            self.write(abandoned.upload_storage_key)
            self.write(orphan, payload=b"stranded")
            self.age(orphan)

            planned = self.sweep(database, dry_run=True)
            settled = get_job(database, owner_id=self.owner, job_id=ready.job_id)
            still_waiting = get_job(
                database, owner_id=self.owner, job_id=abandoned.job_id
            )

        self.assertTrue(planned.dry_run)
        self.assertEqual(planned.promoted_staging_deleted, 1)
        self.assertEqual(planned.abandoned_uploads_cancelled, 1)
        self.assertEqual(planned.orphaned_objects_deleted, 1)
        # Every byte still there, and no row moved.
        self.assertTrue(self.exists(ready.upload_storage_key))
        self.assertTrue(self.exists(abandoned.upload_storage_key))
        self.assertTrue(self.exists(orphan))
        self.assertNotIn("staging_deleted_at", settled.provenance)
        self.assertEqual(still_waiting.status, Status.AWAITING_UPLOAD)

    def test_a_dry_run_predicts_what_the_real_pass_then_does(self) -> None:
        """A preview nobody can trust is worse than none."""

        canonical = f"{self.owner}/canonical/videos/sha256/ba/dc/badc.mp4"
        orphan = f"{self.owner}/canonical/frames/sha256/ef/12/ef12.webp"
        with connection(self.database_url) as database:
            ready = self.upload_job(
                database,
                status="ready",
                created_interval="5 days",
                completed_interval="5 days",
                canonical_key=canonical,
            )
            self.write(ready.upload_storage_key)
            self.write(canonical)
            self.write(orphan, payload=b"stranded")
            self.age(orphan)

            planned = self.sweep(database, dry_run=True)
            applied = self.sweep(database)

        self.assertEqual(planned.total, applied.total)
        self.assertEqual(planned.bytes_reclaimed, applied.bytes_reclaimed)
        self.assertFalse(applied.dry_run)


class RetentionLimitTests(unittest.TestCase):
    def test_limits_come_from_the_environment_with_defaults(self) -> None:
        with patch.dict(os.environ, {"VIDEO_STAGING_RETENTION_DAYS": "3"}):
            limits = load_retention_limits()
        self.assertEqual(limits.staging_retention_days, 3)
        self.assertEqual(limits.abandoned_upload_hours, 24)

    def test_a_nonsense_window_is_rejected_rather_than_ignored(self) -> None:
        with patch.dict(os.environ, {"VIDEO_MEDIA_ORPHAN_GRACE_HOURS": "0"}):
            with self.assertRaises(ValueError):
                load_retention_limits()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
