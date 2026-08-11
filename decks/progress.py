"""Honest progress and time estimates for deck generation jobs.

Deck generation reports a real completed/total count while cards are being
written.  The surrounding inventory and persistence steps are short and do
not have useful item counts, so they use conservative measured defaults.  The
UI labels every duration as an estimate and switches to the job's observed
per-card rate as soon as at least one card has completed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class StageView:
    stage: str
    label: str
    state: str  # done | active | pending
    expected_seconds: float
    elapsed_seconds: float | None = None


@dataclass(frozen=True)
class JobProgress:
    percent: float
    elapsed_seconds: float
    estimated_total_seconds: float
    estimated_remaining_seconds: float | None
    overrunning: bool
    stages: tuple[StageView, ...]


@dataclass(frozen=True)
class _StageDefinition:
    stage: str
    topic_label: str
    extracted_label: str
    weight: float

    def label(self, generation_mode: str) -> str:
        return (
            self.extracted_label
            if generation_mode == "book_extracted"
            else self.topic_label
        )


STAGES: tuple[_StageDefinition, ...] = (
    _StageDefinition("read_source", "Read source", "Read source", 4.0),
    _StageDefinition("inventory", "Plan topic coverage", "Find questions", 4.0),
    _StageDefinition(
        "generation",
        "Write and verify cards",
        "Write grounded answers",
        86.0,
    ),
    _StageDefinition("storing", "Save deck", "Save deck", 6.0),
)

INVENTORY_SECONDS = {
    "topic_generated": 12.0,
    "book_extracted": 18.0,
}
SECONDS_PER_ITEM = {
    "topic_generated": 18.0,
    "book_extracted": 28.0,
}
STORE_SECONDS = {
    "topic_generated": 8.0,
    "book_extracted": 10.0,
}
ASSUMED_ITEMS = 8
OVERRUN_FACTOR = 1.5


def _seconds_between(started_at: datetime | None, now: datetime) -> float:
    if started_at is None:
        return 0.0
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - started_at).total_seconds())


def _definitions(
    *,
    generation_mode: str,
    states: tuple[str, ...],
    item_count: int,
    active_elapsed: float | None,
) -> tuple[StageView, ...]:
    inventory = INVENTORY_SECONDS[generation_mode]
    per_item = SECONDS_PER_ITEM[generation_mode]
    storing = STORE_SECONDS[generation_mode]
    expected = (inventory * 0.45, inventory * 0.55, per_item * item_count, storing)
    return tuple(
        StageView(
            stage=definition.stage,
            label=definition.label(generation_mode),
            state=state,
            expected_seconds=round(seconds, 1),
            elapsed_seconds=(
                round(active_elapsed, 1) if state == "active" else None
            ),
        )
        for definition, state, seconds in zip(STAGES, states, expected, strict=True)
    )


def estimate(
    *,
    status: str,
    stage: str,
    generation_mode: str,
    topics_completed: int,
    topics_total: int,
    created_at: datetime | None,
    now: datetime | None = None,
) -> JobProgress:
    """Estimate a deck job using real item counts wherever they exist."""

    now = now or datetime.now(timezone.utc)
    mode = (
        generation_mode
        if generation_mode in SECONDS_PER_ITEM
        else "topic_generated"
    )
    completed = max(0, topics_completed)
    item_count = max(topics_total, completed, ASSUMED_ITEMS if not topics_total else 0)
    elapsed = _seconds_between(created_at, now)
    inventory = INVENTORY_SECONDS[mode]
    per_item = SECONDS_PER_ITEM[mode]
    storing = STORE_SECONDS[mode]
    expected_total = inventory + item_count * per_item + storing

    if status == "succeeded" or stage == "done":
        return JobProgress(
            percent=100.0,
            elapsed_seconds=round(elapsed, 1),
            estimated_total_seconds=round(elapsed or expected_total, 1),
            estimated_remaining_seconds=0.0,
            overrunning=False,
            stages=_definitions(
                generation_mode=mode,
                states=("done", "done", "done", "done"),
                item_count=item_count,
                active_elapsed=None,
            ),
        )

    if status == "queued" or stage == "pending":
        return JobProgress(
            percent=0.0,
            elapsed_seconds=round(elapsed, 1),
            estimated_total_seconds=round(expected_total, 1),
            estimated_remaining_seconds=round(expected_total, 1),
            overrunning=False,
            stages=_definitions(
                generation_mode=mode,
                states=("pending", "pending", "pending", "pending"),
                item_count=item_count,
                active_elapsed=None,
            ),
        )

    if stage == "inventory":
        fraction = min(0.95, elapsed / inventory) if inventory else 0.0
        percent = 8.0 * fraction
        remaining = max(0.0, inventory - elapsed) + item_count * per_item + storing
        states = ("active", "pending", "pending", "pending")
        active_elapsed = elapsed
    elif stage in {"generation", "repair"}:
        fraction = min(1.0, completed / topics_total) if topics_total else 0.0
        percent = 8.0 + 86.0 * fraction
        generation_elapsed = max(0.0, elapsed - inventory)
        if completed > 0:
            observed_rate = generation_elapsed / completed
            # One unusually fast/slow first response should not make the ETA
            # jump to an absurd value. The range widens enough to reflect a
            # genuinely slower provider while staying useful.
            rate = min(per_item * 4.0, max(per_item * 0.35, observed_rate))
        else:
            rate = per_item
        remaining_items = max(0, item_count - completed)
        remaining = remaining_items * rate + storing
        states = ("done", "done", "active", "pending")
        active_elapsed = generation_elapsed
        expected_total = elapsed + remaining
    elif stage == "storing":
        percent = 94.0 + 5.0 * min(1.0, elapsed / max(expected_total, 1.0))
        remaining = storing
        states = ("done", "done", "done", "active")
        active_elapsed = None
    else:
        percent = 0.0
        remaining = expected_total
        states = ("pending", "pending", "pending", "pending")
        active_elapsed = None

    terminal_failure = status in {"failed", "cancelled"}
    if terminal_failure:
        remaining_value: float | None = 0.0
        states = tuple("done" if state == "done" else "pending" for state in states)
    else:
        remaining_value = round(max(0.0, remaining), 1)

    return JobProgress(
        percent=round(min(99.0, max(0.0, percent)), 1),
        elapsed_seconds=round(elapsed, 1),
        estimated_total_seconds=round(max(elapsed, expected_total), 1),
        estimated_remaining_seconds=remaining_value,
        overrunning=(not terminal_failure and elapsed > expected_total * OVERRUN_FACTOR),
        stages=_definitions(
            generation_mode=mode,
            states=states,
            item_count=item_count,
            active_elapsed=active_elapsed,
        ),
    )


__all__ = ["JobProgress", "StageView", "estimate"]
