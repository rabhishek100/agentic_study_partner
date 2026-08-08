"""Owner-scoped persistence for decks, cards, and review state.

Two rules shape everything here. A deck is written in one transaction and only
then marked readable, so a failed run never leaves half a deck in the library.
And regenerating a scope inserts a new version rather than mutating cards,
because the scheduling rows point at card ids and a reader's review history has
to survive a regeneration it did not ask for.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id

from .contracts import (
    CardBack,
    DeckCard,
    DeckCitation,
    DeckFigure,
    DeckMetrics,
    DeckPreferences,
    DeckSummary,
    QueueCard,
    ReviewState,
)
from .generate import GeneratedDeck
from .scheduler import initial_state, review as schedule_review
from .topics import Topic

# The library and the queue both read cards; neither ever wants a whole book's
# worth in one response.
DEFAULT_QUEUE_LIMIT = 200


class DeckNotFoundError(LookupError):
    """No deck with that id belongs to this owner."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def create_deck(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    scope_key: str,
    title: str,
    source_title: str,
    book_id: int | None = None,
    node_id: int | None = None,
    video_id: str | UUID | None = None,
) -> tuple[UUID, int]:
    """Reserve the next version of a scope, in `generating` state.

    The version is read and written inside one statement so two concurrent
    generations of the same chapter cannot both claim version 2.
    """

    owner = parse_owner_id(owner_id)
    row = connection.execute(
        """
        insert into public.decks (
            owner_id, source_kind, book_id, node_id, video_id,
            scope_key, version, title, source_title, status
        )
        values (
            %s, %s, %s, %s, %s, %s,
            (
                select coalesce(max(version), 0) + 1
                from public.decks
                where owner_id = %s and scope_key = %s
            ),
            %s, %s, 'generating'
        )
        returning id, version
        """,
        (
            owner,
            source_kind,
            book_id,
            node_id,
            UUID(str(video_id)) if video_id else None,
            scope_key,
            owner,
            scope_key,
            title,
            source_title,
        ),
    ).fetchone()
    return row["id"], row["version"]


