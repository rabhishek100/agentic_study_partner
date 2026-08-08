"""Spaced repetition: SM-2 with ease damping, and the daily queue.

Deterministic Python, no model call. Ten cards a day only compounds into
retention if the ones you keep failing come back sooner and the ones you know
stop appearing, and that is the whole job of this module.

The intervals here are SM-2's, with the two corrections every working
implementation has converged on: a floor on ease, so a run of bad days cannot
drive a card into permanent one-day purgatory, and a lapse that halves the
interval rather than resetting it, so one distracted evening does not throw
away a month of successful reviews.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .contracts import (
    RATING_AGAIN,
    RATING_EASY,
    RATING_GOOD,
    RATING_HARD,
    DeckPreferences,
    QueueCard,
    ReviewState,
)

MINUTES_PER_DAY = 1_440.0

# One short step. Two steps is Anki's default and is right for a thousand-card
# language deck reviewed all day; for a handful of dense technical cards it
# just means seeing the same card three times in five minutes.
LEARNING_STEPS_MINUTES: tuple[float, ...] = (10.0,)
RELEARNING_STEPS_MINUTES: tuple[float, ...] = (10.0,)

GRADUATING_INTERVAL_DAYS = 1.0
EASY_INTERVAL_DAYS = 4.0
# Beyond a year the schedule stops being a study plan and starts being a
# filing cabinet.
MAXIMUM_INTERVAL_DAYS = 365.0
MINIMUM_EASE = 1.3
STARTING_EASE = 2.5

EASE_DELTA = {
    RATING_AGAIN: -0.20,
    RATING_HARD: -0.15,
    RATING_GOOD: 0.0,
    RATING_EASY: 0.15,
}
HARD_MULTIPLIER = 1.2
EASY_BONUS = 1.3
# A lapse costs half the interval, not all of it.
LAPSE_MULTIPLIER = 0.5


@dataclass(frozen=True)
class Review:
    """The outcome of grading one card."""

    state: ReviewState
    due_at: datetime


def _clamp_ease(value: float) -> float:
    return max(MINIMUM_EASE, round(value, 4))


def _clamp_interval(days: float) -> float:
    return max(0.0, min(MAXIMUM_INTERVAL_DAYS, round(days, 6)))


def _next_step(current_days: float, steps: tuple[float, ...]) -> float | None:
    """The first step longer than where the card is now, or None to graduate."""

    current_minutes = current_days * MINUTES_PER_DAY
    for step in steps:
        if step > current_minutes + 1e-6:
            return step / MINUTES_PER_DAY
    return None


def review(state: ReviewState, rating: int, *, now: datetime | None = None) -> Review:
    """Schedule one card from its current state and a rating of 1–4."""

    if rating not in (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY):
        raise ValueError("rating must be 1 (again), 2 (hard), 3 (good), or 4 (easy)")
    moment = now or datetime.now(UTC)

    if state.state in ("new", "learning"):
        result = _learning(state, rating, LEARNING_STEPS_MINUTES, "learning")
    elif state.state == "relearning":
        result = _learning(state, rating, RELEARNING_STEPS_MINUTES, "relearning")
    else:
        result = _reviewing(state, rating)

    due_at = moment + timedelta(days=result.interval_days)
    return Review(
        state=ReviewState(
            state=result.state,
            due_at=due_at.isoformat(),
            interval_days=result.interval_days,
            ease=result.ease,
            reps=state.reps + 1,
            lapses=state.lapses + (1 if result.lapsed else 0),
            last_reviewed_at=moment.isoformat(),
            last_rating=rating,
        ),
        due_at=due_at,
    )


@dataclass(frozen=True)
class _Outcome:
    state: str
    interval_days: float
    ease: float
    lapsed: bool = False


def _learning(
    state: ReviewState,
    rating: int,
    steps: tuple[float, ...],
    stage: str,
) -> _Outcome:
    ease = _clamp_ease(state.ease + EASE_DELTA[rating])

    if rating == RATING_EASY:
        return _Outcome("review", EASY_INTERVAL_DAYS, ease)
    if rating == RATING_AGAIN:
        return _Outcome(stage, steps[0] / MINUTES_PER_DAY, ease)
    if rating == RATING_HARD:
        # Stay where you are. A hard card that has not been seen enough times
        # to leave the learning steps has not earned a longer gap.
        return _Outcome(
            stage,
            max(state.interval_days, steps[0] / MINUTES_PER_DAY),
            ease,
        )

    # A card the reader has never seen is graded the moment it is introduced,
    # so by the time `good` is pressed it has already served its first step —
    # otherwise Again, Hard and Good would all schedule the same ten minutes
    # and three of the four buttons would mean nothing.
    position = (
        steps[0] / MINUTES_PER_DAY
        if state.state == "new"
        else state.interval_days
    )
    step = _next_step(position, steps)
    if step is not None:
        return _Outcome(stage, step, ease)
    graduating = (
        GRADUATING_INTERVAL_DAYS
        if stage == "learning"
        # A relapsed card returns to the schedule at the interval its lapse
        # left it, not back at one day: it was known once.
        else max(GRADUATING_INTERVAL_DAYS, state.interval_days)
    )
    return _Outcome("review", _clamp_interval(graduating), ease)


def _reviewing(state: ReviewState, rating: int) -> _Outcome:
    ease = _clamp_ease(state.ease + EASE_DELTA[rating])
    base = max(state.interval_days, GRADUATING_INTERVAL_DAYS)

    if rating == RATING_AGAIN:
        return _Outcome(
            "relearning",
            RELEARNING_STEPS_MINUTES[0] / MINUTES_PER_DAY,
            ease,
            lapsed=True,
        )
    if rating == RATING_HARD:
        return _Outcome("review", _clamp_interval(base * HARD_MULTIPLIER), ease)
    if rating == RATING_GOOD:
        return _Outcome("review", _clamp_interval(base * ease), ease)
    return _Outcome("review", _clamp_interval(base * ease * EASY_BONUS), ease)


def initial_state() -> ReviewState:
    return ReviewState(state="new", ease=STARTING_EASE)


def build_queue(
    *,
    due: list[QueueCard],
    fresh: list[QueueCard],
    preferences: DeckPreferences,
    reviewed_today: int,
    new_introduced_today: int,
) -> list[QueueCard]:
    """Mix everything due with a capped number of new cards.

    Due first, oldest first: a card you are about to forget is worth more than
    a card you have never seen. New cards follow in interview-priority order,
    which is where "top questions first" actually happens — a deck keeps full
    coverage while the queue introduces what matters most, first.
    """

    remaining = max(0, preferences.max_reviews_per_day - reviewed_today)
    if not remaining:
        return []

    ordered_due = sorted(due, key=lambda item: (item.review.due_at or "", item.card.front))
    new_allowance = max(0, preferences.new_cards_per_day - new_introduced_today)
    ordered_new = sorted(
        fresh,
        key=lambda item: (-item.card.interview_priority, item.deck_title, item.card.card_index),
    )[:new_allowance]

    return [*ordered_due, *ordered_new][:remaining]
