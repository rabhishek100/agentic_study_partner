"""Retention behaviour: abandoned uploads and expired sources."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from ingestion.cleanup import _JOB_PREFIX, run_cleanup
from ingestion.config import IngestionLimits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.jobs import get_job, list_events
from ingestion.states import Status
from storage.database import connection, resolve_database_url


LIMITS = IngestionLimits(abandoned_upload_hours=24, source_retention_days=7)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.database_url = resolve_database_url()
        self.owner = str(uuid4())
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@test.local"),
            )
        self.addCleanup(self._discard)

        self.deleted_objects: list[str] = []
        deleter = patch(
            "ingestion.cleanup.delete_object",
            side_effect=lambda bucket, path: self.deleted_objects.append(path)
            or True,
        )
        self.delete_object = deleter.start()
        self.addCleanup(deleter.stop)

        # Default: an empty bucket, so job-driven passes are tested alone.
        lister = patch("ingestion.cleanup.list_prefix", return_value=[])
        self.list_prefix = lister.start()
        self.addCleanup(lister.stop)

    def bucket_contains(self, paths):
        """Make the fake bucket list the two levels of the given paths."""

        tree: dict[str, list[str]] = {}
        for path in paths:
            owner, job, _ = path.split("/")
            tree.setdefault(owner, []).append(job)

        def fake(bucket, prefix="", *, limit=100):
            return list(tree) if not prefix else tree.get(prefix, [])

        self.list_prefix.side_effect = fake

    def _discard(self):
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s", (self.owner,)
            )

    def job(
        self,
        database,
        *,
        status,
        age_interval,
        completed_interval=None,
        book_id=None,
    ):
        job_id = uuid4()
        database.execute(
            f"""
            insert into ingestion_jobs (
                id, owner_id, idempotency_key, status, storage_bucket,
                storage_path, original_filename, book_id, created_at,
                completed_at
            ) values (
                %s, %s, %s, %s, 'book-sources', %s, 'book.pdf', %s,
                now() - interval '{age_interval}',
                {"now() - interval '" + completed_interval + "'" if completed_interval else "null"}
            )
            """,
            (
                job_id,
                self.owner,
                uuid4(),
                status,
                f"{self.owner}/{job_id}/original.pdf",
                book_id,
            ),
        )
        return job_id

    def test_an_abandoned_upload_is_cancelled_and_its_path_released(self):
        with connection(self.database_url) as database:
            stale = self.job(database, status="awaiting_upload", age_interval="2 days")
            fresh = self.job(database, status="awaiting_upload", age_interval="1 hour")

            summary = run_cleanup(database, limits=LIMITS)

            stale_job = get_job(database, owner_id=self.owner, job_id=stale)
            fresh_job = get_job(database, owner_id=self.owner, job_id=fresh)
            events = list_events(database, owner_id=self.owner, job_id=stale)

        self.assertEqual(summary.abandoned_uploads_cancelled, 1)
        self.assertEqual(stale_job.status, Status.CANCELLED)
        self.assertFalse(stale_job.last_error_retryable)
        self.assertIn("source_deleted_at", stale_job.provenance)
        self.assertEqual(fresh_job.status, Status.AWAITING_UPLOAD)
        self.assertIn(
            "abandoned_upload_cancelled",
            [event["event_type"] for event in events],
        )
        self.assertEqual(len(self.deleted_objects), 1)

    def test_expired_failed_sources_are_deleted_once(self):
        with connection(self.database_url) as database:
            old_failed = self.job(
                database,
                status="failed",
                age_interval="30 days",
                completed_interval="10 days",
            )
            recent_failed = self.job(
                database,
                status="failed",
                age_interval="3 days",
                completed_interval="2 days",
            )

            first = run_cleanup(database, limits=LIMITS)
            second = run_cleanup(database, limits=LIMITS)

            old_job = get_job(database, owner_id=self.owner, job_id=old_failed)
            recent_job = get_job(
                database, owner_id=self.owner, job_id=recent_failed
            )

        self.assertEqual(first.expired_sources_deleted, 1)
        # The provenance marker makes a second pass a no-op.
        self.assertEqual(second.expired_sources_deleted, 0)
        self.assertIn("source_deleted_at", old_job.provenance)
        self.assertEqual(old_job.status, Status.FAILED)  # history is kept
        self.assertNotIn("source_deleted_at", recent_job.provenance)

    def test_ready_jobs_keep_their_sources_forever(self):
        with connection(self.database_url) as database:
            book_id = database.execute(
                """
                insert into books (
                    owner_id, title, source_path, source_filename, file_hash,
                    parser_version, parsed_at, status, ready_at
                ) values (%s, 'T', 'p', 'f', %s, 'v', now(), 'ready', now())
                returning id
                """,
                (self.owner, "d" * 64),
            ).fetchone()["id"]
            self.job(
                database,
                status="ready",
                age_interval="90 days",
                completed_interval="90 days",
                book_id=book_id,
            )

            summary = run_cleanup(database, limits=LIMITS)

        self.assertEqual(summary.total, 0)
        self.assertEqual(self.deleted_objects, [])

    def test_objects_with_no_job_row_are_swept(self):
        """A deleted account's objects would otherwise be billed forever.

        Storage has no foreign key to cascade through, so removing a user
        leaves their uploads behind. This sweep is what reclaims them.
        """

        with connection(self.database_url) as database:
            live = self.job(database, status="queued", age_interval="1 hour")
            live_path = f"{self.owner}/{live}/original.pdf"
            orphan_path = f"{uuid4()}/{uuid4()}/original.pdf"
            self.bucket_contains([live_path, orphan_path])

            summary = run_cleanup(database, limits=LIMITS)

        self.assertEqual(summary.orphaned_objects_deleted, 1)
        self.assertEqual(self.deleted_objects, [orphan_path])
        # The live job's object must survive.
        self.assertNotIn(live_path, self.deleted_objects)

    def test_a_ready_books_source_survives_a_sweep_that_cannot_see_its_job(self):
        """A book referencing an object is enough to keep it.

        The sweep decides what is orphaned by reconstructing paths from two
        Storage listings and deleting whatever it cannot match, leaving no
        event behind. A book that points at the object is independent evidence
        that it is not garbage, and costs one query to consult.
        """

        from storage.postgres import ingest_book
        from tests.fixtures import FILE_HASH, sample_book

        stranded_job = uuid4()
        path = f"{self.owner}/{stranded_job}/original.pdf"

        with connection(self.database_url) as database:
            book_id = ingest_book(
                database,
                sample_book(),
                owner_id=self.owner,
                title="Restored Book",
                author=None,
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
            database.execute(
                """
                update books
                set source_storage_bucket = 'book-sources',
                    source_storage_path = %s
                where id = %s
                """,
                (path, book_id),
            )
            # No ingestion_jobs row references it, so the sweep sees an orphan.
            self.bucket_contains([path])
            summary = run_cleanup(database, limits=LIMITS)

        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertEqual(self.deleted_objects, [])

    def test_an_unreachable_bucket_does_not_fail_the_pass(self):
        self.list_prefix.side_effect = IngestionError(ErrorCode.STORAGE_UNAVAILABLE)

        with connection(self.database_url) as database:
            summary = run_cleanup(database, limits=LIMITS)

        self.assertEqual(summary.orphaned_objects_deleted, 0)
        self.assertGreaterEqual(summary.deletions_failed, 1)

    def test_storage_failures_leave_the_job_eligible_for_the_next_pass(self):
        self.delete_object.side_effect = IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE
        )
        with connection(self.database_url) as database:
            expired = self.job(
                database,
                status="cancelled",
                age_interval="30 days",
                completed_interval="30 days",
            )

            summary = run_cleanup(database, limits=LIMITS)
            job = get_job(database, owner_id=self.owner, job_id=expired)

        self.assertEqual(summary.expired_sources_deleted, 0)
        self.assertEqual(summary.deletions_failed, 1)
        self.assertNotIn("source_deleted_at", job.provenance)


if __name__ == "__main__":
    unittest.main()


class OrphanSweepProtectionTests(unittest.TestCase):
    """What the sweep must never delete, and what it must never try to.

    This is the one deletion path with no job row to attach an event to, so it
    is the one where a mistake leaves only a log line behind.
    """

    def test_a_book_scoped_prefix_is_not_a_job_directory(self) -> None:
        """Reading copies live under `book-<id>/`, not a job UUID.

        Building a source path inside one asks Storage to delete something
        that was never there, which every sweep then counts as a failure.
        """

        self.assertIsNone(_JOB_PREFIX.fullmatch("book-536"))
        self.assertIsNone(_JOB_PREFIX.fullmatch("viewer"))

    def test_a_job_directory_is_recognised(self) -> None:
        self.assertIsNotNone(
            _JOB_PREFIX.fullmatch("afd7a837-3746-412e-994c-be29c95dd144")
        )
