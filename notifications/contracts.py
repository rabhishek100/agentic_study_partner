"""Typed API and store contracts for persistent notifications."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from decks.contracts import ContractModel
from decks.review_time import reminder_time, timezone


class ReminderPreferencesUpdate(ContractModel):
    enabled: bool
    reminder_time: str
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("reminder_time")
    @classmethod
    def valid_reminder_time(cls, value: str) -> str:
        return reminder_time(value).strftime("%H:%M")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        normalized = value.strip()
        timezone(normalized)
        return normalized


class ReminderPreferences(ReminderPreferencesUpdate):
    enabled: bool = False
    reminder_time: str = "19:00"
    timezone: str = "UTC"
    next_reminder_at: datetime | None = None


class Notification(ContractModel):
    notification_id: str
    kind: Literal["daily_cards_review"]
    title: str
    body: str
    href: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    read_at: datetime | None = None
    dismissed_at: datetime | None = None


class NotificationList(ContractModel):
    notifications: list[Notification] = Field(default_factory=list)
    unread_count: int = Field(default=0, ge=0)
