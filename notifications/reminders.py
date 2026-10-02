"""Idempotent creation of owner-local daily review reminders."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Mapping

from psycopg import Connection

from observability import traced
from decks import store as deck_store
from decks.contracts import DeckPreferences
from decks.review_time import (
    local_date,
    next_occurrence,
    reminder_time,
    timezone,
)
from decks.scheduler import build_queue

from . import store


logger = logging.getLogger("study_partner.notifications.reminders")


@traced("notifications.reminders.reconcile_due_review_reminders", flow="reminders", operational=True)
def reconcile_due_review_reminders(
    connection: Connection,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Create at most one current-day reminder for each due owner preference.

    Preference rows are the tiny claim queue. A transaction lock plus the
    notification's unique daily identity make this safe with several workers
    and after a crash. Missed days never fan out into stale notifications.
    """

    if not 1 <= limit <= 1_000:
        raise ValueError("reminder limit must be between 1 and 1000")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    created = 0
    with connection.transaction():
        rows = connection.execute(
            """
            select owner_id, new_cards_per_day, max_reviews_per_day,
                   review_reminder_time, review_timezone
            from public.deck_preferences
            where review_reminder_enabled
              and next_review_reminder_at is not null
              and next_review_reminder_at <= %s
            order by next_review_reminder_at, owner_id
            for update skip locked
            limit %s
            """,
            (moment, limit),
        ).fetchall()

        for row in rows:
            owner_id = row["owner_id"]
            try:
                # A nested transaction is a PostgreSQL savepoint. One corrupt
                # preference or owner-specific query failure must not roll
                # back successful reminders for every other due owner.
                with connection.transaction():
                    owner_created = _reconcile_owner(
                        connection, row=row, moment=moment
                    )
                created += owner_created
            except ValueError as error:
                logger.warning(
                    "invalid daily card reminder preference disabled",
                    extra={
                        "owner_id": str(owner_id),
                        "error_detail": str(error),
                    },
                )
                try:
                    with connection.transaction():
                        connection.execute(
                            """
                            update public.deck_preferences
                            set review_reminder_enabled = false,
                                next_review_reminder_at = null,
                                updated_at = now()
                            where owner_id = %s
                            """,
                            (owner_id,),
                        )
                except Exception:
                    logger.exception(
                        "could not disable invalid daily card reminder preference",
                        extra={"owner_id": str(owner_id)},
                    )
            except Exception:
                logger.exception(
                    "daily card reminder owner reconciliation failed",
                    extra={"owner_id": str(owner_id)},
                )
    return created


def _reconcile_owner(
    connection: Connection, *, row: Mapping[str, Any], moment: datetime
) -> int:
    owner_id = row["owner_id"]
    timezone_name = row["review_timezone"]
    wall_time = row["review_reminder_time"]
    # Validate before reading the owner's queue so corrupt scheduling data is
    # classified and permanently disabled rather than retried every minute.
    timezone(timezone_name)
    reminder_time(wall_time)
    preferences = DeckPreferences(
        new_cards_per_day=row["new_cards_per_day"],
        max_reviews_per_day=row["max_reviews_per_day"],
    )
    due = deck_store.due_cards(connection, owner_id=owner_id)
    fresh = deck_store.new_cards(connection, owner_id=owner_id)
    reviewed, introduced = deck_store.counts_today(
        connection,
        owner_id=owner_id,
        timezone_name=timezone_name,
        now=moment,
    )
    queue = build_queue(
        due=due,
        fresh=fresh,
        preferences=preferences,
        reviewed_today=reviewed,
        new_introduced_today=introduced,
    )
    due_count = sum(item.review.due_at is not None for item in queue)
    new_count = len(queue) - due_count
    created = 0
    if queue:
        notification = store.create_daily_review_notification(
            connection,
            owner_id=owner_id,
            local_day=local_date(moment, timezone_name),
            review_count=len(queue),
            due_count=due_count,
            new_count=new_count,
        )
        created = notification is not None

    connection.execute(
        """
        update public.deck_preferences
        set next_review_reminder_at = %s, updated_at = now()
        where owner_id = %s
        """,
        (
            next_occurrence(
                moment,
                timezone_name=timezone_name,
                wall_time=wall_time,
            ),
            owner_id,
        ),
    )
    return int(created)
