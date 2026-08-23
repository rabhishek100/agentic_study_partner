"""Daily reminder scheduling, persistence, and owner-scoped API behavior."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from decks import store as deck_store
from decks.contracts import CardBack, DeckCard, DeckCitation
from decks.generate import GeneratedDeck
from decks.review_time import local_day_bounds, next_occurrence
from decks.topics import Topic, book_scope_key
from decks.validate import ValidationTally, build_metrics
from notifications import store
from notifications.contracts import ReminderPreferencesUpdate
from notifications.reminders import reconcile_due_review_reminders
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.scope import resolve_chapter
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class ReviewNotificationTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _new_card(self) -> tuple[int, str, str]:
        book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Reminder Book",
            author=None,
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=book_id
        )
        node_id = scope.root_node_id
        topic = Topic(
            key=f"node:{node_id}",
            ordinal=0,
            label=scope.display_path,
            required=True,
            evidence_text=f"[N{node_id}:P1]\nReminder evidence.",
            allowed_markers=frozenset({f"[N{node_id}:P1]"}),
            node_id=node_id,
            start_page=1,
            end_page=1,
        )
        card = DeckCard(
            topic_key=topic.key,
            card_index=0,
            card_type="qa",
            front="What should I review?",
            back=CardBack(answer="The grounded card.", say_it_aloud="The card."),
            citations=[
                DeckCitation(
                    marker=f"[N{node_id}:P1]", node_id=node_id, page=1
                )
            ],
            interview_priority=4,
        )
        deck_id, _ = deck_store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=book_scope_key(book_id, node_id),
            title=scope.display_path,
            source_title="Reminder Book",
            book_id=book_id,
            node_id=node_id,
        )
        tally = ValidationTally(generated=1)
        tally.keep(card)
        deck_store.store_deck(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
            topics=(topic,),
            generated=GeneratedDeck(
                inventory=None,
                cards=(card,),
                metrics=build_metrics(
                    tally,
                    topics=(topic,),
                    covered_keys={topic.key},
                    repair_attempted=False,
                ),
                model_name="test/model",
                prompt_version="test",
            ),
        )
        card_id = self.connection.execute(
            "select id from public.deck_cards where deck_id = %s",
            (deck_id,),
        ).fetchone()["id"]
        return book_id, str(deck_id), str(card_id)

    def test_reminders_are_disabled_by_default_and_schedule_only_the_future(self):
        self.assertEqual(
            store.get_reminder_preferences(
                self.connection, owner_id=self.owner_id
            ).model_dump(),
            {
                "enabled": False,
                "reminder_time": "19:00",
                "timezone": "UTC",
                "next_reminder_at": None,
            },
        )

        now = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
        saved = store.save_reminder_preferences(
            self.connection,
            owner_id=self.owner_id,
            preferences=ReminderPreferencesUpdate(
                enabled=True,
                reminder_time="18:00",
                timezone="Asia/Kolkata",
            ),
            now=now,
        )

        self.assertEqual(saved.reminder_time, "18:00")
        self.assertEqual(saved.timezone, "Asia/Kolkata")
        # 19:30 local is already past 18:00, so enabling never surprises the
        # owner with an immediate catch-up alert.
        self.assertEqual(
            saved.next_reminder_at,
            datetime(2026, 8, 22, 12, 30, tzinfo=UTC),
        )

    def test_owner_local_review_day_bounds_drive_today_counts(self):
        _, deck_id, card_id = self._new_card()
        self.connection.execute(
            """
            insert into public.deck_review_events (
                owner_id, deck_id, card_id, rating, prior_state, reviewed_at
            ) values (%s, %s, %s, 3, 'new', %s)
            """,
            (
                self.owner_id,
                deck_id,
                card_id,
                datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
            ),
        )
        now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)

        self.assertEqual(
            deck_store.counts_today(
                self.connection,
                owner_id=self.owner_id,
                timezone_name="UTC",
                now=now,
            ),
            (0, 0),
        )
        self.assertEqual(
            deck_store.counts_today(
                self.connection,
                owner_id=self.owner_id,
                timezone_name="Asia/Kolkata",
                now=now,
            ),
            (1, 1),
        )

    def test_due_reconciliation_creates_one_persistent_daily_notification(self):
        self._new_card()
        moment = datetime.now(UTC)
        store.save_reminder_preferences(
            self.connection,
            owner_id=self.owner_id,
            preferences=ReminderPreferencesUpdate(
                enabled=True, reminder_time="19:00", timezone="UTC"
            ),
            now=moment,
        )
        self.connection.execute(
            """
            update public.deck_preferences
            set next_review_reminder_at = %s
            where owner_id = %s
            """,
            (moment - timedelta(seconds=1), self.owner_id),
        )

        self.assertEqual(
            reconcile_due_review_reminders(self.connection, now=moment), 1
        )
        notifications, unread = store.list_notifications(
            self.connection, owner_id=self.owner_id
        )
        self.assertEqual(unread, 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].href, "/decks?review=today")
        self.assertEqual(
            notifications[0].body,
            "It's time for your daily review. "
            "Open Today to see what's ready now.",
        )
        self.assertEqual(notifications[0].payload["review_count"], 1)
        self.assertEqual(notifications[0].payload["new_count"], 1)
        self.assertNotIn("What should I review", notifications[0].body)

        # Even if a stale scheduler timestamp is replayed, the owner-local day
        # is the durable idempotency key.
        self.connection.execute(
            """
            update public.deck_preferences
            set next_review_reminder_at = %s
            where owner_id = %s
            """,
            (moment - timedelta(seconds=1), self.owner_id),
        )
        self.assertEqual(
            reconcile_due_review_reminders(self.connection, now=moment), 0
        )
        self.assertEqual(
            len(store.list_notifications(self.connection, owner_id=self.owner_id)[0]),
            1,
        )

    def test_paused_sources_do_not_create_a_reminder(self):
        book_id, _, _ = self._new_card()
        moment = datetime.now(UTC)
        store.save_reminder_preferences(
            self.connection,
            owner_id=self.owner_id,
            preferences=ReminderPreferencesUpdate(
                enabled=True, reminder_time="19:00", timezone="UTC"
            ),
            now=moment,
        )
        self.connection.execute(
            "update public.books set cards_enabled = false where id = %s",
            (book_id,),
        )
        self.connection.execute(
            """
            update public.deck_preferences set next_review_reminder_at = %s
            where owner_id = %s
            """,
            (moment - timedelta(seconds=1), self.owner_id),
        )

        self.assertEqual(
            reconcile_due_review_reminders(self.connection, now=moment), 0
        )
        self.assertEqual(
            store.list_notifications(self.connection, owner_id=self.owner_id),
            ([], 0),
        )

    def test_invalid_owner_preference_is_disabled_without_blocking_others(self):
        self._new_card()
        invalid_owner = uuid4()
        self.connection.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (invalid_owner, f"{invalid_owner}@test.local"),
        )

        def delete_invalid_owner() -> None:
            with database_connection(self.database_url) as connection:
                connection.execute(
                    "delete from auth.users where id = %s", (invalid_owner,)
                )

        self.addCleanup(delete_invalid_owner)
        moment = datetime.now(UTC)
        store.save_reminder_preferences(
            self.connection,
            owner_id=self.owner_id,
            preferences=ReminderPreferencesUpdate(
                enabled=True, reminder_time="19:00", timezone="UTC"
            ),
            now=moment,
        )
        self.connection.execute(
            """
            update public.deck_preferences
            set next_review_reminder_at = %s
            where owner_id = %s
            """,
            (moment - timedelta(seconds=1), self.owner_id),
        )
        self.connection.execute(
            """
            insert into public.deck_preferences (
                owner_id, review_reminder_enabled, review_reminder_time,
                review_timezone, next_review_reminder_at
            ) values (%s, true, time '19:00', 'Mars/Olympus', %s)
            """,
            (invalid_owner, moment - timedelta(seconds=1)),
        )

        with self.assertLogs(
            "study_partner.notifications.reminders", level="WARNING"
        ) as logs:
            created = reconcile_due_review_reminders(
                self.connection, now=moment
            )

        self.assertEqual(created, 1)
        self.assertTrue(
            any("invalid daily card reminder" in line for line in logs.output)
        )
        invalid = self.connection.execute(
            """
            select review_reminder_enabled, next_review_reminder_at
            from public.deck_preferences where owner_id = %s
            """,
            (invalid_owner,),
        ).fetchone()
        self.assertFalse(invalid["review_reminder_enabled"])
        self.assertIsNone(invalid["next_review_reminder_at"])
        self.assertEqual(
            len(
                store.list_notifications(
                    self.connection, owner_id=self.owner_id
                )[0]
            ),
            1,
        )

    def test_read_dismiss_and_read_all_are_idempotent(self):
        first = store.create_daily_review_notification(
            self.connection,
            owner_id=self.owner_id,
            local_day=date(2026, 8, 20),
            review_count=1,
            due_count=1,
            new_count=0,
        )
        second = store.create_daily_review_notification(
            self.connection,
            owner_id=self.owner_id,
            local_day=date(2026, 8, 21),
            review_count=1,
            due_count=0,
            new_count=1,
        )
        assert first is not None and second is not None

        read = store.mark_read(
            self.connection,
            owner_id=self.owner_id,
            notification_id=first.notification_id,
        )
        again = store.mark_read(
            self.connection,
            owner_id=self.owner_id,
            notification_id=first.notification_id,
        )
        self.assertEqual(read.read_at, again.read_at)
        self.assertEqual(store.mark_all_read(self.connection, owner_id=self.owner_id), 1)
        self.assertEqual(store.mark_all_read(self.connection, owner_id=self.owner_id), 0)
        dismissed = store.dismiss(
            self.connection,
            owner_id=self.owner_id,
            notification_id=second.notification_id,
        )
        self.assertIsNotNone(dismissed.dismissed_at)
        self.assertEqual(
            [item.notification_id for item in store.list_notifications(
                self.connection, owner_id=self.owner_id
            )[0]],
            [first.notification_id],
        )

    def test_dst_and_local_day_helpers_are_deterministic(self):
        spring = next_occurrence(
            datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
            timezone_name="America/New_York",
            wall_time="02:30",
        )
        # The nonexistent 02:30 wall time maps through the DST gap once.
        self.assertEqual(spring, datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
        start, end = local_day_bounds(
            datetime(2026, 11, 1, 12, 0, tzinfo=UTC), "America/New_York"
        )
        self.assertEqual(end - start, timedelta(hours=25))


class ReviewNotificationApiTests(
    PostgresOwnerMixin, unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.foreign_owner = uuid4()
        with database_connection(self.database_url) as connection:
            connection.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.foreign_owner, f"{self.foreign_owner}@test.local"),
            )
            self.own = store.create_daily_review_notification(
                connection,
                owner_id=self.owner_id,
                local_day=date(2026, 8, 20),
                review_count=2,
                due_count=1,
                new_count=1,
            )
            self.foreign = store.create_daily_review_notification(
                connection,
                owner_id=self.foreign_owner,
                local_day=date(2026, 8, 20),
                review_count=1,
                due_count=1,
                new_count=0,
            )
        app.dependency_overrides[current_owner] = lambda: UUID(self.owner_id)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(current_owner, None)
        with database_connection(self.database_url) as connection:
            connection.execute(
                "delete from auth.users where id = %s", (self.foreign_owner,)
            )
        self.tearDownPostgresOwner()

    async def _client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def test_preferences_validate_timezone_and_remain_dedicated(self):
        async with await self._client() as client:
            defaults = await client.get("/api/decks/reminder-preferences")
            incomplete = await client.patch(
                "/api/decks/reminder-preferences", json={}
            )
            invalid = await client.patch(
                "/api/decks/reminder-preferences",
                json={
                    "enabled": True,
                    "reminder_time": "19:00",
                    "timezone": "Mars/Olympus",
                },
            )
            saved = await client.patch(
                "/api/decks/reminder-preferences",
                json={
                    "enabled": True,
                    "reminder_time": "18:30",
                    "timezone": "Asia/Kolkata",
                },
            )

        self.assertEqual(defaults.status_code, 200, defaults.text)
        self.assertFalse(defaults.json()["enabled"])
        self.assertEqual(incomplete.status_code, 422, incomplete.text)
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["reminder_time"], "18:30")
        self.assertIsNotNone(saved.json()["next_reminder_at"])

    async def test_notifications_are_owner_scoped_and_acknowledgeable(self):
        assert self.own is not None and self.foreign is not None
        async with await self._client() as client:
            listed = await client.get("/api/notifications")
            foreign = await client.post(
                f"/api/notifications/{self.foreign.notification_id}/read"
            )
            read = await client.post(
                f"/api/notifications/{self.own.notification_id}/read"
            )
            all_read = await client.post("/api/notifications/read-all")
            dismissed = await client.post(
                f"/api/notifications/{self.own.notification_id}/dismiss"
            )
            after = await client.get("/api/notifications")

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["unread_count"], 1)
        self.assertEqual(
            [item["notification_id"] for item in listed.json()["notifications"]],
            [self.own.notification_id],
        )
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertEqual(read.status_code, 200, read.text)
        self.assertIsNotNone(read.json()["read_at"])
        self.assertEqual(all_read.status_code, 204, all_read.text)
        self.assertEqual(dismissed.status_code, 200, dismissed.text)
        self.assertIsNotNone(dismissed.json()["dismissed_at"])
        self.assertEqual(after.json(), {"notifications": [], "unread_count": 0})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
