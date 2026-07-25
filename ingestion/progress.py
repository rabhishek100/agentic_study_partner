"""Progress and time estimates for a running ingestion job.

The layout parser reports nothing between "started" and "finished", so a
parse of a long book would otherwise show an unmoving bar for twenty-five
minutes. These estimates fill that gap honestly: they are derived from a
measured production run, they are labelled as estimates everywhere they
surface, and any stage that can report real progress does so instead.

Rates come from the first production ingestion: a 269-page book on the
deployed worker took 24m51s to parse, 33s to chunk, 10s to embed, and 5s to
verify. Persistence was 5m18s before inserts were batched and is now a small
fraction of that. Re-measure and update these when the parser, the hardware,
or the batch size changes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from .states import Stage, Status, is_terminal


# Seconds per page, measured on the deployed worker.
SECONDS_PER_PAGE: dict[Stage, float] = {
    Stage.PARSE_PAGES: 5.6,
    Stage.PERSIST_CANONICAL: 0.08,
    Stage.BUILD_CHUNKS: 0.13,
    Stage.BUILD_EMBEDDINGS: 0.05,
    Stage.VERIFY_BOOK: 0.02,
}

# Fixed cost per stage regardless of length: model loading, connection setup,
# provider latency that does not scale with the document.
FIXED_SECONDS: dict[Stage, float] = {
    Stage.VERIFY_UPLOAD: 2.0,
    Stage.DOWNLOAD_SOURCE: 6.0,
    Stage.PREFLIGHT: 3.0,
    Stage.PARSE_PAGES: 25.0,
    Stage.PERSIST_CANONICAL: 6.0,
    Stage.BUILD_CHUNKS: 4.0,
    Stage.BUILD_EMBEDDINGS: 6.0,
    Stage.VERIFY_BOOK: 3.0,
}

# Used before preflight has counted the pages.
ASSUMED_PAGES = 150

STAGE_ORDER: tuple[Stage, ...] = (
    Stage.VERIFY_UPLOAD,
    Stage.DOWNLOAD_SOURCE,
    Stage.PREFLIGHT,
    Stage.PARSE_PAGES,
    Stage.PERSIST_CANONICAL,
    Stage.BUILD_CHUNKS,
    Stage.BUILD_EMBEDDINGS,
    Stage.VERIFY_BOOK,
)

STAGE_LABELS: dict[Stage, str] = {
    Stage.VERIFY_UPLOAD: "Checking the upload",
    Stage.DOWNLOAD_SOURCE: "Fetching the file",
    Stage.PREFLIGHT: "Inspecting the PDF",
    Stage.PARSE_PAGES: "Reading pages",
    Stage.PERSIST_CANONICAL: "Saving the book",
    Stage.BUILD_CHUNKS: "Building search data",
    Stage.BUILD_EMBEDDINGS: "Building semantic search",
    Stage.VERIFY_BOOK: "Verifying",
}

# A stage taking this much longer than expected is reported as overrunning
# rather than being shown a countdown that has already reached zero.
OVERRUN_FACTOR = 1.5


@dataclass(frozen=True)
class StageView:
    """One stage's place in the pipeline, for a timeline display."""

    stage: str
    label: str
    state: str  # done | active | pending
    expected_seconds: float
    elapsed_seconds: float | None


@dataclass(frozen=True)
class JobProgress:
    """What the UI needs to show honest progress and a time estimate."""

    percent: float
    elapsed_seconds: float
    estimated_total_seconds: float
    estimated_remaining_seconds: float | None
    overrunning: bool
    stages: tuple[StageView, ...]


def stage_seconds(stage: Stage, pages: int) -> float:
    """Expected duration of one stage for a document of ``pages`` pages."""

    return FIXED_SECONDS.get(stage, 0.0) + SECONDS_PER_PAGE.get(stage, 0.0) * pages


def _elapsed(since: datetime | None, now: datetime) -> float | None:
    if since is None:
        return None
    return max(0.0, (now - since).total_seconds())


