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

    def test_a_published_total_with_no_movement_does_not_pin_progress_at_zero(self):
        """Regression: a 389-page parse sat at 1% for its whole duration.

        The parse stage publishes its page total before starting and cannot
        update the count while the parser runs, so a total alone must not be
        taken as evidence that zero pages are done.
        """

        stuck = running(
            Stage.PARSE_PAGES,
            in_stage_seconds=26 * 60,
            pages=389,
            progress_completed=0,
            progress_total=389,
        )

        self.assertGreater(stuck.percent, 40.0)
        self.assertLess(stuck.percent, 99.0)
        # The countdown must shrink as the parse proceeds, not stay near total.
        self.assertLess(
            stuck.estimated_remaining_seconds, stuck.estimated_total_seconds / 2
        )

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


class ObservedRateTests(unittest.TestCase):
    """Once a stage reports its own rate, that rate beats the constant.

    Parse cost per page varies roughly twentyfold between books on identical
    hardware, which is how a job came to promise 49 minutes and take 92.
    """

    def _parsing(self, *, in_stage_seconds, completed, total=535, pages=535):
        return running(
            Stage.PARSE_PAGES,
            in_stage_seconds=in_stage_seconds,
            pages=pages,
            progress_completed=completed,
            progress_total=total,
        )

    def test_a_slow_book_grows_its_own_estimate(self):
        # A quarter done after 30 minutes projects to about two hours, not to
        # the constant's fifty.
        slow = self._parsing(in_stage_seconds=30 * 60, completed=134)

        self.assertGreater(slow.estimated_total_seconds, 100 * 60)
        self.assertLess(slow.estimated_total_seconds, 140 * 60)

    def test_a_fast_book_shrinks_its_own_estimate(self):
        fast = self._parsing(in_stage_seconds=60, completed=300)

        # Well under the constant's projection for a 535-page book.
        self.assertLess(fast.estimated_total_seconds, 20 * 60)

    def test_a_projected_stage_is_not_reported_as_overrunning(self):
        """Re-estimating is the alternative to declaring the job late."""

        slow = self._parsing(in_stage_seconds=90 * 60, completed=400)

        self.assertFalse(slow.overrunning)
        self.assertIsNotNone(slow.estimated_remaining_seconds)

    def test_the_remaining_time_matches_the_observed_rate(self):
        # Half the pages in 40 minutes means roughly 40 minutes of parsing
        # left, plus the stages that follow.
        half = self._parsing(in_stage_seconds=40 * 60, completed=268)

        self.assertGreater(half.estimated_remaining_seconds, 35 * 60)
        self.assertLess(half.estimated_remaining_seconds, 55 * 60)

    def test_an_early_report_is_not_trusted_yet(self):
        """One batch in, the rate is mostly model loading."""

        instant = self._parsing(in_stage_seconds=2, completed=25)
        constant = stage_seconds(Stage.PARSE_PAGES, 535)

        self.assertAlmostEqual(
            instant.estimated_total_seconds,
            sum(stage_seconds(stage, 535) for stage in STAGE_ORDER),
            delta=1.0,
        )
        self.assertGreater(constant, 0)

    def test_the_projection_never_runs_backwards(self):
        """A stage cannot be projected to finish before it already has run."""

        nearly = self._parsing(in_stage_seconds=60 * 60, completed=535)

        self.assertGreaterEqual(nearly.estimated_total_seconds, 60 * 60)
        self.assertGreaterEqual(nearly.estimated_remaining_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
