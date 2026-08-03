"""Independent video queue claiming, leases, cancellation, and retry."""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.jobs import (
    VideoJobConflictError,
    VideoJobNotFoundError,
    claim_next_job,
    get_job,
    renew_lease,
    request_cancellation,
    retry_job,
)
from video.repository import create_youtube_video
from video.states import Status


class VideoJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-jobs.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )

    def create(self, database, *, owner=None, video_id="abcdefghijk"):
        return create_youtube_video(
            database,
            owner_id=owner or self.owner,
            idempotency_key=uuid4(),
            url=f"https://youtu.be/{video_id}",
            title="Lecture",
        )

    def test_job_reads_are_owner_scoped(self) -> None:
        with connection(self.database_url) as database:
            created = self.create(database)
            loaded = get_job(
                database, owner_id=self.owner, job_id=created.job_id
            )
            with self.assertRaises(VideoJobNotFoundError):
                get_job(database, owner_id=self.other_owner, job_id=created.job_id)

        self.assertEqual(loaded.video_id, created.video_id)
        self.assertEqual(loaded.status, Status.QUEUED)

    def test_claim_and_lease_are_exclusive_and_worker_guarded(self) -> None:
        with connection(self.database_url) as database:
            created = self.create(database)
            claimed = claim_next_job(
                database, worker_id="video-worker-a", lease_seconds=60
            )
            none_left = claim_next_job(
                database, worker_id="video-worker-b", lease_seconds=60
            )
            wrong_worker = renew_lease(
                database,
                job_id=created.job_id,
                worker_id="video-worker-b",
                lease_seconds=60,
            )
            renewed = renew_lease(
                database,
                job_id=created.job_id,
                worker_id="video-worker-a",
                lease_seconds=60,
            )
            database.execute(
                """
                update video.ingestion_jobs
                set lease_expires_at = now() - interval '1 second'
                where id = %s
                """,
                (created.job_id,),
            )
            expired = renew_lease(
                database,
                job_id=created.job_id,
                worker_id="video-worker-a",
                lease_seconds=60,
            )

        self.assertEqual(claimed.status, Status.RUNNING)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertIsNone(none_left)
        self.assertFalse(wrong_worker)
        self.assertTrue(renewed)
        self.assertFalse(expired)

    def test_cancellation_is_immediate_when_queued_and_cooperative_when_running(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            queued = self.create(database)
            cancelled = request_cancellation(
                database, owner_id=self.owner, job_id=queued.job_id
            )
            cancelled_again = request_cancellation(
                database, owner_id=self.owner, job_id=queued.job_id
            )
            version = database.execute(
                "select status from video.ingestion_versions where id = %s",
                (queued.version_id,),
            ).fetchone()
            video = database.execute(
                "select readiness_status from video.videos where id = %s",
                (queued.video_id,),
            ).fetchone()

            running_source = self.create(database, video_id="lmnopqrstuv")
            claimed = claim_next_job(database, worker_id="video-worker")
            requested = request_cancellation(
                database, owner_id=self.owner, job_id=running_source.job_id
            )
            running_version = database.execute(
                "select status from video.ingestion_versions where id = %s",
                (running_source.version_id,),
            ).fetchone()

        self.assertEqual(cancelled.status, Status.CANCELLED)
        self.assertEqual(cancelled_again.status, Status.CANCELLED)
        self.assertEqual(version["status"], "cancelled")
        self.assertEqual(video["readiness_status"], "failed")
        self.assertEqual(claimed.id, running_source.job_id)
        self.assertEqual(requested.status, Status.RUNNING)
        self.assertTrue(requested.cancellation_requested)
        self.assertEqual(running_version["status"], "building")

    def test_manual_retry_requires_retryable_failure_and_reopens_version(self) -> None:
        with connection(self.database_url) as database:
            created = self.create(database)
            database.execute(
                """
                update video.ingestion_jobs
                set status = 'failed', completed_at = now(), attempt_count = 3,
                    last_error_code = 'provider_timeout',
                    last_error_message = 'Provider timed out',
                    last_error_retryable = true
                where id = %s
                """,
                (created.job_id,),
            )
            database.execute(
                """
                update video.ingestion_versions
                set status = 'failed', completed_at = now(),
                    error_code = 'provider_timeout',
                    error_message = 'Provider timed out'
                where id = %s
                """,
                (created.version_id,),
            )
            retried = retry_job(
                database, owner_id=self.owner, job_id=created.job_id
            )
            version = database.execute(
                """
                select status, completed_at, error_code
                from video.ingestion_versions where id = %s
                """,
                (created.version_id,),
            ).fetchone()
            with self.assertRaises(VideoJobConflictError):
                retry_job(database, owner_id=self.owner, job_id=created.job_id)

        self.assertEqual(retried.status, Status.RETRY_SCHEDULED)
        self.assertEqual(retried.attempt_count, 0)
        self.assertIsNone(retried.last_error_code)
        self.assertEqual(version["status"], "building")
        self.assertIsNone(version["completed_at"])
        self.assertIsNone(version["error_code"])


if __name__ == "__main__":
    unittest.main()
