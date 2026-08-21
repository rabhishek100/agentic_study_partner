"""Owner-scoped persistence for reminders and their in-app events."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from decks.review_time import next_occurrence
from storage.database import parse_owner_id

from .contracts import Notification, ReminderPreferences, ReminderPreferencesUpdate


def _notification(row: Any) -> Notification:
    return Notification(
        notification_id=str(row["id"]),
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        href=row["href"],
        payload=dict(row["payload_json"] or {}),
        created_at=row["created_at"],
        read_at=row["read_at"],
        dismissed_at=row["dismissed_at"],
    )


def get_reminder_preferences(
    connection: Connection, *, owner_id: str | UUID
) -> ReminderPreferences:
    row = connection.execute(
        """
        select review_reminder_enabled, review_reminder_time,
               review_timezone, next_review_reminder_at
        from public.deck_preferences
        where owner_id = %s
        """,
        (parse_owner_id(owner_id),),
    ).fetchone()
    if row is None:
        return ReminderPreferences()
    return ReminderPreferences(
        enabled=row["review_reminder_enabled"],
        reminder_time=row["review_reminder_time"].strftime("%H:%M"),
        timezone=row["review_timezone"],
        next_reminder_at=row["next_review_reminder_at"],
    )


def save_reminder_preferences(
    connection: Connection,
    *,
    owner_id: str | UUID,
    preferences: ReminderPreferencesUpdate,
    now: datetime | None = None,
) -> ReminderPreferences:
    """Save one complete reminder preference and schedule only a future alert."""

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    next_at = (
        next_occurrence(
            moment,
            timezone_name=preferences.timezone,
            wall_time=preferences.reminder_time,
        )
        if preferences.enabled
        else None
    )
    row = connection.execute(
        """
        insert into public.deck_preferences (
            owner_id, review_reminder_enabled, review_reminder_time,
            review_timezone, next_review_reminder_at, updated_at
        )
        values (%s, %s, %s::time, %s, %s, now())
        on conflict (owner_id) do update
        set review_reminder_enabled = excluded.review_reminder_enabled,
            review_reminder_time = excluded.review_reminder_time,
            review_timezone = excluded.review_timezone,
            next_review_reminder_at = excluded.next_review_reminder_at,
            updated_at = now()
        returning review_reminder_enabled, review_reminder_time,
                  review_timezone, next_review_reminder_at
        """,
        (
            parse_owner_id(owner_id),
            preferences.enabled,
            preferences.reminder_time,
            preferences.timezone,
            next_at,
        ),
    ).fetchone()
    return ReminderPreferences(
        enabled=row["review_reminder_enabled"],
        reminder_time=row["review_reminder_time"].strftime("%H:%M"),
        timezone=row["review_timezone"],
        next_reminder_at=row["next_review_reminder_at"],
    )


def create_daily_review_notification(
    connection: Connection,
    *,
    owner_id: str | UUID,
    local_day: date,
    review_count: int,
    due_count: int,
    new_count: int,
) -> Notification | None:
    """Insert one server-authored daily event, or return None if it exists."""

    if review_count <= 0 or due_count < 0 or new_count < 0:
        raise ValueError("a daily review notification needs a positive queue")
    if due_count + new_count != review_count:
        raise ValueError("due and new counts must equal the review count")
    # The payload is an auditable snapshot. The visible copy deliberately is
    # not: reviews, source toggles, and newly generated cards can change the
    # live Today queue after this event is created.
    body = (
        "It's time for your daily review. "
        "Open Today to see what's ready now."
    )
    row = connection.execute(
        """
        insert into public.notifications (
            owner_id, kind, dedupe_key, local_date, title, body, href,
            payload_json
        )
        values (
            %s, 'daily_cards_review', %s, %s,
            'Your daily cards are ready', %s, '/decks', %s
        )
        on conflict (owner_id, kind, dedupe_key) do nothing
        returning *
        """,
        (
            parse_owner_id(owner_id),
            local_day.isoformat(),
            local_day,
            body,
            Jsonb(
                {
                    "review_count": review_count,
                    "due_count": due_count,
                    "new_count": new_count,
                    "local_date": local_day.isoformat(),
                }
            ),
        ),
    ).fetchone()
    return _notification(row) if row is not None else None


def list_notifications(
    connection: Connection,
    *,
    owner_id: str | UUID,
    unread_only: bool = False,
    limit: int = 50,
) -> tuple[list[Notification], int]:
    owner = parse_owner_id(owner_id)
    if not 1 <= limit <= 100:
        raise ValueError("notification limit must be between 1 and 100")
    rows = connection.execute(
        """
        select * from public.notifications
        where owner_id = %s and dismissed_at is null
          and (%s::boolean = false or read_at is null)
        order by created_at desc, id desc
        limit %s
        """,
        (owner, unread_only, limit),
    ).fetchall()
    unread = connection.execute(
        """
        select count(*) as count from public.notifications
        where owner_id = %s and dismissed_at is null and read_at is null
        """,
        (owner,),
    ).fetchone()["count"]
    return [_notification(row) for row in rows], int(unread)


def mark_read(
    connection: Connection, *, owner_id: str | UUID, notification_id: str | UUID
) -> Notification:
    row = connection.execute(
        """
        update public.notifications
        set read_at = coalesce(read_at, now())
        where id = %s and owner_id = %s and dismissed_at is null
        returning *
        """,
        (UUID(str(notification_id)), parse_owner_id(owner_id)),
    ).fetchone()
    if row is None:
        raise LookupError("no such notification")
    return _notification(row)


def mark_all_read(connection: Connection, *, owner_id: str | UUID) -> int:
    result = connection.execute(
        """
        update public.notifications set read_at = now()
        where owner_id = %s and dismissed_at is null and read_at is null
        """,
        (parse_owner_id(owner_id),),
    )
    return result.rowcount


def dismiss(
    connection: Connection, *, owner_id: str | UUID, notification_id: str | UUID
) -> Notification:
    row = connection.execute(
        """
        update public.notifications
        set dismissed_at = coalesce(dismissed_at, now()),
            read_at = coalesce(read_at, now())
        where id = %s and owner_id = %s
        returning *
        """,
        (UUID(str(notification_id)), parse_owner_id(owner_id)),
    ).fetchone()
    if row is None:
        raise LookupError("no such notification")
    return _notification(row)
