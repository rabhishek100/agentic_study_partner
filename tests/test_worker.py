"""The worker loop: claiming, failure recording, cleanup, and shutdown."""

import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.errors import ErrorCode, IngestionError
from ingestion.jobs import get_job
from ingestion.pipeline import CancellationRequested, PipelineDependencies
from ingestion.states import Status
from storage.database import connection
from tests.pdf_fixtures import structured_pdf
from tests.test_ingestion_pipeline import (
    LIMITS,
    DeterministicEmbedder,
    PipelineFixture,
    stub_parsed_book,
)
from worker.main import Worker


class WorkerTests(PipelineFixture):
    def setUp(self):
        super().setUp()
        # The pipeline imports the parser lazily, so patch it at its source.
        self.parser = patch(
            "parsing.parser.parse_book",
            side_effect=lambda *a, **k: stub_parsed_book(),
        )
        self.parser.start()
        self.addCleanup(self.parser.stop)
        self.worker = Worker(
            worker_id="test-worker",
            limits=LIMITS,
            database_url=self.database_url,
            dependencies=PipelineDependencies(embedder_factory=DeterministicEmbedder),
            temporary_root=self.directory / "work",
        )

    def test_an_idle_worker_reports_that_it_did_nothing(self):
        self.assertFalse(self.worker.run_once())

    def test_a_queued_job_runs_to_a_ready_book(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))

        self.assertTrue(self.worker.run_once())

        finished = self.job_now(job.id)
        self.assertEqual(finished.status, Status.READY)
        self.assertIsNotNone(finished.book_id)
        self.assertIsNone(finished.lease_owner)

    def test_the_job_directory_is_removed_afterwards(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))

        self.worker.run_once()

        self.assertFalse(self.worker.work_directory(str(job.id)).exists())

    def test_a_permanent_failure_is_recorded_and_not_retried(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with patch(
            "ingestion.pipeline.preflight",
            side_effect=IngestionError(ErrorCode.ENCRYPTED_PDF),
        ):
            self.worker.run_once()

        failed = self.job_now(job.id)
        self.assertEqual(failed.status, Status.FAILED)
        self.assertEqual(failed.last_error_code, ErrorCode.ENCRYPTED_PDF)
        self.assertFalse(failed.last_error_retryable)
        self.assertIsNone(failed.lease_owner)
        self.assertIsNotNone(failed.completed_at)

    def test_a_transient_failure_is_rescheduled_with_backoff(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with patch(
            "ingestion.pipeline.preflight",
            side_effect=IngestionError(ErrorCode.STORAGE_UNAVAILABLE),
        ):
            self.worker.run_once()

        rescheduled = self.job_now(job.id)
        self.assertEqual(rescheduled.status, Status.RETRY_SCHEDULED)
        self.assertTrue(rescheduled.last_error_retryable)
        self.assertIsNotNone(rescheduled.next_attempt_at)
        self.assertIsNone(rescheduled.lease_owner)

    def test_a_failure_logs_why_even_though_the_reader_is_not_told(self):
        """`detail` exists for structured logs and was never written to one,
        so a rejected book showed only its generic safe message and the
        specific reason - which page, which headings - was discarded.
        """

        import json

        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with patch(
            "ingestion.pipeline.preflight",
            side_effect=IngestionError(
                ErrorCode.EXTRACTION_CONTRACT_VIOLATION,
                detail="could not resolve 2 outline headings sharing page 42",
            ),
        ):
            with self.assertLogs("study_partner.worker", level="WARNING") as logs:
                self.worker.run_once()

        record = next(
            record for record in logs.records if record.msg == "job attempt failed"
        )
        self.assertEqual(
            record.error_detail,
            "could not resolve 2 outline headings sharing page 42",
        )

        # And it survives the JSON formatter, which drops any field it does
        # not name explicitly.
        from worker.main import JsonFormatter

        payload = json.loads(JsonFormatter().format(record))
        self.assertIn("sharing page 42", payload["error_detail"])

        # The reader still sees only the safe message.
        failed = self.job_now(job.id)
        self.assertNotIn("page 42", failed.last_error_message or "")

    def test_an_unexpected_error_is_still_recorded_safely(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with patch(
            "ingestion.pipeline.preflight",
            side_effect=RuntimeError("something surprising at /tmp/secret"),
        ):
            self.worker.run_once()

        recorded = self.job_now(job.id)
        self.assertIn(
            recorded.status, (Status.RETRY_SCHEDULED, Status.FAILED)
        )
        self.assertEqual(recorded.last_error_code, ErrorCode.UNEXPECTED_ERROR)
        # The raw exception text never reaches the stored message.
        self.assertNotIn("/tmp/secret", recorded.last_error_message)

    def test_attempts_are_bounded(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with connection(self.database_url) as database:
            database.execute(
                "update ingestion_jobs set max_attempts = 2 where id = %s", (job.id,)
            )

        with patch(
            "ingestion.pipeline.preflight",
            side_effect=IngestionError(ErrorCode.STORAGE_UNAVAILABLE),
        ):
            for _ in range(2):
                with connection(self.database_url) as database:
                    database.execute(
                        "update ingestion_jobs set next_attempt_at = now() "
                        "where id = %s",
                        (job.id,),
                    )
                self.worker.run_once()

        exhausted = self.job_now(job.id)
        self.assertEqual(exhausted.status, Status.FAILED)
        self.assertEqual(exhausted.attempt_count, 2)

    def test_a_cancelled_job_is_not_recorded_as_a_failure(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with patch(
            "ingestion.pipeline.preflight", side_effect=CancellationRequested(str(job.id))
        ):
            with connection(self.database_url) as database:
                database.execute(
                    "update ingestion_jobs set cancellation_requested_at = now() "
                    "where id = %s",
                    (job.id,),
                )
            self.worker.run_once()

        stopped = self.job_now(job.id)
        self.assertNotEqual(stopped.status, Status.FAILED)

    def test_an_abandoned_job_is_reclaimed_before_claiming_new_work(self):
        job = self.queued_job(structured_pdf(self.directory / "book.pdf"))
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'parsing', stage = 'parse_pages',
                    lease_owner = 'dead-worker', attempt_count = 1,
                    lease_expires_at = now() - interval '1 minute'
                where id = %s
                """,
                (job.id,),
            )

        self.assertEqual(self.worker.recover_abandoned_jobs(), 1)

        reclaimed = self.job_now(job.id)
        self.assertEqual(reclaimed.status, Status.RETRY_SCHEDULED)
        self.assertEqual(reclaimed.last_error_code, ErrorCode.LEASE_EXPIRED)

    def test_a_stop_request_ends_the_loop(self):
        self.worker.request_stop()

        self.assertTrue(self.worker.stopping)
        self.worker.run()  # returns instead of polling forever

    def test_the_worker_identifies_itself_on_the_lease(self):
        self.queued_job(structured_pdf(self.directory / "book.pdf"))

        claimed = self.worker.claim()

        self.assertEqual(claimed.lease_owner, "test-worker")
        with connection(self.database_url) as database:
            stored = get_job(
                database, owner_id=self.owner, job_id=claimed.id
            )
        self.assertEqual(stored.lease_owner, "test-worker")

    def test_the_temporary_directory_is_job_scoped(self):
        first = self.worker.work_directory("job-a")
        second = self.worker.work_directory("job-b")

        self.assertNotEqual(first, second)
        self.assertIn("study-partner-ingestion", str(first))
        self.assertTrue(str(first).startswith(str(Path(self.directory / "work"))))


if __name__ == "__main__":
    unittest.main()
