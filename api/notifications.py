"""Owner-scoped reminder preferences and persistent notification actions."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from notifications import store
from notifications.contracts import (
    Notification,
    NotificationList,
    ReminderPreferences,
    ReminderPreferencesUpdate,
)
from storage.database import connection as database_connection


router = APIRouter(prefix="/api", tags=["notifications"])


@router.get(
    "/decks/reminder-preferences", response_model=ReminderPreferences
)
async def reminder_preferences(
    owner_id: UUID = Depends(current_owner),
) -> ReminderPreferences:
    """Read the signed-in user’s daily review reminder opt-in, local time, timezone, and next scheduled instant."""

    def run() -> ReminderPreferences:
        with database_connection(readonly=True) as connection:
            return store.get_reminder_preferences(
                connection, owner_id=owner_id
            )

    return await run_in_threadpool(run)


@router.patch(
    "/decks/reminder-preferences", response_model=ReminderPreferences
)
async def update_reminder_preferences(
    request: ReminderPreferencesUpdate,
    owner_id: UUID = Depends(current_owner),
) -> ReminderPreferences:
    """Save daily review reminder preferences and schedule the next future occurrence. The worker creates notifications."""

    def run() -> ReminderPreferences:
        with database_connection() as connection:
            return store.save_reminder_preferences(
                connection, owner_id=owner_id, preferences=request
            )

    return await run_in_threadpool(run)


@router.get("/notifications", response_model=NotificationList)
async def notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    owner_id: UUID = Depends(current_owner),
) -> NotificationList:
    """List non-dismissed notifications and the unread count for the signed-in user."""

    def run() -> NotificationList:
        with database_connection(readonly=True) as connection:
            items, unread = store.list_notifications(
                connection,
                owner_id=owner_id,
                unread_only=unread_only,
                limit=limit,
            )
            return NotificationList(notifications=items, unread_count=unread)

    return await run_in_threadpool(run)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all_notifications(
    owner_id: UUID = Depends(current_owner),
) -> Response:
    """Mark all active notifications as read; return an empty 204 response."""

    def run() -> None:
        with database_connection() as connection:
            store.mark_all_read(connection, owner_id=owner_id)

    await run_in_threadpool(run)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _not_found(error: LookupError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="no such notification"
    )


@router.post("/notifications/{notification_id}/read", response_model=Notification)
async def read_notification(
    notification_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> Notification:
    """Mark one owned notification as read. Repeated acknowledgements preserve its first read time."""

    def run() -> Notification:
        with database_connection() as connection:
            try:
                return store.mark_read(
                    connection,
                    owner_id=owner_id,
                    notification_id=notification_id,
                )
            except LookupError as error:
                raise _not_found(error) from error

    return await run_in_threadpool(run)


@router.post(
    "/notifications/{notification_id}/dismiss", response_model=Notification
)
async def dismiss_notification(
    notification_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> Notification:
    """Dismiss one owned notification and mark it as read."""

    def run() -> Notification:
        with database_connection() as connection:
            try:
                return store.dismiss(
                    connection,
                    owner_id=owner_id,
                    notification_id=notification_id,
                )
            except LookupError as error:
                raise _not_found(error) from error

    return await run_in_threadpool(run)