def estimate(
    *,
    status: Status | str,
    stage: Stage | str | None,
    page_count: int | None,
    started_at: datetime | None,
    stage_started_at: datetime | None,
    completed_at: datetime | None = None,
    progress_completed: int = 0,
    progress_total: int | None = None,
    now: datetime | None = None,
) -> JobProgress:
    """Estimate overall progress and remaining time for one job.

    Stages that report real counts use them. The parse stage, which cannot,
    is estimated from elapsed time against its expected duration and is
    capped below completion so it never claims to be finished early.
    """

    now = now or datetime.now(timezone.utc)
    current_status = Status(status)
    current_stage = Stage(stage) if stage else None
    pages = page_count or ASSUMED_PAGES

    expected = {step: stage_seconds(step, pages) for step in STAGE_ORDER}
    total = sum(expected.values())
    elapsed = _elapsed(started_at, now) or 0.0

    if is_terminal(current_status):
        finished = current_status is Status.READY
        actual = _elapsed(started_at, completed_at) if completed_at else elapsed
        return JobProgress(
            percent=100.0 if finished else 0.0,
            elapsed_seconds=actual or elapsed,
            estimated_total_seconds=actual or total,
            estimated_remaining_seconds=0.0,
            overrunning=False,
            stages=tuple(
                StageView(
                    stage=str(step),
                    label=STAGE_LABELS[step],
                    state="done" if finished else "pending",
                    expected_seconds=round(expected[step], 1),
                    elapsed_seconds=None,
                )
                for step in STAGE_ORDER
            ),
        )

    if current_stage is None or current_stage not in STAGE_ORDER:
        # Queued, or a stage outside the structured pipeline.
        return JobProgress(
            percent=0.0,
            elapsed_seconds=elapsed,
            estimated_total_seconds=total,
            estimated_remaining_seconds=total,
            overrunning=False,
            stages=tuple(
                StageView(
                    stage=str(step),
                    label=STAGE_LABELS[step],
                    state="pending",
                    expected_seconds=round(expected[step], 1),
                    elapsed_seconds=None,
                )
                for step in STAGE_ORDER
            ),
        )

    index = STAGE_ORDER.index(current_stage)
    done_seconds = sum(expected[step] for step in STAGE_ORDER[:index])
    in_stage = _elapsed(stage_started_at, now) or 0.0
    stage_expected = expected[current_stage] or 1.0

    # Prefer a real count, but only once it is actually moving. The parse
    # stage publishes its page total up front and cannot update the count
    # while the parser runs, so trusting the total alone pins a long parse at
    # zero for its entire duration.
    if progress_total and progress_completed > 0:
        fraction = min(1.0, max(0.0, progress_completed / progress_total))
    else:
        # Time-based, capped just under complete: an estimate must not claim
        # a stage finished when only the clock says so.
        fraction = min(0.97, in_stage / stage_expected)

    percent = 100.0 * (done_seconds + stage_expected * fraction) / total
    remaining = max(0.0, total - (done_seconds + stage_expected * fraction))
    overrunning = in_stage > stage_expected * OVERRUN_FACTOR

    stages = []
    for position, step in enumerate(STAGE_ORDER):
        if position < index:
            state = "done"
        elif position == index:
            state = "active"
        else:
            state = "pending"
        stages.append(
            StageView(
                stage=str(step),
                label=STAGE_LABELS[step],
                state=state,
                expected_seconds=round(expected[step], 1),
                elapsed_seconds=round(in_stage, 1) if state == "active" else None,
            )
        )

    return JobProgress(
        percent=round(min(99.0, max(0.0, percent)), 1),
        elapsed_seconds=round(elapsed, 1),
        estimated_total_seconds=round(total, 1),
        estimated_remaining_seconds=None if overrunning else round(remaining, 1),
        overrunning=overrunning,
        stages=tuple(stages),
    )


def pipeline_stage_labels() -> dict[str, str]:
    """Stage labels for clients that render their own timeline."""

    return {str(step): STAGE_LABELS[step] for step in STAGE_ORDER}


__all__ = [
    "JobProgress",
    "StageView",
    "estimate",
    "pipeline_stage_labels",
    "stage_seconds",
]
