"""Reader-facing progress and ETA estimates for video ingestion.

The worker already persists the durable truth needed for recovery: the active
stage, per-stage checkpoints, progress counters, and timestamps.  This module
turns that technical state into an honest estimate without making the browser
invent its own pipeline.

The fallback rates are deliberately coarse.  They are calibrated for the
production worker and an 80-minute technical lecture, then scaled by media
duration.  When a stage reports real item progress, its observed rate replaces
the fallback for the rest of that stage.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from video.states import PIPELINE, TERMINAL, Stage, Status


# Fixed startup/provider overhead plus seconds per minute of source media.
# These values are estimates, not service guarantees; the API labels them as
# such and stops showing a countdown when a stage materially overruns it.
STAGE_RATES: dict[Stage, tuple[float, float]] = {
    Stage.ACQUIRE_SOURCE: (35.0, 0.12),
    Stage.MEDIA_METADATA: (8.0, 0.02),
    Stage.TRANSCRIPT: (20.0, 0.35),
    Stage.RESOURCES: (12.0, 0.08),
    Stage.FRAME_SELECTION: (18.0, 0.32),
    Stage.OCR: (20.0, 0.80),
    Stage.VISUAL_ANALYSIS: (30.0, 2.40),
    Stage.SPATIAL_REGIONS: (18.0, 0.42),
    Stage.INDEXING: (12.0, 0.14),
    Stage.EMBEDDINGS: (55.0, 3.00),
    Stage.QUALITY_GATES: (18.0, 0.05),
    Stage.PUBLISH: (8.0, 0.02),
}

STAGE_LABELS: dict[Stage, str] = {
    Stage.ACQUIRE_SOURCE: "Fetching the video",
    Stage.MEDIA_METADATA: "Reading media details",
    Stage.TRANSCRIPT: "Getting the transcript",
    Stage.RESOURCES: "Reading linked documents",
    Stage.FRAME_SELECTION: "Choosing useful frames",
    Stage.OCR: "Reading text on screen",
    Stage.VISUAL_ANALYSIS: "Understanding slides and diagrams",
    Stage.SPATIAL_REGIONS: "Extracting important visual regions",
    Stage.INDEXING: "Building keyword search",
    Stage.EMBEDDINGS: "Building semantic search",
    Stage.QUALITY_GATES: "Checking answer coverage",
    Stage.PUBLISH: "Publishing the lecture",
}

ASSUMED_DURATION_MINUTES = 60.0
MINIMUM_OBSERVED_SECONDS = 20.0
OVERRUN_FACTOR = 1.75


@dataclass(frozen=True)
class StageTiming:
    stage: str
    label: str
    state: str  # done | active | pending
    expected_seconds: float
    elapsed_seconds: float | None


@dataclass(frozen=True)
class VideoProgress:
    percent: float
    estimated_total_seconds: float
    estimated_remaining_seconds: float | None
    overrunning: bool
    stages: tuple[StageTiming, ...]
    waiting_for_worker: bool = False


def expected_stage_seconds(stage: Stage, duration_ms: int | None) -> float:
    minutes = (
        max(1.0, duration_ms / 60_000)
        if duration_ms is not None and duration_ms > 0
        else ASSUMED_DURATION_MINUTES
    )
    fixed, per_minute = STAGE_RATES[stage]
    return fixed + per_minute * minutes


def _elapsed(since: datetime | None, now: datetime) -> float:
    if since is None:
        return 0.0
    return max(0.0, (now - since).total_seconds())


def estimate(
    *,
    status: Status | str,
    stage: Stage | str | None,
    duration_ms: int | None,
    stage_started_at: datetime | None,
    progress_completed: int = 0,
    progress_total: int | None = None,
    now: datetime | None = None,
    worker_available: bool | None = None,
) -> VideoProgress:
    """Estimate pipeline completion from durable job state.

    Queued time is never treated as work completed.  A running stage can use
    its own observed item rate; queued and retry-scheduled stages retain the
    conservative production fallback until the worker claims them again.

    ``worker_available`` is how the caller says whether anything is actually
    going to do this work.  Given ``False``, the estimate keeps its percentage
    and its stage list but withholds the countdown, because remaining seconds
    are a promise and there is nobody to keep it: with no worker running, the
    old estimate ticked toward a completion that could not arrive.  ``None``
    means the caller has not asserted either way and the countdown stands,
    which is how the book and deck pipelines still call this.
    """

    current_status = Status(status)
    current_stage = Stage(stage) if stage else None
    now = now or datetime.now(timezone.utc)
    expected = {
        candidate: expected_stage_seconds(candidate, duration_ms)
        for candidate in PIPELINE
    }
    baseline_total = sum(expected.values())

    if current_status is Status.READY:
        return VideoProgress(
            percent=100.0,
            estimated_total_seconds=round(baseline_total, 1),
            estimated_remaining_seconds=0.0,
            overrunning=False,
            stages=tuple(
                StageTiming(
                    stage=str(candidate),
                    label=STAGE_LABELS[candidate],
                    state="done",
                    expected_seconds=round(expected[candidate], 1),
                    elapsed_seconds=None,
                )
                for candidate in PIPELINE
            ),
        )

    stalled = worker_available is False and current_status not in TERMINAL

    if current_stage is None or current_stage not in PIPELINE:
        return VideoProgress(
            percent=0.0,
            estimated_total_seconds=round(baseline_total, 1),
            estimated_remaining_seconds=(
                # A job that has stopped for good has no remaining time to
                # report — not zero, which reads as "any moment now".
                None
                if current_status in TERMINAL or stalled
                else round(baseline_total, 1)
            ),
            overrunning=False,
            waiting_for_worker=stalled,
            stages=tuple(
                StageTiming(
                    stage=str(candidate),
                    label=STAGE_LABELS[candidate],
                    state="pending",
                    expected_seconds=round(expected[candidate], 1),
                    elapsed_seconds=None,
                )
                for candidate in PIPELINE
            ),
        )

    index = PIPELINE.index(current_stage)
    done_seconds = sum(expected[candidate] for candidate in PIPELINE[:index])
    later_seconds = sum(expected[candidate] for candidate in PIPELINE[index + 1 :])
    running = current_status is Status.RUNNING
    in_stage = _elapsed(stage_started_at, now) if running else 0.0
    stage_expected = expected[current_stage]
    observed_projection = False

    if running and progress_total and progress_completed > 0:
        fraction = min(1.0, max(0.0, progress_completed / progress_total))
        if in_stage >= MINIMUM_OBSERVED_SECONDS and fraction > 0:
            stage_expected = max(in_stage, in_stage / fraction)
            expected[current_stage] = stage_expected
            observed_projection = True
    elif running:
        fraction = min(0.92, in_stage / max(stage_expected, 1.0))
    else:
        fraction = 0.0

    total = done_seconds + stage_expected + later_seconds
    completed_seconds = done_seconds + stage_expected * fraction
    remaining = max(0.0, total - completed_seconds)
    overrunning = (
        running
        and not observed_projection
        and in_stage > stage_expected * OVERRUN_FACTOR
    )

    stages = []
    for position, candidate in enumerate(PIPELINE):
        state = "done" if position < index else "active" if position == index else "pending"
        stages.append(
            StageTiming(
                stage=str(candidate),
                label=STAGE_LABELS[candidate],
                state=state,
                expected_seconds=round(expected[candidate], 1),
                elapsed_seconds=round(in_stage, 1) if state == "active" and running else None,
            )
        )

    return VideoProgress(
        percent=round(min(99.0, max(0.0, 100.0 * completed_seconds / total)), 1),
        estimated_total_seconds=round(total, 1),
        estimated_remaining_seconds=(
            None
            if overrunning or stalled or current_status in TERMINAL
            else round(remaining, 1)
        ),
        overrunning=overrunning,
        stages=tuple(stages),
        waiting_for_worker=stalled,
    )

