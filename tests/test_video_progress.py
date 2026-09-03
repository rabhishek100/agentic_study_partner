"""Progress estimates stay honest as video jobs queue, run, and resume."""

from datetime import datetime, timedelta, timezone

from video.progress import estimate


def test_queued_job_reports_remaining_pipeline_without_fake_elapsed_work() -> None:
    progress = estimate(
        status="queued",
        stage="embeddings",
        duration_ms=80 * 60_000,
        stage_started_at=None,
    )

    assert 50 < progress.percent < 95
    assert progress.estimated_remaining_seconds is not None
    assert progress.estimated_remaining_seconds > 300
    assert next(stage for stage in progress.stages if stage.stage == "embeddings").state == "active"


def test_running_stage_uses_observed_item_rate_for_its_eta() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    progress = estimate(
        status="running",
        stage="embeddings",
        duration_ms=80 * 60_000,
        stage_started_at=now - timedelta(seconds=200),
        progress_completed=500,
        progress_total=1000,
        now=now,
    )

    assert progress.estimated_remaining_seconds is not None
    assert 200 < progress.estimated_remaining_seconds < 260
    assert not progress.overrunning


def test_overrunning_stage_stops_promising_a_countdown() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    progress = estimate(
        status="running",
        stage="visual_analysis",
        duration_ms=80 * 60_000,
        stage_started_at=now - timedelta(minutes=8),
        now=now,
    )

    assert progress.overrunning
    assert progress.estimated_remaining_seconds is None


def test_ready_job_is_complete() -> None:
    progress = estimate(
        status="ready",
        stage=None,
        duration_ms=30 * 60_000,
        stage_started_at=None,
    )

    assert progress.percent == 100
    assert progress.estimated_remaining_seconds == 0
    assert all(stage.state == "done" for stage in progress.stages)



def test_no_worker_means_no_countdown() -> None:
    """A queued lecture with nothing to run it must not tick toward an ETA.

    The percentage and the stage list still describe where the work stopped;
    what goes away is the promise about when it resumes.
    """

    progress = estimate(
        status="queued",
        stage="embeddings",
        duration_ms=80 * 60_000,
        stage_started_at=None,
        worker_available=False,
    )

    assert progress.waiting_for_worker
    assert progress.estimated_remaining_seconds is None
    assert 50 < progress.percent < 95
    assert next(s for s in progress.stages if s.stage == "embeddings").state == "active"


def test_a_running_stage_whose_worker_has_gone_stops_promising_a_finish() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)

    progress = estimate(
        status="running",
        stage="ocr",
        duration_ms=80 * 60_000,
        stage_started_at=now - timedelta(seconds=30),
        now=now,
        worker_available=False,
    )

    assert progress.waiting_for_worker
    assert progress.estimated_remaining_seconds is None


def test_a_finished_job_is_not_described_as_waiting_for_a_worker() -> None:
    for status in ("failed", "cancelled"):
        progress = estimate(
            status=status,
            stage="publish",
            duration_ms=80 * 60_000,
            stage_started_at=None,
            worker_available=False,
        )
        assert not progress.waiting_for_worker, status
        # Not zero: zero reads as "any moment now" for work that has stopped.
        assert progress.estimated_remaining_seconds is None, status


def test_an_unasserted_worker_keeps_the_existing_countdown() -> None:
    """Books and decks call this without a liveness signal; they are unchanged."""

    progress = estimate(
        status="queued",
        stage="embeddings",
        duration_ms=80 * 60_000,
        stage_started_at=None,
    )

    assert not progress.waiting_for_worker
    assert progress.estimated_remaining_seconds is not None
