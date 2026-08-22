"""Owner-local day and reminder scheduling helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def timezone(name: str) -> ZoneInfo:
    """Return one IANA timezone or reject a value the worker cannot schedule."""

    value = name.strip()
    if not value or len(value) > 100:
        raise ValueError("timezone must be a valid IANA timezone")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError("timezone must be a valid IANA timezone") from error


def reminder_time(value: str | time) -> time:
    """Normalize a minute-precision wall-clock time."""

    if isinstance(value, time):
        parsed = value.replace(second=0, microsecond=0, tzinfo=None)
    else:
        try:
            parsed = datetime.strptime(value.strip(), "%H:%M").time()
        except ValueError as error:
            raise ValueError("reminder_time must use HH:MM") from error
    return parsed


def local_date(moment: datetime, timezone_name: str) -> date:
    return _aware(moment).astimezone(timezone(timezone_name)).date()


def local_day_bounds(
    moment: datetime, timezone_name: str
) -> tuple[datetime, datetime]:
    """UTC bounds for the calendar day containing ``moment`` for one owner."""

    zone = timezone(timezone_name)
    current = _aware(moment).astimezone(zone)
    start = datetime.combine(current.date(), time.min, tzinfo=zone)
    end = datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def next_occurrence(
    moment: datetime, *, timezone_name: str, wall_time: str | time
) -> datetime:
    """Return the next future owner-local occurrence in UTC.

    ``zoneinfo`` maps a wall time in a daylight-saving gap to its corresponding
    post-gap instant and chooses the first occurrence of an ambiguous fall-back
    time. The persisted daily dedupe key still guarantees one event.
    """

    zone = timezone(timezone_name)
    now = _aware(moment)
    local_now = now.astimezone(zone)
    requested = reminder_time(wall_time)
    candidate = datetime.combine(local_now.date(), requested, tzinfo=zone)
    if candidate.astimezone(UTC) <= now:
        candidate = datetime.combine(
            local_now.date() + timedelta(days=1), requested, tzinfo=zone
        )
    return candidate.astimezone(UTC)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("a timezone-aware moment is required")
    return moment.astimezone(UTC)
