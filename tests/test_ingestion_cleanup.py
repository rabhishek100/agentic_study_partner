"""Retention behaviour: abandoned uploads and expired sources."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from ingestion.cleanup import run_cleanup
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
