"""Watch-session persistence and its invariants, against a real database.

The lecture counterpart of `test_reading_sessions.py`, and it asserts the same
things because the same things matter: one session per lecture, questions that
hang off it, and a position that does not count as using it.
"""

import unittest
from uuid import uuid4

from psycopg.errors import CheckViolation, UniqueViolation

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.conversation_store import (
    create_conversation,
    delete_conversation,
    list_conversations,
    list_side_chats,
    list_watch_sessions,
    set_source_position,
    watch_session,
)

MOMENT_ANCHOR = {
    "kind": "lecture_moment",
    "anchor_id": "anchor-one",
    "timestamp_ms": 724000,
}


class WatchSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@watch-session.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)
            self.other = publish_video_with_evidence(
                database,
                owner_id=self.owner,
                title="Second lecture",
                url="https://youtu.be/zyxwvutsrqp",
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def session(self, video_id=None):
        with connection(self.database_url) as database:
            return create_conversation(
                database,
                owner_id=self.owner,
                video_id=video_id or self.video.video_id,
                title="Lecture 1 — Transformer",
                session_kind="watch",
            )

    def test_a_watch_session_records_its_kind_and_its_lecture(self):
        created = self.session()

        self.assertEqual(created["session_kind"], "watch")
        self.assertIsNone(created["source_position"])

    def test_one_session_per_lecture_per_viewer(self):
        self.session()

        with self.assertRaises(UniqueViolation):
            self.session()

    def test_a_different_lecture_gets_its_own_session(self):
        first = self.session()
        second = self.session(video_id=self.other.video_id)

        self.assertNotEqual(first["id"], second["id"])

    def test_a_watch_session_is_found_by_its_lecture(self):
        created = self.session()

        with connection(self.database_url) as database:
            found = watch_session(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
            )
            missing = watch_session(
                database,
                owner_id=self.owner,
                video_id=self.other.video_id,
            )

        self.assertEqual(found["id"], created["id"])
        self.assertIsNone(missing)

    def test_a_watch_session_can_never_be_a_side_chat(self):
        parent = self.session()

        with self.assertRaises(CheckViolation):
            with connection(self.database_url) as database:
                create_conversation(
                    database,
                    owner_id=self.owner,
                    video_id=self.video.video_id,
                    title="A session hanging off a session",
                    parent_conversation_id=parent["id"],
                    session_kind="watch",
                )

    def test_the_moment_the_viewer_reached_is_recorded(self):
        created = self.session()

        with connection(self.database_url) as database:
            updated = set_source_position(
                database,
                created["id"],
                owner_id=self.owner,
                position={"timestamp_ms": 724000},
            )

        self.assertEqual(updated["source_position"], {"timestamp_ms": 724000})

    def test_the_playhead_moving_does_not_count_as_using_the_session(self):
        # It reports several times a second while a lecture plays. Letting that
        # reorder history would put a lecture left running above one being
        # worked in.
        created = self.session()

        with connection(self.database_url) as database:
            updated = set_source_position(
                database,
                created["id"],
                owner_id=self.owner,
                position={"timestamp_ms": 724000},
            )

        self.assertEqual(updated["updated_at"], created["updated_at"])

    def test_an_ask_first_conversation_has_no_position_to_set(self):
        with connection(self.database_url) as database:
            thread = create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="An ordinary conversation",
            )
            updated = set_source_position(
                database,
                thread["id"],
                owner_id=self.owner,
                position={"timestamp_ms": 724000},
            )

        self.assertIsNone(updated)

    def test_a_watch_session_stays_out_of_the_ask_first_history(self):
        self.session()
        with connection(self.database_url) as database:
            thread = create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="An ordinary conversation",
            )
            listed = list_conversations(database, owner_id=self.owner)

        self.assertEqual([row["id"] for row in listed], [thread["id"]])

    def test_the_questions_asked_in_a_session_are_its_side_chats(self):
        created = self.session()
        with connection(self.database_url) as database:
            asked = create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="Why divide by the square root?",
                parent_conversation_id=created["id"],
                anchors=(
                    {**MOMENT_ANCHOR, "video_id": str(self.video.video_id)},
                ),
            )
            children = list_side_chats(database, created["id"], owner_id=self.owner)
            sessions = list_watch_sessions(database, owner_id=self.owner)

        self.assertEqual([row["id"] for row in children], [asked["id"]])
        self.assertEqual(sessions[0]["question_count"], 1)

    def test_deleting_a_session_takes_its_questions_with_it(self):
        created = self.session()
        with connection(self.database_url) as database:
            create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="Why divide by the square root?",
                parent_conversation_id=created["id"],
                anchors=(
                    {**MOMENT_ANCHOR, "video_id": str(self.video.video_id)},
                ),
            )
            delete_conversation(database, created["id"], owner_id=self.owner)
            remaining = list_watch_sessions(database, owner_id=self.owner)

        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
