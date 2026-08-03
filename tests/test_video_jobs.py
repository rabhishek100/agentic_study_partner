"""Independent video queue claiming, leases, cancellation, and retry."""

import unittest
from decimal import Decimal
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.jobs import (
    VideoJobConflictError,
    VideoJobNotFoundError,
    advance_stage,
    begin_stage_checkpoint,
    claim_next_job,
    complete_stage_checkpoint,
    get_job,
    reclaim_expired_leases,
    renew_lease,
    request_cancellation,
    retry_job,
)
from video.errors import VideoBudgetExceeded
from video.repository import create_youtube_video
from video.states import Stage, Status


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
                attempt_count=1,
                lease_seconds=60,
            )
            renewed = renew_lease(
                database,
                job_id=created.job_id,
                worker_id="video-worker-a",
                attempt_count=1,
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
                attempt_count=1,
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

    def test_checkpoint_completion_is_idempotent_costed_and_stage_guarded(self) -> None:
        dependency = "a" * 64
        with connection(self.database_url) as database:
            created = self.create(database)
            claimed = claim_next_job(database, worker_id="video-worker")
            started = begin_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=dependency,
            )
            completed = complete_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=dependency,
                output_manifest={"source_hash": "b" * 64},
                cost_usd="0.100000",
            )
            replay = complete_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=dependency,
                output_manifest={"ignored": True},
                cost_usd="0.100000",
            )
            advanced = advance_stage(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                next_stage=Stage.MEDIA_METADATA,
            )
            costs = database.execute(
                """
                select j.actual_cost_usd as job_cost,
                       v.actual_cost_usd as version_cost
                from video.ingestion_jobs j
                join video.ingestion_versions v on v.id = j.target_version_id
                where j.id = %s
                """,
                (created.job_id,),
            ).fetchone()

        self.assertEqual(started.status, "running")
        self.assertEqual(completed.status, "complete")
        self.assertTrue(replay.reused)
        self.assertEqual(replay.output_manifest, {"source_hash": "b" * 64})
        self.assertEqual(costs["job_cost"], Decimal("0.100000"))
        self.assertEqual(costs["version_cost"], Decimal("0.100000"))
        self.assertEqual(advanced.stage, Stage.MEDIA_METADATA)

    def test_checkpoint_dependency_change_and_budget_failure_are_atomic(self) -> None:
        first_hash, second_hash = "c" * 64, "d" * 64
        with connection(self.database_url) as database:
            created = self.create(database)
            claimed = claim_next_job(database, worker_id="video-worker")
            begin_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=first_hash,
            )
            changed = begin_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=second_hash,
            )
            with self.assertRaises(VideoBudgetExceeded):
                complete_stage_checkpoint(
                    database,
                    job_id=created.job_id,
                    worker_id="video-worker",
                    attempt_count=claimed.attempt_count,
                    stage=Stage.ACQUIRE_SOURCE,
                    dependency_hash=second_hash,
                    output_manifest={"should": "roll back"},
                    cost_usd="0.500001",
                )
            state = database.execute(
                """
                select c.status, c.output_manifest_json, c.actual_cost_usd,
                       j.actual_cost_usd as job_cost,
                       v.actual_cost_usd as version_cost
                from video.ingestion_stage_checkpoints c
                join video.ingestion_jobs j
                  on j.target_version_id = c.ingestion_version_id
                join video.ingestion_versions v on v.id = c.ingestion_version_id
                where j.id = %s and c.stage = 'acquire_source'
                """,
                (created.job_id,),
            ).fetchone()

        self.assertEqual(changed.dependency_hash, second_hash)
        self.assertEqual(changed.attempt_count, 2)
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["output_manifest_json"], {})
        self.assertEqual(state["actual_cost_usd"], Decimal("0"))
        self.assertEqual(state["job_cost"], Decimal("0"))
        self.assertEqual(state["version_cost"], Decimal("0"))

    def test_expired_lease_retries_then_exhausts_with_attempt_fencing(self) -> None:
        dependency = "e" * 64
        with connection(self.database_url) as database:
            created = self.create(database)
            first = claim_next_job(database, worker_id="stable-worker", lease_seconds=60)
            begin_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="stable-worker",
                attempt_count=first.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=dependency,
            )
            database.execute(
                "update video.ingestion_jobs set lease_expires_at = now() - interval '1 second' where id = %s",
                (created.job_id,),
            )
            reclaimed = reclaim_expired_leases(database, retry_delay_seconds=0)
            reclaimed_again = reclaim_expired_leases(database, retry_delay_seconds=0)
            second = claim_next_job(database, worker_id="stable-worker", lease_seconds=60)
            stale_renewal = renew_lease(
                database,
                job_id=created.job_id,
                worker_id="stable-worker",
                attempt_count=first.attempt_count,
            )
            with self.assertRaises(VideoJobConflictError):
                begin_stage_checkpoint(
                    database,
                    job_id=created.job_id,
                    worker_id="stable-worker",
                    attempt_count=first.attempt_count,
                    stage=Stage.ACQUIRE_SOURCE,
                    dependency_hash=dependency,
                )
            resumed = begin_stage_checkpoint(
                database,
                job_id=created.job_id,
                worker_id="stable-worker",
                attempt_count=second.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=dependency,
            )
            database.execute(
                """
                update video.ingestion_jobs
                set attempt_count = max_attempts,
                    lease_expires_at = now() - interval '1 second'
                where id = %s
                """,
                (created.job_id,),
            )
            exhausted = reclaim_expired_leases(database, retry_delay_seconds=0)
            final_job = get_job(
                database, owner_id=self.owner, job_id=created.job_id
            )
            version = database.execute(
                "select status, error_code from video.ingestion_versions where id = %s",
                (created.version_id,),
            ).fetchone()
            video = database.execute(
                "select readiness_status from video.videos where id = %s",
                (created.video_id,),
            ).fetchone()

        self.assertEqual(reclaimed, 1)
        self.assertEqual(reclaimed_again, 0)
        self.assertEqual(second.attempt_count, 2)
        self.assertFalse(stale_renewal)
        self.assertEqual(resumed.attempt_count, 2)
        self.assertEqual(exhausted, 1)
        self.assertEqual(final_job.status, Status.FAILED)
        self.assertEqual(final_job.last_error_code, "attempts_exhausted")
        self.assertFalse(final_job.last_error_retryable)
        self.assertEqual(version["status"], "failed")
        self.assertEqual(version["error_code"], "attempts_exhausted")
        self.assertEqual(video["readiness_status"], "failed")


if __name__ == "__main__":
    unittest.main()
    reclaim_expired_leases,
