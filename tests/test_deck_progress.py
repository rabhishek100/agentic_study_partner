"""Progress semantics shared by AI-generated and book-extracted decks."""

from datetime import datetime, timedelta, timezone
import unittest

from decks.progress import estimate


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


class DeckProgressTests(unittest.TestCase):
    def test_real_question_counts_drive_the_overall_percentage_and_eta(self) -> None:
        progress = estimate(
            status="running",
            stage="generation",
            generation_mode="book_extracted",
            topics_completed=7,
            topics_total=10,
            created_at=NOW - timedelta(seconds=134),
            now=NOW,
        )

        self.assertAlmostEqual(progress.percent, 68.2)
        self.assertAlmostEqual(progress.elapsed_seconds, 134)
        self.assertGreater(progress.estimated_remaining_seconds or 0, 50)
        self.assertLess(progress.estimated_remaining_seconds or 999, 80)
        self.assertEqual(
            [stage.label for stage in progress.stages],
            ["Read source", "Find questions", "Write grounded answers", "Save deck"],
        )
        self.assertEqual(
            [stage.state for stage in progress.stages],
            ["done", "done", "active", "pending"],
        )

    def test_ai_generation_uses_mode_specific_plain_language(self) -> None:
        progress = estimate(
            status="running",
            stage="generation",
            generation_mode="topic_generated",
            topics_completed=2,
            topics_total=8,
            created_at=NOW - timedelta(seconds=60),
            now=NOW,
        )
        self.assertEqual(progress.stages[1].label, "Plan topic coverage")
        self.assertEqual(progress.stages[2].label, "Write and verify cards")

    def test_queued_and_finished_jobs_never_claim_false_partial_completion(self) -> None:
        queued = estimate(
            status="queued",
            stage="pending",
            generation_mode="topic_generated",
            topics_completed=0,
            topics_total=0,
            created_at=NOW - timedelta(seconds=5),
            now=NOW,
        )
        finished = estimate(
            status="succeeded",
            stage="done",
            generation_mode="topic_generated",
            topics_completed=8,
            topics_total=8,
            created_at=NOW - timedelta(seconds=120),
            now=NOW,
        )
        self.assertEqual(queued.percent, 0)
        self.assertTrue(all(stage.state == "pending" for stage in queued.stages))
        self.assertEqual(finished.percent, 100)
        self.assertEqual(finished.estimated_remaining_seconds, 0)
        self.assertTrue(all(stage.state == "done" for stage in finished.stages))

    def test_failed_job_stops_the_countdown_and_has_no_active_stage(self) -> None:
        failed = estimate(
            status="failed",
            stage="generation",
            generation_mode="book_extracted",
            topics_completed=3,
            topics_total=10,
            created_at=NOW - timedelta(seconds=90),
            now=NOW,
        )
        self.assertEqual(failed.estimated_remaining_seconds, 0)
        self.assertFalse(any(stage.state == "active" for stage in failed.stages))


if __name__ == "__main__":
    unittest.main()
