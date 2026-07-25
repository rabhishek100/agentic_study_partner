"""State-machine, retry-policy, and limit rules that need no database."""

import os
import unittest
from unittest.mock import patch

from ingestion.config import IngestionLimits, load_limits
from ingestion.errors import (
    BACKOFF_SECONDS,
    MAXIMUM_BACKOFF_SECONDS,
    SAFE_MESSAGES,
    ErrorCode,
    IngestionError,
    backoff_seconds,
    classify_failure,
    is_retryable,
    safe_message,
)
from ingestion.states import (
    PIPELINE,
    PROCESSING_STATUSES,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    Stage,
    Status,
    can_transition,
    is_terminal,
    next_pipeline_step,
    resume_step,
    validate_transition,
)


class StateMachineTests(unittest.TestCase):
    def test_the_structured_pipeline_runs_in_order(self):
        status = Status.QUEUED
        visited = []
        while (step := next_pipeline_step(status)) is not None:
            status, stage = step
            visited.append((status, stage))

        self.assertEqual(visited, list(PIPELINE))
        self.assertEqual(visited[0][0], Status.VALIDATING)
        self.assertEqual(visited[-1][0], Status.VERIFYING)

    def test_each_pipeline_status_moves_to_the_next_one(self):
        for index, (status, _) in enumerate(PIPELINE[:-1]):
            with self.subTest(status=status):
                self.assertTrue(can_transition(status, PIPELINE[index + 1][0]))

    def test_a_finished_job_never_restarts_itself(self):
        # A failed job is terminal for the worker but may be re-queued by its
        # owner, which the next test pins down; ready and cancelled are final.
        for status in (Status.READY, Status.CANCELLED):
            with self.subTest(status=status):
                self.assertTrue(is_terminal(status))
                for target in Status:
                    self.assertFalse(
                        can_transition(status, target),
                        f"{status} must not move to {target}",
                    )

        self.assertEqual(
            TERMINAL_STATUSES,
            frozenset({Status.READY, Status.CANCELLED, Status.FAILED}),
        )

    def test_a_failed_job_can_only_be_rescheduled(self):
        # Terminal for the worker, but a person may retry a retryable failure.
        # The retry is a scheduled one so the recorded stage stays resumable;
        # plain ``queued`` may only enter the pipeline at validation.
        self.assertTrue(can_transition(Status.FAILED, Status.RETRY_SCHEDULED))
        for target in (
            Status.READY,
            Status.PARSING,
            Status.CANCELLED,
            Status.QUEUED,
        ):
            with self.subTest(target=target):
                self.assertFalse(can_transition(Status.FAILED, target))

    def test_illegal_transitions_raise(self):
        for current, target in (
            (Status.AWAITING_UPLOAD, Status.PARSING),
            (Status.QUEUED, Status.READY),
            (Status.PARSING, Status.CHUNKING),
            (Status.READY, Status.QUEUED),
            (Status.CANCELLED, Status.QUEUED),
        ):
            with self.subTest(current=current, target=target):
                with self.assertRaises(InvalidTransitionError):
                    validate_transition(current, target)

    def test_a_job_cannot_be_published_without_verification(self):
        for status, _ in PIPELINE:
            if status is Status.VERIFYING:
                continue
            with self.subTest(status=status):
                # Validation may short-circuit to ready for a duplicate upload;
                # nothing else reaches ready without the verification stage.
                expected = status is Status.VALIDATING
                self.assertEqual(can_transition(status, Status.READY), expected)

    def test_every_processing_status_can_be_interrupted(self):
        for status in PROCESSING_STATUSES:
            with self.subTest(status=status):
                self.assertTrue(can_transition(status, Status.FAILED))
                self.assertTrue(can_transition(status, Status.RETRY_SCHEDULED))
                self.assertTrue(can_transition(status, Status.CANCELLED))

    def test_a_retry_resumes_the_stage_that_failed(self):
        for status, stage in PIPELINE:
            with self.subTest(stage=stage):
                self.assertEqual(resume_step(stage), (status, stage))

    def test_stages_inside_validation_resume_in_validation(self):
        for stage in (Stage.DOWNLOAD_SOURCE, Stage.PREFLIGHT):
            with self.subTest(stage=stage):
                self.assertEqual(resume_step(stage), (Status.VALIDATING, stage))

    def test_a_job_with_no_recorded_stage_starts_at_the_beginning(self):
        self.assertEqual(resume_step(None), PIPELINE[0])


