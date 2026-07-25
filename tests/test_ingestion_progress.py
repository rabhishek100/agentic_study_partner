"""Progress and time estimates, including what they refuse to claim."""

import unittest
from datetime import datetime, timedelta, timezone

from ingestion.progress import STAGE_ORDER, estimate, stage_seconds
from ingestion.states import Stage, Status


NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


def running(stage, *, in_stage_seconds, elapsed_seconds=None, pages=269, **kw):
    elapsed = elapsed_seconds if elapsed_seconds is not None else in_stage_seconds
    return estimate(
        status=kw.pop("status", Status.PARSING),
        stage=stage,
        page_count=pages,
        started_at=NOW - timedelta(seconds=elapsed),
        stage_started_at=NOW - timedelta(seconds=in_stage_seconds),
        now=NOW,
        **kw,
    )


class EstimateTests(unittest.TestCase):
    def test_a_longer_book_is_expected_to_take_longer(self):
        short = running(Stage.PARSE_PAGES, in_stage_seconds=10, pages=50)
        long = running(Stage.PARSE_PAGES, in_stage_seconds=10, pages=800)

        self.assertLess(
            short.estimated_total_seconds, long.estimated_total_seconds
        )
        # Parsing dominates, so the gap should be large rather than marginal.
        self.assertGreater(long.estimated_total_seconds, short.estimated_total_seconds * 3)

    def test_progress_and_remaining_move_in_opposite_directions(self):
        early = running(Stage.PARSE_PAGES, in_stage_seconds=60)
        later = running(Stage.PARSE_PAGES, in_stage_seconds=900)

        self.assertGreater(later.percent, early.percent)
        self.assertLess(
            later.estimated_remaining_seconds, early.estimated_remaining_seconds
        )

    def test_a_time_based_stage_never_claims_to_be_finished(self):
        """The clock running out is not evidence that the parser is done."""

        overdue = running(Stage.PARSE_PAGES, in_stage_seconds=100_000)

        self.assertLess(overdue.percent, 100.0)
        self.assertTrue(overdue.overrunning)
        # No countdown is offered once the estimate is clearly wrong.
        self.assertIsNone(overdue.estimated_remaining_seconds)

    def test_a_real_count_is_preferred_over_the_clock(self):
        counted = running(
            Stage.BUILD_EMBEDDINGS,
            in_stage_seconds=1,
            status=Status.EMBEDDING,
            progress_completed=90,
            progress_total=100,
        )
        timed = running(
            Stage.BUILD_EMBEDDINGS, in_stage_seconds=1, status=Status.EMBEDDING
        )

        # One second in, the real count says nearly done and the clock does not.
        self.assertGreater(counted.percent, timed.percent)

    def test_later_stages_report_more_progress_than_earlier_ones(self):
        percents = [
            running(stage, in_stage_seconds=1, status=Status.PARSING).percent
            for stage in STAGE_ORDER
        ]

        self.assertEqual(percents, sorted(percents))
        self.assertLess(percents[0], 5.0)

    def test_a_ready_job_reports_complete_with_its_actual_duration(self):
        finished = estimate(
            status=Status.READY,
            stage=None,
            page_count=269,
            started_at=NOW - timedelta(minutes=31),
            stage_started_at=NOW - timedelta(minutes=1),
            completed_at=NOW,
            now=NOW,
        )

        self.assertEqual(finished.percent, 100.0)
        self.assertEqual(finished.estimated_remaining_seconds, 0.0)
        self.assertAlmostEqual(finished.elapsed_seconds, 31 * 60, delta=1)
        self.assertTrue(all(view.state == "done" for view in finished.stages))

    def test_a_failed_job_does_not_report_progress(self):
        failed = estimate(
            status=Status.FAILED,
            stage=Stage.PARSE_PAGES,
            page_count=269,
            started_at=NOW - timedelta(minutes=5),
            stage_started_at=NOW - timedelta(minutes=4),
            completed_at=NOW,
            now=NOW,
        )

        self.assertEqual(failed.percent, 0.0)
        self.assertTrue(all(view.state == "pending" for view in failed.stages))

    def test_a_queued_job_estimates_the_whole_pipeline(self):
        queued = estimate(
            status=Status.QUEUED,
            stage=None,
            page_count=269,
            started_at=None,
            stage_started_at=None,
            now=NOW,
        )

        self.assertEqual(queued.percent, 0.0)
        self.assertEqual(
            queued.estimated_remaining_seconds, queued.estimated_total_seconds
        )
        self.assertTrue(all(view.state == "pending" for view in queued.stages))

    def test_an_unknown_page_count_still_produces_an_estimate(self):
        # Before preflight there is no page count; the estimate must not be
        # zero or None, or the UI would show a broken bar.
        early = estimate(
            status=Status.VALIDATING,
            stage=Stage.DOWNLOAD_SOURCE,
            page_count=None,
            started_at=NOW - timedelta(seconds=5),
            stage_started_at=NOW - timedelta(seconds=2),
            now=NOW,
        )

        self.assertGreater(early.estimated_total_seconds, 0)
        self.assertGreaterEqual(early.percent, 0.0)

    def test_the_timeline_marks_exactly_one_active_stage(self):
        view = running(Stage.BUILD_CHUNKS, in_stage_seconds=5, status=Status.CHUNKING)

        states = [entry.state for entry in view.stages]
        self.assertEqual(states.count("active"), 1)
        active = next(e for e in view.stages if e.state == "active")
        self.assertEqual(active.stage, str(Stage.BUILD_CHUNKS))
        self.assertEqual(active.label, "Building search data")
        self.assertIsNotNone(active.elapsed_seconds)
        # Everything before it is done, everything after is pending.
        index = states.index("active")
        self.assertTrue(all(s == "done" for s in states[:index]))
        self.assertTrue(all(s == "pending" for s in states[index + 1 :]))

    def test_estimates_reflect_the_measured_production_run(self):
        """A 269-page book took about 31 minutes end to end in production."""

        total = sum(stage_seconds(stage, 269) for stage in STAGE_ORDER)

        self.assertGreater(total, 20 * 60)
        self.assertLess(total, 45 * 60)


if __name__ == "__main__":
    unittest.main()
