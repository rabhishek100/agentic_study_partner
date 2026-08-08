"""Spaced repetition and the daily queue — pure functions, no database."""

import unittest
from datetime import UTC, datetime, timedelta

from decks.contracts import (
    CardBack,
    DeckCard,
    DeckCitation,
    DeckPreferences,
    QueueCard,
    ReviewState,
)
from decks.scheduler import (
    EASY_INTERVAL_DAYS,
    GRADUATING_INTERVAL_DAYS,
    MAXIMUM_INTERVAL_DAYS,
    MINIMUM_EASE,
    build_queue,
    initial_state,
    review,
)

NOW = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


def reviewing(**overrides) -> ReviewState:
    base = {"state": "review", "interval_days": 10.0, "ease": 2.5, "reps": 4}
    return ReviewState(**{**base, **overrides})


def queue_card(
    *,
    front: str,
    priority: int = 3,
    due_at: str | None = None,
    deck_title: str = "Chapter 1",
    card_index: int = 0,
) -> QueueCard:
    return QueueCard(
        card=DeckCard(
            topic_key="node:1",
            card_index=card_index,
            card_type="qa",
            front=front,
            back=CardBack(answer="because"),
            citations=[DeckCitation(marker="[N1:P1]", node_id=1, page=1)],
            interview_priority=priority,
        ),
        deck_id="deck-1",
        deck_title=deck_title,
        source_kind="book",
        source_title="Designing ML Systems",
        review=ReviewState(state="review" if due_at else "new", due_at=due_at),
    )


class LearningTests(unittest.TestCase):
    def test_a_new_card_rated_good_enters_the_learning_step(self) -> None:
        result = review(initial_state(), 3, now=NOW)
        self.assertEqual(result.state.state, "learning")
        self.assertAlmostEqual(result.state.interval_days * 1440, 10.0, places=3)
        self.assertEqual(result.due_at, NOW + timedelta(minutes=10))

    def test_a_second_good_graduates_to_the_review_schedule(self) -> None:
        first = review(initial_state(), 3, now=NOW).state
        second = review(first, 3, now=NOW)
        self.assertEqual(second.state.state, "review")
        self.assertEqual(second.state.interval_days, GRADUATING_INTERVAL_DAYS)

    def test_easy_skips_the_learning_steps(self) -> None:
        result = review(initial_state(), 4, now=NOW)
        self.assertEqual(result.state.state, "review")
        self.assertEqual(result.state.interval_days, EASY_INTERVAL_DAYS)

    def test_again_holds_the_card_in_learning(self) -> None:
        first = review(initial_state(), 3, now=NOW).state
        result = review(first, 1, now=NOW)
        self.assertEqual(result.state.state, "learning")
        self.assertAlmostEqual(result.state.interval_days * 1440, 10.0, places=3)
        # Failing a card still in learning is not a lapse: it never left.
        self.assertEqual(result.state.lapses, 0)


class ReviewingTests(unittest.TestCase):
    def test_good_multiplies_the_interval_by_ease(self) -> None:
        result = review(reviewing(), 3, now=NOW)
        self.assertEqual(result.state.interval_days, 25.0)
        self.assertEqual(result.state.ease, 2.5)

    def test_hard_grows_slowly_and_costs_ease(self) -> None:
        result = review(reviewing(), 2, now=NOW)
        self.assertEqual(result.state.interval_days, 12.0)
        self.assertEqual(result.state.ease, 2.35)

    def test_easy_grows_fastest_and_earns_ease(self) -> None:
        result = review(reviewing(), 4, now=NOW)
        self.assertEqual(result.state.ease, 2.65)
        self.assertGreater(result.state.interval_days, 25.0)

    def test_again_lapses_into_relearning_and_halves_nothing_yet(self) -> None:
        result = review(reviewing(), 1, now=NOW)
        self.assertEqual(result.state.state, "relearning")
        self.assertEqual(result.state.lapses, 1)
        self.assertAlmostEqual(result.state.interval_days * 1440, 10.0, places=3)

    def test_ease_never_falls_below_the_floor(self) -> None:
        state = reviewing(ease=MINIMUM_EASE)
        result = review(state, 1, now=NOW)
        self.assertEqual(result.state.ease, MINIMUM_EASE)

    def test_intervals_are_capped_at_a_year(self) -> None:
        result = review(reviewing(interval_days=300.0, ease=2.5), 4, now=NOW)
        self.assertEqual(result.state.interval_days, MAXIMUM_INTERVAL_DAYS)

    def test_a_relearned_card_returns_to_review(self) -> None:
        lapsed = review(reviewing(), 1, now=NOW).state
        recovered = review(lapsed, 3, now=NOW)
        self.assertEqual(recovered.state.state, "review")
        self.assertGreaterEqual(recovered.state.interval_days, GRADUATING_INTERVAL_DAYS)

    def test_a_rating_outside_one_to_four_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            review(initial_state(), 5, now=NOW)


class QueueTests(unittest.TestCase):
    def test_due_cards_come_before_new_ones(self) -> None:
        due = [queue_card(front="due", due_at="2026-08-08T08:00:00+00:00")]
        fresh = [queue_card(front="new", priority=5)]
        queue = build_queue(
            due=due,
            fresh=fresh,
            preferences=DeckPreferences(),
            reviewed_today=0,
            new_introduced_today=0,
        )
        self.assertEqual([item.card.front for item in queue], ["due", "new"])

    def test_new_cards_are_introduced_highest_priority_first(self) -> None:
        fresh = [
            queue_card(front="peripheral", priority=1, card_index=1),
            queue_card(front="central", priority=5, card_index=2),
        ]
        queue = build_queue(
            due=[],
            fresh=fresh,
            preferences=DeckPreferences(new_cards_per_day=2),
            reviewed_today=0,
            new_introduced_today=0,
        )
        self.assertEqual([item.card.front for item in queue], ["central", "peripheral"])

    def test_the_daily_new_cap_is_respected(self) -> None:
        fresh = [queue_card(front=f"card {index}", card_index=index) for index in range(5)]
        queue = build_queue(
            due=[],
            fresh=fresh,
            preferences=DeckPreferences(new_cards_per_day=10),
            reviewed_today=0,
            new_introduced_today=8,
        )
        self.assertEqual(len(queue), 2)

    def test_the_review_ceiling_truncates_the_whole_queue(self) -> None:
        due = [
            queue_card(front=f"due {index}", due_at=f"2026-08-08T0{index}:00:00+00:00")
            for index in range(4)
        ]
        queue = build_queue(
            due=due,
            fresh=[queue_card(front="new")],
            preferences=DeckPreferences(max_reviews_per_day=3),
            reviewed_today=0,
            new_introduced_today=0,
        )
        self.assertEqual(len(queue), 3)
        self.assertTrue(all(item.card.front.startswith("due") for item in queue))

    def test_nothing_is_served_once_the_ceiling_is_reached(self) -> None:
        queue = build_queue(
            due=[queue_card(front="due", due_at="2026-08-08T08:00:00+00:00")],
            fresh=[],
            preferences=DeckPreferences(max_reviews_per_day=5),
            reviewed_today=5,
            new_introduced_today=0,
        )
        self.assertEqual(queue, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