def store_deck(
    connection: Connection,
    *,
    owner_id: str | UUID,
    deck_id: UUID,
    topics: tuple[Topic, ...],
    generated: GeneratedDeck,
    ingestion_version_id: str | UUID | None = None,
) -> None:
    """Write topics, cards, and their scheduling rows, then publish the deck.

    Everything lands in one transaction. A deck that is visible is a deck that
    is complete; a deck that failed midway is still `generating` and gets swept
    by the job's own failure path.
    """

    owner = parse_owner_id(owner_id)
    metrics = generated.metrics
    covered = {card.topic_key for card in generated.cards}

    with connection.transaction():
        connection.execute(
            "delete from public.deck_topics where deck_id = %s and owner_id = %s",
            (deck_id, owner),
        )
        for topic in topics:
            connection.execute(
                """
                insert into public.deck_topics (
                    owner_id, deck_id, topic_key, ordinal, label, required,
                    node_id, start_page, end_page, start_ms, end_ms,
                    evidence_ranks, covered
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    owner,
                    deck_id,
                    topic.key,
                    topic.ordinal,
                    topic.label,
                    topic.required,
                    topic.node_id,
                    topic.start_page,
                    topic.end_page,
                    topic.start_ms,
                    topic.end_ms,
                    list(topic.evidence_ranks),
                    topic.key in covered,
                ),
            )

        for card in generated.cards:
            row = connection.execute(
                """
                insert into public.deck_cards (
                    owner_id, deck_id, topic_key, card_index, card_type,
                    front, back_json, interview_priority, priority_reason,
                    difficulty, citations_json, figures_json, interview_angle
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    owner,
                    deck_id,
                    card.topic_key,
                    card.card_index,
                    card.card_type,
                    card.front,
                    _json(card.back.model_dump(mode="json")),
                    card.interview_priority,
                    card.priority_reason,
                    card.difficulty,
                    _json([item.model_dump(mode="json") for item in card.citations]),
                    _json([item.model_dump(mode="json") for item in card.figures]),
                    card.interview_angle,
                ),
            ).fetchone()
            connection.execute(
                """
                insert into public.deck_card_reviews (card_id, owner_id, deck_id, state)
                values (%s, %s, %s, 'new')
                """,
                (row["id"], owner, deck_id),
            )

        connection.execute(
            """
            update public.decks
            set status = %s,
                generation_model = %s,
                prompt_version = %s,
                ingestion_version_id = %s,
                topic_count = %s,
                card_count = %s,
                metrics_json = %s,
                updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                "ready" if metrics.complete else "partial",
                generated.model_name,
                generated.prompt_version,
                UUID(str(ingestion_version_id)) if ingestion_version_id else None,
                len(topics),
                len(generated.cards),
                _json(metrics.model_dump(mode="json")),
                deck_id,
                owner,
            ),
        )

        # Only one version of a scope is the one you study. Earlier versions
        # stay in the table so their cards keep their review history, but they
        # leave the library — the partial unique index requires it.
        connection.execute(
            """
            update public.decks
            set status = 'failed', updated_at = now()
            where owner_id = %s
              and scope_key = (select scope_key from public.decks where id = %s)
              and id <> %s
              and status in ('ready', 'partial')
            """,
            (owner, deck_id, deck_id),
        )


def fail_deck(
    connection: Connection, *, owner_id: str | UUID, deck_id: UUID
) -> None:
    connection.execute(
        """
        update public.decks
        set status = 'failed', updated_at = now()
        where id = %s and owner_id = %s and status = 'generating'
        """,
        (deck_id, parse_owner_id(owner_id)),
    )


def _summary(row: Any) -> DeckSummary:
    return DeckSummary(
        deck_id=str(row["id"]),
        source_kind=row["source_kind"],
        scope_key=row["scope_key"],
        version=row["version"],
        title=row["title"],
        source_title=row["source_title"],
        status=row["status"],
        card_count=row["card_count"],
        topic_count=row["topic_count"],
        book_id=row["book_id"],
        node_id=row["node_id"],
        video_id=str(row["video_id"]) if row["video_id"] else None,
        metrics=DeckMetrics.model_validate(row["metrics_json"] or {}),
        due_count=row.get("due_count") or 0,
        new_count=row.get("new_count") or 0,
        updated_at=_iso(row["updated_at"]),
    )


def list_decks(
    connection: Connection, *, owner_id: str | UUID
) -> list[DeckSummary]:
    """Every readable deck, with what it owes you today."""

    rows = connection.execute(
        """
        select
            deck.*,
            count(*) filter (
                where review.due_at is not null and review.due_at <= now()
            ) as due_count,
            count(*) filter (where review.state = 'new') as new_count
        from public.decks as deck
        left join public.deck_card_reviews as review
          on review.deck_id = deck.id and review.owner_id = deck.owner_id
        where deck.owner_id = %s and deck.status in ('ready', 'partial')
        group by deck.id
        order by deck.updated_at desc
        """,
        (parse_owner_id(owner_id),),
    ).fetchall()
    return [_summary(row) for row in rows]


def get_deck(
    connection: Connection, *, owner_id: str | UUID, deck_id: str | UUID
) -> DeckSummary:
    row = connection.execute(
        """
        select
            deck.*,
            count(*) filter (
                where review.due_at is not null and review.due_at <= now()
            ) as due_count,
            count(*) filter (where review.state = 'new') as new_count
        from public.decks as deck
        left join public.deck_card_reviews as review
          on review.deck_id = deck.id and review.owner_id = deck.owner_id
        where deck.id = %s and deck.owner_id = %s
        group by deck.id
        """,
        (UUID(str(deck_id)), parse_owner_id(owner_id)),
    ).fetchone()
    if row is None:
        raise DeckNotFoundError(f"no deck {deck_id}")
    return _summary(row)


def _card(row: Any) -> DeckCard:
    return DeckCard(
        card_id=str(row["id"]),
        topic_key=row["topic_key"],
        card_index=row["card_index"],
        card_type=row["card_type"],
        front=row["front"],
        back=CardBack.model_validate(row["back_json"]),
        citations=[
            DeckCitation.model_validate(item) for item in row["citations_json"] or []
        ],
        figures=[DeckFigure.model_validate(item) for item in row["figures_json"] or []],
        interview_priority=row["interview_priority"],
        priority_reason=row["priority_reason"] or "",
        difficulty=row["difficulty"],
        interview_angle=row["interview_angle"],
    )


def _review_state(row: Any) -> ReviewState:
    return ReviewState(
        state=row["state"],
        due_at=_iso(row["due_at"]),
        interval_days=row["interval_days"],
        ease=row["ease"],
        reps=row["reps"],
        lapses=row["lapses"],
        last_reviewed_at=_iso(row["last_reviewed_at"]),
        last_rating=row["last_rating"],
    )


def _queue_card(row: Any) -> QueueCard:
    return QueueCard(
        card=_card(row),
        deck_id=str(row["deck_id"]),
        deck_title=row["deck_title"],
        source_kind=row["source_kind"],
        source_title=row["source_title"],
        book_id=row["book_id"],
        video_id=str(row["video_id"]) if row["video_id"] else None,
        review=_review_state(row),
    )


_CARD_SELECT = """
    select
        card.*,
        deck.title as deck_title,
        deck.source_kind,
        deck.source_title,
        deck.book_id,
        deck.video_id,
        review.state,
        review.due_at,
        review.interval_days,
        review.ease,
        review.reps,
        review.lapses,
        review.last_reviewed_at,
        review.last_rating
    from public.deck_cards as card
    join public.decks as deck
      on deck.id = card.deck_id and deck.owner_id = card.owner_id
    join public.deck_card_reviews as review
      on review.card_id = card.id and review.owner_id = card.owner_id
"""


def deck_cards(
    connection: Connection,
    *,
    owner_id: str | UUID,
    deck_id: str | UUID,
) -> list[QueueCard]:
    rows = connection.execute(
        _CARD_SELECT
        + """
        where card.owner_id = %s and card.deck_id = %s
        order by card.card_index
        """,
        (parse_owner_id(owner_id), UUID(str(deck_id))),
    ).fetchall()
    return [_queue_card(row) for row in rows]


def due_cards(
    connection: Connection,
    *,
    owner_id: str | UUID,
    deck_id: str | UUID | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
) -> list[QueueCard]:
    rows = connection.execute(
        _CARD_SELECT
        + """
        where card.owner_id = %s
          and deck.status in ('ready', 'partial')
          and (%s::uuid is null or card.deck_id = %s::uuid)
          and review.due_at is not null
          and review.due_at <= now()
        order by review.due_at
        limit %s
        """,
        (
            parse_owner_id(owner_id),
            str(deck_id) if deck_id else None,
            str(deck_id) if deck_id else None,
            limit,
        ),
    ).fetchall()
    return [_queue_card(row) for row in rows]


def new_cards(
    connection: Connection,
    *,
    owner_id: str | UUID,
    deck_id: str | UUID | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
) -> list[QueueCard]:
    rows = connection.execute(
        _CARD_SELECT
        + """
        where card.owner_id = %s
          and deck.status in ('ready', 'partial')
          and (%s::uuid is null or card.deck_id = %s::uuid)
          and review.state = 'new'
        order by card.interview_priority desc, card.card_index
        limit %s
        """,
        (
            parse_owner_id(owner_id),
            str(deck_id) if deck_id else None,
            str(deck_id) if deck_id else None,
            limit,
        ),
    ).fetchall()
    return [_queue_card(row) for row in rows]


def counts_today(
    connection: Connection, *, owner_id: str | UUID
) -> tuple[int, int]:
    """Reviews done today, and how many of them introduced a new card.

    Counted from the event log rather than from the scheduling rows: the rows
    say where a card is now, and "how much have I done today" is a question
    only the log can answer.
    """

    row = connection.execute(
        """
        select
            count(*) as reviewed,
            count(*) filter (where prior_state = 'new') as introduced
        from public.deck_review_events
        where owner_id = %s and reviewed_at >= %s
        """,
        (parse_owner_id(owner_id), datetime.combine(date.today(), datetime.min.time(), UTC)),
    ).fetchone()
    return int(row["reviewed"] or 0), int(row["introduced"] or 0)


def record_review(
    connection: Connection,
    *,
    owner_id: str | UUID,
    card_id: str | UUID,
    rating: int,
    elapsed_ms: int | None = None,
    answered_correctly: bool | None = None,
    now: datetime | None = None,
) -> ReviewState:
    """Grade one card: append the event, then move its scheduling row."""

    owner = parse_owner_id(owner_id)
    card = UUID(str(card_id))
    current = connection.execute(
        """
        select deck_id, state, due_at, interval_days, ease, reps, lapses,
               last_reviewed_at, last_rating
        from public.deck_card_reviews
        where card_id = %s and owner_id = %s
        """,
        (card, owner),
    ).fetchone()
    if current is None:
        raise DeckNotFoundError(f"no card {card_id}")

    outcome = schedule_review(_review_state(current), rating, now=now)
    with connection.transaction():
        connection.execute(
            """
            insert into public.deck_review_events (
                owner_id, deck_id, card_id, rating, prior_state,
                next_due_at, elapsed_ms, answered_correctly
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                owner,
                current["deck_id"],
                card,
                rating,
                current["state"],
                outcome.due_at,
                elapsed_ms,
                answered_correctly,
            ),
        )
        connection.execute(
            """
            update public.deck_card_reviews
            set state = %s,
                due_at = %s,
                interval_days = %s,
                ease = %s,
                reps = %s,
                lapses = %s,
                last_reviewed_at = %s,
                last_rating = %s
            where card_id = %s and owner_id = %s
            """,
            (
                outcome.state.state,
                outcome.due_at,
                outcome.state.interval_days,
                outcome.state.ease,
                outcome.state.reps,
                outcome.state.lapses,
                outcome.state.last_reviewed_at,
                outcome.state.last_rating,
                card,
                owner,
            ),
        )
    return outcome.state


