"""Postgres behaviour of the durable ingestion job queue."""

import unittest
from uuid import uuid4

from ingestion.config import IngestionLimits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.jobs import (
    IngestionJob,
    JobConflictError,
    JobNotFoundError,
    append_event,
    cancel_running_job,
    claim_next_job,
    complete_job,
    confirm_outline_review,
    create_job,
    fail_job,
    get_job,
    list_events,
    list_jobs,
    mark_upload_complete,
    outline_review,
    pause_for_outline_review,
    reclaim_expired_leases,
    record_progress,
    renew_lease,
    request_cancellation,
    retry_job,
    schedule_retry,
)
from ingestion.states import Stage, Status
from storage.database import connection, resolve_database_url
from tests.postgres import require_empty_ingestion_queue


LIMITS = IngestionLimits(max_queued_jobs_per_owner=3, lease_seconds=300)


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        require_empty_ingestion_queue(self)
        self.database_url = resolve_database_url()
        self.owner = str(uuid4())
        self.other_owner = str(uuid4())
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@test.local"),
                )
        self.addCleanup(self._discard)

    def _discard(self):
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )

    def create(self, database, *, owner=None, filename="book.pdf", size=1024):
        job, created = create_job(
            database,
            owner_id=owner or self.owner,
            idempotency_key=uuid4(),
            original_filename=filename,
            content_type="application/pdf",
            content_length=size,
            limits=LIMITS,
        )
        self.assertTrue(created)
        return job

    def queued(self, database, **kwargs):
        job = self.create(database, **kwargs)
        return mark_upload_complete(
            database,
            owner_id=job.owner_id,
            job_id=job.id,
            verified_size_bytes=job.declared_size_bytes,
            limits=LIMITS,
        )

    def test_create_reserves_an_owner_scoped_immutable_path(self):
        with connection(self.database_url) as database:
            job = self.create(database)

        self.assertEqual(job.status, Status.AWAITING_UPLOAD)
        self.assertEqual(job.storage_bucket, "book-sources")
        self.assertEqual(job.storage_path, f"{job.owner_id}/{job.id}/original.pdf")
        self.assertEqual(job.attempt_count, 0)

    def test_create_is_idempotent_for_one_key(self):
        key = uuid4()
        with connection(self.database_url) as database:
            first, created_first = create_job(
                database,
                owner_id=self.owner,
                idempotency_key=key,
                original_filename="book.pdf",
                content_type="application/pdf",
                content_length=2048,
                limits=LIMITS,
            )
            second, created_second = create_job(
                database,
                owner_id=self.owner,
                idempotency_key=key,
                original_filename="different-name.pdf",
                content_type="application/pdf",
                content_length=4096,
                limits=LIMITS,
            )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.original_filename, "book.pdf")

    def test_the_same_key_for_two_owners_creates_two_jobs(self):
        key = uuid4()
        with connection(self.database_url) as database:
            mine, _ = create_job(
                database,
                owner_id=self.owner,
                idempotency_key=key,
                original_filename="book.pdf",
                content_type="application/pdf",
                content_length=2048,
                limits=LIMITS,
            )
            theirs, created = create_job(
                database,
                owner_id=self.other_owner,
                idempotency_key=key,
                original_filename="book.pdf",
                content_type="application/pdf",
                content_length=2048,
                limits=LIMITS,
            )

        self.assertTrue(created)
        self.assertNotEqual(mine.id, theirs.id)

    def test_create_rejects_unsupported_declarations(self):
        with connection(self.database_url) as database:
            for content_type, size, code in (
                ("image/png", 1024, ErrorCode.UNSUPPORTED_CONTENT_TYPE),
                ("application/pdf", 110_000_000, ErrorCode.SOURCE_TOO_LARGE),
            ):
                with self.subTest(content_type=content_type, size=size):
                    with self.assertRaises(IngestionError) as caught:
                        create_job(
                            database,
                            owner_id=self.owner,
                            idempotency_key=uuid4(),
                            original_filename="book.pdf",
                            content_type=content_type,
                            content_length=size,
                            limits=LIMITS,
                        )
                    self.assertEqual(caught.exception.code, code)
                    self.assertFalse(caught.exception.retryable)

    def test_create_enforces_the_pending_quota_per_owner(self):
        with connection(self.database_url) as database:
            for _ in range(LIMITS.max_queued_jobs_per_owner):
                self.create(database)

            with self.assertRaises(IngestionError) as caught:
                self.create(database)
            self.assertEqual(caught.exception.code, ErrorCode.QUOTA_EXCEEDED)

            # Another owner's queue is unaffected.
            self.create(database, owner=self.other_owner)

    def test_filenames_are_stored_as_display_metadata_only(self):
        with connection(self.database_url) as database:
            job = self.create(database, filename="../../etc/passwd.pdf")

        self.assertEqual(job.original_filename, "passwd.pdf")
        self.assertNotIn("..", job.storage_path)

    def test_completing_an_upload_queues_the_job_once(self):
        with connection(self.database_url) as database:
            job = self.create(database)
            queued = mark_upload_complete(
                database,
                owner_id=self.owner,
                job_id=job.id,
                verified_size_bytes=1024,
                limits=LIMITS,
            )
            repeated = mark_upload_complete(
                database,
                owner_id=self.owner,
                job_id=job.id,
                verified_size_bytes=1024,
                limits=LIMITS,
            )

        self.assertEqual(queued.status, Status.QUEUED)
        self.assertEqual(queued.verified_size_bytes, 1024)
        self.assertEqual(repeated.status, Status.QUEUED)

    def test_completing_rejects_an_oversized_stored_object(self):
        with connection(self.database_url) as database:
            job = self.create(database)
            with self.assertRaises(IngestionError) as caught:
                mark_upload_complete(
                    database,
                    owner_id=self.owner,
                    job_id=job.id,
                    verified_size_bytes=110_000_000,
                    limits=LIMITS,
                )
            self.assertEqual(caught.exception.code, ErrorCode.SOURCE_TOO_LARGE)
            self.assertEqual(
                get_job(database, owner_id=self.owner, job_id=job.id).status,
                Status.AWAITING_UPLOAD,
            )

    def test_outline_review_is_durable_and_confirmation_requeues_the_job(self):
        proposal = [
            (1, "Chapter 1", 1),
            (2, "First section", 2),
            (1, "Chapter 2", 4),
        ]
        with connection(self.database_url) as database:
            queued = self.queued(database)
            database.execute(
                """
                update ingestion_jobs
                set status = 'validating', stage = 'preflight',
                    file_hash = %s, page_count = 6,
                    lease_owner = 'test-worker',
                    lease_expires_at = now() + interval '5 minutes'
                where id = %s
                """,
                ("a" * 64, queued.id),
            )
            paused = pause_for_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                proposal=proposal,
                reasons=("invalid_destinations",),
                outline_source="deterministic_proposal",
                proposer_version="proposal-test-v1",
            )

            self.assertEqual(paused.status, Status.NEEDS_TOC_REVIEW)
            self.assertEqual(paused.stage, Stage.PROPOSE_TOC)
            self.assertIsNone(paused.lease_owner)
            self.assertEqual(outline_review(paused)["entries"][1]["title"], "First section")

            confirmed = confirm_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                toc=proposal,
            )
            repeated = confirm_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                toc=proposal,
            )

        self.assertEqual(confirmed.status, Status.QUEUED)
        self.assertEqual(confirmed.stage, Stage.PREFLIGHT)
        self.assertEqual(repeated.status, Status.QUEUED)
        review = outline_review(confirmed)
        self.assertEqual(review["state"], "confirmed")
        self.assertEqual(review["source_sha256"], "a" * 64)
        self.assertEqual(review["confirmed_entries"], review["entries"])

    def test_confirming_banks_the_time_the_reviewer_took(self):
        """The wait has to be recorded on the way out, or it is unrecoverable.

        Once the job resumes, ``stage_started_at`` moves to the new stage and
        nothing on the row remembers how long a person was thinking. Without
        the banked figure the resumed job counts the reviewer's night as
        pipeline time and reports itself as overrunning.
        """

        proposal = [(1, "Chapter 1", 1)]
        with connection(self.database_url) as database:
            queued = self.queued(database)
            database.execute(
                """
                update ingestion_jobs
                set status = 'validating', stage = 'preflight',
                    file_hash = %s, page_count = 6
                where id = %s
                """,
                ("c" * 64, queued.id),
            )
            pause_for_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                proposal=proposal,
                reasons=("missing_outline",),
                outline_source="deterministic_proposal",
                proposer_version="proposal-test-v1",
            )
            # Stand the review clock back an hour rather than sleeping.
            database.execute(
                "update ingestion_jobs "
                "set stage_started_at = now() - interval '1 hour' where id = %s",
                (queued.id,),
            )
            confirmed = confirm_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                toc=proposal,
            )

        self.assertAlmostEqual(confirmed.awaiting_input_seconds, 3600, delta=30)

    def test_outline_confirmation_is_owner_scoped_and_cannot_be_changed(self):
        proposal = [(1, "Chapter 1", 1)]
        with connection(self.database_url) as database:
            queued = self.queued(database)
            database.execute(
                """
                update ingestion_jobs
                set status = 'validating', stage = 'preflight',
                    file_hash = %s, page_count = 6
                where id = %s
                """,
                ("b" * 64, queued.id),
            )
            pause_for_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                proposal=proposal,
                reasons=("missing_outline",),
                outline_source="deterministic_proposal",
                proposer_version="proposal-test-v1",
            )

            with self.assertRaises(JobNotFoundError):
                confirm_outline_review(
                    database,
                    owner_id=self.other_owner,
                    job_id=queued.id,
                    toc=proposal,
                )

            confirm_outline_review(
                database,
                owner_id=self.owner,
                job_id=queued.id,
                toc=proposal,
            )
            with self.assertRaises(JobConflictError):
                confirm_outline_review(
                    database,
                    owner_id=self.owner,
                    job_id=queued.id,
                    toc=[(1, "Different chapter", 1)],
                )

    def test_another_owner_cannot_read_or_change_a_job(self):
        with connection(self.database_url) as database:
            job = self.queued(database)

            for operation in (
                lambda: get_job(database, owner_id=self.other_owner, job_id=job.id),
                lambda: request_cancellation(
                    database, owner_id=self.other_owner, job_id=job.id
                ),
                lambda: retry_job(
                    database, owner_id=self.other_owner, job_id=job.id, limits=LIMITS
                ),
                lambda: mark_upload_complete(
                    database,
                    owner_id=self.other_owner,
                    job_id=job.id,
                    verified_size_bytes=1024,
                    limits=LIMITS,
                ),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(JobNotFoundError):
                        operation()

            self.assertEqual(
                get_job(database, owner_id=self.owner, job_id=job.id).status,
                Status.QUEUED,
            )
            self.assertEqual(list_jobs(database, owner_id=self.other_owner), [])

    def test_claiming_starts_the_pipeline_and_takes_a_lease(self):
        with connection(self.database_url) as database:
            job = self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

        self.assertIsInstance(claimed, IngestionJob)
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(claimed.status, Status.VALIDATING)
        self.assertEqual(claimed.stage, Stage.VERIFY_UPLOAD)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(claimed.lease_owner, "worker-1")
        self.assertIsNotNone(claimed.lease_expires_at)
        self.assertIsNotNone(claimed.started_at)

    def test_only_one_job_per_owner_occupies_the_worker(self):
        with connection(self.database_url) as database:
            self.queued(database)
            self.queued(database)

            first = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            second = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_a_second_worker_skips_a_locked_row(self):
        with connection(self.database_url) as database:
            self.queued(database)
            self.queued(database, owner=self.other_owner)

        with connection(self.database_url) as first_worker:
            with first_worker.transaction():
                first_worker.execute(
                    """
                    select id from ingestion_jobs
                    where owner_id = %s and status = 'queued'
                    for update
                    """,
                    (self.owner,),
                ).fetchone()

                with connection(self.database_url) as second_worker:
                    claimed = claim_next_job(
                        second_worker, worker_id="worker-2", limits=LIMITS
                    )

        self.assertIsNotNone(claimed)
        self.assertEqual(str(claimed.owner_id), self.other_owner)

    def test_claiming_returns_none_when_nothing_is_eligible(self):
        with connection(self.database_url) as database:
            self.create(database)  # still awaiting upload
            self.assertIsNone(
                claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            )

    def test_cancelled_queued_jobs_are_never_claimed(self):
        with connection(self.database_url) as database:
            job = self.queued(database)
            cancelled = request_cancellation(
                database, owner_id=self.owner, job_id=job.id
            )

            self.assertEqual(cancelled.status, Status.CANCELLED)
            self.assertIsNone(
                claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            )

    def test_cancelling_a_running_job_is_cooperative(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

            flagged = request_cancellation(
                database, owner_id=self.owner, job_id=claimed.id
            )
            self.assertEqual(flagged.status, Status.VALIDATING)
            self.assertTrue(flagged.cancellation_requested)

            stopped = cancel_running_job(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=Status.VALIDATING,
            )

        self.assertEqual(stopped.status, Status.CANCELLED)
        self.assertIsNotNone(stopped.completed_at)
        self.assertFalse(stopped.last_error_retryable)

    def test_progress_is_recorded_without_an_event_per_tick(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            before = len(
                list_events(database, owner_id=self.owner, job_id=claimed.id)
            )

            for completed in (10, 20, 30):
                record_progress(
                    database,
                    owner_id=self.owner,
                    job_id=claimed.id,
                    completed=completed,
                    total=120,
                    unit="pages",
                )

            job = get_job(database, owner_id=self.owner, job_id=claimed.id)
            after = len(list_events(database, owner_id=self.owner, job_id=claimed.id))

        self.assertEqual(job.progress_completed, 30)
        self.assertEqual(job.progress_total, 120)
        self.assertEqual(job.progress_unit, "pages")
        self.assertEqual(job.progress_percent, 25.0)
        self.assertEqual(before, after)

    def test_a_retryable_failure_reschedules_and_keeps_the_stage(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

            scheduled = schedule_retry(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=claimed.status,
                error=IngestionError(ErrorCode.STORAGE_UNAVAILABLE),
                delay_seconds=30,
            )

        self.assertEqual(scheduled.status, Status.RETRY_SCHEDULED)
        self.assertEqual(scheduled.stage, Stage.VERIFY_UPLOAD)
        self.assertIsNone(scheduled.lease_owner)
        self.assertTrue(scheduled.last_error_retryable)
        self.assertIsNotNone(scheduled.next_attempt_at)

    def test_a_scheduled_retry_is_not_claimed_before_its_backoff(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            schedule_retry(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=claimed.status,
                error=IngestionError(ErrorCode.PROVIDER_TIMEOUT),
                delay_seconds=600,
            )

            self.assertIsNone(
                claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            )

            database.execute(
                "update ingestion_jobs set next_attempt_at = now() - interval '1 second'"
                " where id = %s",
                (claimed.id,),
            )
            resumed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

        self.assertEqual(resumed.id, claimed.id)
        self.assertEqual(resumed.attempt_count, 2)

    def test_a_resumed_job_restarts_the_stage_it_stopped_in(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            database.execute(
                "update ingestion_jobs set status = 'embedding', "
                "stage = 'build_embeddings' where id = %s",
                (claimed.id,),
            )
            schedule_retry(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=Status.EMBEDDING,
                error=IngestionError(ErrorCode.PROVIDER_RATE_LIMITED),
                delay_seconds=0,
            )
            resumed = claim_next_job(database, worker_id="worker-2", limits=LIMITS)

        self.assertEqual(resumed.status, Status.EMBEDDING)
        self.assertEqual(resumed.stage, Stage.BUILD_EMBEDDINGS)

    def test_an_expired_lease_returns_the_job_to_the_queue(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            database.execute(
                "update ingestion_jobs set lease_expires_at = now() - interval "
                "'1 minute' where id = %s",
                (claimed.id,),
            )

            reclaimed = reclaim_expired_leases(database, limits=LIMITS)
            job = get_job(database, owner_id=self.owner, job_id=claimed.id)

        self.assertEqual(reclaimed, 1)
        self.assertEqual(job.status, Status.RETRY_SCHEDULED)
        self.assertEqual(job.last_error_code, ErrorCode.LEASE_EXPIRED)
        self.assertIsNone(job.lease_owner)

    def test_an_expired_lease_fails_the_job_once_attempts_run_out(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            database.execute(
                """
                update ingestion_jobs
                set lease_expires_at = now() - interval '1 minute',
                    attempt_count = max_attempts
                where id = %s
                """,
                (claimed.id,),
            )

            reclaim_expired_leases(database, limits=LIMITS)
            job = get_job(database, owner_id=self.owner, job_id=claimed.id)

        self.assertEqual(job.status, Status.FAILED)
        self.assertIsNotNone(job.completed_at)

    def test_lease_renewal_fails_once_another_worker_takes_over(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

            self.assertTrue(
                renew_lease(
                    database, job_id=claimed.id, worker_id="worker-1", limits=LIMITS
                )
            )
            self.assertFalse(
                renew_lease(
                    database, job_id=claimed.id, worker_id="worker-2", limits=LIMITS
                )
            )

    def test_a_stale_worker_cannot_overwrite_a_reassigned_job(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            database.execute(
                "update ingestion_jobs set status = 'parsing', stage = 'parse_pages' "
                "where id = %s",
                (claimed.id,),
            )

            with self.assertRaises(JobConflictError):
                complete_job(
                    database,
                    owner_id=self.owner,
                    job_id=claimed.id,
                    current_status=Status.VALIDATING,
                    book_id=1,
                )

    def test_only_a_retryable_failure_can_be_retried_manually(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            fail_job(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=claimed.status,
                error=IngestionError(ErrorCode.ENCRYPTED_PDF),
            )

            with self.assertRaises(IngestionError) as caught:
                retry_job(
                    database, owner_id=self.owner, job_id=claimed.id, limits=LIMITS
                )
            self.assertEqual(caught.exception.code, ErrorCode.ATTEMPTS_EXHAUSTED)

    def test_retrying_a_retryable_failure_resets_the_attempt_budget(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            failed = fail_job(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=claimed.status,
                error=IngestionError(ErrorCode.STORAGE_UNAVAILABLE),
            )
            self.assertTrue(failed.last_error_retryable)

            rescheduled = retry_job(
                database, owner_id=self.owner, job_id=claimed.id, limits=LIMITS
            )

        self.assertEqual(rescheduled.status, Status.RETRY_SCHEDULED)
        self.assertEqual(rescheduled.attempt_count, 0)
        self.assertIsNone(rescheduled.last_error_code)
        self.assertIsNone(rescheduled.completed_at)

    def test_a_manual_retry_is_claimable_and_resumes_its_stage(self):
        """Regression: retry of a job that failed mid-embedding.

        The old design sent the job back to ``queued`` while keeping
        ``stage = build_embeddings``; the claim then attempted the forbidden
        ``queued -> embedding`` transition on every poll and wedged the queue.
        """

        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            database.execute(
                "update ingestion_jobs set status = 'embedding', "
                "stage = 'build_embeddings' where id = %s",
                (claimed.id,),
            )
            fail_job(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=Status.EMBEDDING,
                error=IngestionError(ErrorCode.STORAGE_UNAVAILABLE),
            )
            retry_job(
                database, owner_id=self.owner, job_id=claimed.id, limits=LIMITS
            )

            resumed = claim_next_job(database, worker_id="worker-2", limits=LIMITS)

        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.id, claimed.id)
        self.assertEqual(resumed.status, Status.EMBEDDING)
        self.assertEqual(resumed.stage, Stage.BUILD_EMBEDDINGS)

    def test_claiming_survives_a_stage_its_status_cannot_resume(self):
        """A hand-corrupted row restarts the pipeline instead of wedging it."""

        with connection(self.database_url) as database:
            job = self.queued(database)
            database.execute(
                "update ingestion_jobs set stage = 'build_embeddings' "
                "where id = %s",
                (job.id,),
            )

            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, Status.VALIDATING)
        self.assertEqual(claimed.stage, Stage.VERIFY_UPLOAD)

    def test_a_failed_job_exposes_only_a_safe_message(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            failed = fail_job(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=claimed.status,
                error=IngestionError(
                    ErrorCode.INVALID_PDF,
                    detail="/tmp/job-42/original.pdf: xref table broken at 0x1f",
                ),
            )

        self.assertEqual(failed.last_error_code, ErrorCode.INVALID_PDF)
        self.assertNotIn("/tmp", failed.last_error_message)
        self.assertNotIn("xref", failed.last_error_message)
        self.assertFalse(failed.last_error_retryable)

    def test_completing_a_job_publishes_its_book(self):
        with connection(self.database_url) as database:
            self.queued(database)
            claimed = claim_next_job(database, worker_id="worker-1", limits=LIMITS)
            book_id = database.execute(
                """
                insert into books (
                    owner_id, title, source_path, source_filename, file_hash,
                    parser_version, parsed_at, status
                ) values (%s, 'T', 'p', 'f', %s, 'v', now(), 'processing')
                returning id
                """,
                (self.owner, "c" * 64),
            ).fetchone()["id"]

            done = complete_job(
                database,
                owner_id=self.owner,
                job_id=claimed.id,
                current_status=claimed.status,
                book_id=book_id,
            )
            events = list_events(database, owner_id=self.owner, job_id=claimed.id)

        self.assertEqual(done.status, Status.READY)
        self.assertEqual(done.book_id, book_id)
        self.assertIsNone(done.lease_owner)
        self.assertIsNotNone(done.completed_at)
        self.assertEqual(
            [event["event_type"] for event in events],
            ["created", "queued", "claimed", "ready"],
        )

    def test_events_are_owner_scoped(self):
        with connection(self.database_url) as database:
            job = self.create(database)
            append_event(
                database,
                owner_id=self.owner,
                job_id=job.id,
                event_type="note",
                message="visible to its owner only",
            )

            self.assertTrue(list_events(database, owner_id=self.owner, job_id=job.id))
            self.assertEqual(
                list_events(database, owner_id=self.other_owner, job_id=job.id), []
            )


if __name__ == "__main__":
    unittest.main()