class RetryPolicyTests(unittest.TestCase):
    def test_permanent_failures_are_never_retried(self):
        for code in (
            ErrorCode.SOURCE_TOO_LARGE,
            ErrorCode.TOO_MANY_PAGES,
            ErrorCode.ENCRYPTED_PDF,
            ErrorCode.INVALID_PDF,
            ErrorCode.MISSING_TABLE_OF_CONTENTS,
            ErrorCode.UNSUPPORTED_DOCUMENT_CLASS,
            ErrorCode.INVALID_HIERARCHY,
            ErrorCode.CONTENT_LIMIT_EXCEEDED,
            ErrorCode.QUOTA_EXCEEDED,
            ErrorCode.CANCELLED,
        ):
            with self.subTest(code=code):
                self.assertFalse(is_retryable(code))
                decision = classify_failure(
                    IngestionError(code), attempt=1, max_attempts=3, job_id="job"
                )
                self.assertFalse(decision.retry)

    def test_transient_failures_are_retried_until_the_budget_runs_out(self):
        error = IngestionError(ErrorCode.PROVIDER_UNAVAILABLE)

        retried = classify_failure(error, attempt=2, max_attempts=3, job_id="job")
        exhausted = classify_failure(error, attempt=3, max_attempts=3, job_id="job")

        self.assertTrue(retried.retry)
        self.assertGreater(retried.delay_seconds, 0)
        self.assertFalse(exhausted.retry)

    def test_an_unclassified_exception_is_bounded_but_retryable(self):
        decision = classify_failure(
            RuntimeError("something surprising"),
            attempt=1,
            max_attempts=3,
            job_id="job",
        )

        self.assertEqual(decision.code, ErrorCode.UNEXPECTED_ERROR)
        self.assertTrue(decision.retry)

    def test_backoff_grows_and_stays_capped(self):
        delays = [
            backoff_seconds(attempt, job_id="job")
            for attempt in range(1, len(BACKOFF_SECONDS) + 3)
        ]

        for delay in delays:
            self.assertLessEqual(delay, MAXIMUM_BACKOFF_SECONDS)
        # Jitter is bounded, so each step still clears the previous base.
        self.assertLess(delays[0], delays[1])
        self.assertLess(delays[1], delays[2])

    def test_backoff_jitter_is_bounded_and_deterministic(self):
        for attempt, base in enumerate(BACKOFF_SECONDS, start=1):
            with self.subTest(attempt=attempt):
                delay = backoff_seconds(attempt, job_id="job-42")
                self.assertGreaterEqual(delay, base * 0.75)
                self.assertLessEqual(delay, base * 1.25)
                self.assertEqual(delay, backoff_seconds(attempt, job_id="job-42"))

        # Different jobs retrying the same attempt do not synchronise.
        self.assertNotEqual(
            backoff_seconds(1, job_id="job-a"), backoff_seconds(1, job_id="job-b")
        )

    def test_provider_retry_after_takes_precedence(self):
        delay = backoff_seconds(1, job_id="job", retry_after_seconds=45)
        self.assertEqual(delay, 45)

        capped = backoff_seconds(1, job_id="job", retry_after_seconds=99_999)
        self.assertEqual(capped, MAXIMUM_BACKOFF_SECONDS)

    def test_rate_limit_guidance_flows_through_classification(self):
        error = IngestionError(
            ErrorCode.PROVIDER_RATE_LIMITED, retry_after_seconds=12
        )
        decision = classify_failure(error, attempt=1, max_attempts=3, job_id="job")

        self.assertTrue(decision.retry)
        self.assertEqual(decision.delay_seconds, 12)

    def test_the_first_attempt_must_be_counted(self):
        with self.assertRaises(ValueError):
            backoff_seconds(0, job_id="job")


class SafeMessageTests(unittest.TestCase):
    def test_every_error_code_has_a_user_facing_message(self):
        for code in ErrorCode:
            with self.subTest(code=code):
                self.assertIn(code, SAFE_MESSAGES)
                self.assertTrue(SAFE_MESSAGES[code].strip())

    def test_internal_detail_never_reaches_the_safe_message(self):
        error = IngestionError(
            ErrorCode.INVALID_PDF,
            detail="psycopg.OperationalError at /tmp/job/original.pdf",
        )

        self.assertNotIn("/tmp", error.safe_message)
        self.assertNotIn("psycopg", error.safe_message)
        self.assertEqual(error.safe_message, safe_message(ErrorCode.INVALID_PDF))

    def test_an_unknown_code_degrades_to_the_generic_message(self):
        self.assertEqual(
            safe_message("something-we-never-defined"),
            SAFE_MESSAGES[ErrorCode.UNEXPECTED_ERROR],
        )


class LimitTests(unittest.TestCase):
    def test_defaults_stay_below_the_unproven_page_target(self):
        limits = IngestionLimits()

        self.assertEqual(limits.max_source_bytes, 52_428_800)
        self.assertLess(limits.max_pages, 1000)
        self.assertEqual(limits.allowed_content_types, ("application/pdf",))

    def test_storage_paths_are_owner_scoped_and_fixed(self):
        limits = IngestionLimits()

        self.assertEqual(limits.storage_path("owner", "job"), "owner/job/original.pdf")

    def test_limits_come_from_the_environment(self):
        with patch.dict(
            os.environ,
            {"INGESTION_MAX_PAGES": "50", "INGESTION_MAX_SOURCE_BYTES": "1024"},
        ):
            limits = load_limits()

        self.assertEqual(limits.max_pages, 50)
        self.assertEqual(limits.max_source_bytes, 1024)

    def test_an_unusable_limit_is_rejected_loudly(self):
        for value in ("0", "-1", "many"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"INGESTION_MAX_PAGES": value}):
                    with self.assertRaises(ValueError):
                        load_limits()


if __name__ == "__main__":
    unittest.main()