def reset_deck_progress(
    connection: Connection, *, owner_id: str | UUID, deck_id: str | UUID
) -> int:
    """Send every card in a deck back to new, keeping the review log intact."""

    fresh = initial_state()
    result = connection.execute(
        """
        update public.deck_card_reviews
        set state = 'new', due_at = null, interval_days = 0, ease = %s,
            reps = 0, lapses = 0, last_reviewed_at = null, last_rating = null
        where deck_id = %s and owner_id = %s
        """,
        (fresh.ease, UUID(str(deck_id)), parse_owner_id(owner_id)),
    )
    return result.rowcount


def get_preferences(
    connection: Connection, *, owner_id: str | UUID
) -> DeckPreferences:
    row = connection.execute(
        """
        select new_cards_per_day, max_reviews_per_day
        from public.deck_preferences
        where owner_id = %s
        """,
        (parse_owner_id(owner_id),),
    ).fetchone()
    if row is None:
        return DeckPreferences()
    return DeckPreferences.model_validate(dict(row))


def save_preferences(
    connection: Connection,
    *,
    owner_id: str | UUID,
    preferences: DeckPreferences,
) -> DeckPreferences:
    connection.execute(
        """
        insert into public.deck_preferences (
            owner_id, new_cards_per_day, max_reviews_per_day, updated_at
        )
        values (%s, %s, %s, now())
        on conflict (owner_id) do update
        set new_cards_per_day = excluded.new_cards_per_day,
            max_reviews_per_day = excluded.max_reviews_per_day,
            updated_at = now()
        """,
        (
            parse_owner_id(owner_id),
            preferences.new_cards_per_day,
            preferences.max_reviews_per_day,
        ),
    )
    return preferences
