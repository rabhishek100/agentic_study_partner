"""Reading-session persistence and its invariants, against a real database.

A reading session is a conversation whose subject is the source rather than
the thread. What has to hold is that there is exactly one per source per
reader, that its questions hang off it as side chats, that it stays out of the
ask-first history, and that turning a page does not count as using it.
"""

import unittest
from uuid import uuid4

from psycopg.errors import CheckViolation, UniqueViolation

from storage.conversations import (
    append_turn,
    create_conversation,
    delete_conversation,
    list_conversations,
    list_reading_sessions,
    list_side_chats,
    reading_session,
    set_source_position,
)
from storage.database import connection as database_connection
from study.contracts import ConversationState, TurnResult
from tests.postgres import PostgresOwnerMixin

PAGE_ANCHOR = {
    "kind": "document_page",
    "anchor_id": "anchor-one",
    "book_id": 1,
    "page": 108,
}


class ReadingSessionStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def session(self, book_id: int = 1, title: str = "Designing ML Systems"):
        with database_connection(self.database_url) as connection:
            return create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(book_id,),
                retrieval_mode="hybrid_rerank",
                title=title,
                session_kind="read",
            )

    def test_a_reading_session_records_its_kind_and_its_one_source(self):
        created = self.session()

        self.assertEqual(created["session_kind"], "read")
        self.assertEqual(created["book_ids"], [1])
        self.assertIsNone(created["source_position"])

    def test_one_session_per_source_per_reader(self):
        # Resuming rather than accumulating is the whole point: a second
        # session would leave last week's margin questions behind while
        # looking identical to the one that has them.
        self.session()

        with self.assertRaises(UniqueViolation):
            self.session()

    def test_a_different_source_gets_its_own_session(self):
        first = self.session(book_id=1)
        second = self.session(book_id=2, title="AI Engineering")

        self.assertNotEqual(first["id"], second["id"])

    def test_a_reading_session_is_found_by_its_source(self):
        created = self.session()

        with database_connection(self.database_url) as connection:
            found = reading_session(connection, owner_id=self.owner_id, book_id=1)
            missing = reading_session(connection, owner_id=self.owner_id, book_id=99)

        self.assertEqual(found["id"], created["id"])
        self.assertIsNone(missing)

    def test_a_session_scoped_to_two_sources_is_refused(self):
        # The ladder's first rung is "the source the reader has open", and a
        # session over three books has no first rung to speak of.
        with self.assertRaises(CheckViolation):
            with database_connection(self.database_url) as connection:
                create_conversation(
                    connection,
                    owner_id=self.owner_id,
                    book_ids=(1, 2),
                    retrieval_mode="hybrid_rerank",
                    title="Two books at once",
                    session_kind="read",
                )

    def test_a_reading_session_can_never_be_a_side_chat(self):
        parent = self.session()

        with self.assertRaises(CheckViolation):
            with database_connection(self.database_url) as connection:
                create_conversation(
                    connection,
                    owner_id=self.owner_id,
                    book_ids=(1,),
                    retrieval_mode="hybrid_rerank",
                    title="A session hanging off a session",
                    parent_conversation_id=parent["id"],
                    session_kind="read",
                )


class ReadingPositionTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
            self.created = create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(1,),
                retrieval_mode="hybrid_rerank",
                title="Designing ML Systems",
                session_kind="read",
            )

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def test_the_page_the_reader_reached_is_recorded(self):
        with database_connection(self.database_url) as connection:
            updated = set_source_position(
                connection,
                self.created["id"],
                owner_id=self.owner_id,
                position={"page": 112},
            )

        self.assertEqual(updated["source_position"], {"page": 112})

    def test_turning_a_page_does_not_count_as_using_the_session(self):
        # Position is written on every page turn. If that touched `updated_at`
        # a book being idly scrolled would climb above the one being worked in.
        with database_connection(self.database_url) as connection:
            updated = set_source_position(
                connection,
                self.created["id"],
                owner_id=self.owner_id,
                position={"page": 112},
            )

        self.assertEqual(updated["updated_at"], self.created["updated_at"])

    def test_an_ask_first_conversation_has_no_position_to_set(self):
        with database_connection(self.database_url) as connection:
            thread = create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(1,),
                retrieval_mode="hybrid",
                title="An ordinary conversation",
            )
            updated = set_source_position(
                connection,
                thread["id"],
                owner_id=self.owner_id,
                position={"page": 112},
            )

        self.assertIsNone(updated)

    def test_another_owners_session_cannot_be_moved(self):
        with database_connection(self.database_url) as connection:
            updated = set_source_position(
                connection,
                self.created["id"],
                owner_id=str(uuid4()),
                position={"page": 112},
            )

        self.assertIsNone(updated)


class ReadingSessionListingTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def read_session(self, book_id: int = 1):
        with database_connection(self.database_url) as connection:
            return create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(book_id,),
                retrieval_mode="hybrid_rerank",
                title=f"Book {book_id}",
                session_kind="read",
            )

    def question(self, session_id):
        with database_connection(self.database_url) as connection:
            return create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(1,),
                retrieval_mode="hybrid_rerank",
                title="Why is accuracy the wrong measure?",
                parent_conversation_id=session_id,
                anchors=(PAGE_ANCHOR,),
            )

    def test_a_reading_session_stays_out_of_the_ask_first_history(self):
        # It would sit there named after a book, with none of its questions
        # under it, because those are its side chats.
        self.read_session()
        with database_connection(self.database_url) as connection:
            thread = create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(1,),
                retrieval_mode="hybrid",
                title="An ordinary conversation",
            )
            listed = list_conversations(connection, owner_id=self.owner_id)

        self.assertEqual([row["id"] for row in listed], [thread["id"]])

    def test_the_questions_asked_in_a_session_are_its_side_chats(self):
        session = self.read_session()
        asked = self.question(session["id"])

        with database_connection(self.database_url) as connection:
            children = list_side_chats(
                connection,
                session["id"],
                owner_id=self.owner_id,
            )
            sessions = list_reading_sessions(connection, owner_id=self.owner_id)

        self.assertEqual([row["id"] for row in children], [asked["id"]])
        self.assertEqual(sessions[0]["question_count"], 1)
        self.assertEqual(children[0]["anchors_json"], [PAGE_ANCHOR])

    def test_deleting_a_session_takes_its_questions_with_it(self):
        session = self.read_session()
        asked = self.question(session["id"])

        with database_connection(self.database_url) as connection:
            delete_conversation(connection, session["id"], owner_id=self.owner_id)
            remaining = list_reading_sessions(connection, owner_id=self.owner_id)
            orphan = reading_session(connection, owner_id=self.owner_id, book_id=1)

        self.assertEqual(remaining, [])
        self.assertIsNone(orphan)
        self.assertIsNotNone(asked["id"])

    def test_sessions_are_listed_most_recently_read_first(self):
        first = self.read_session(book_id=1)
        second = self.read_session(book_id=2)
        # A recorded turn is what "used" means; a page turn deliberately is not.
        with database_connection(self.database_url) as connection:
            append_turn(
                connection,
                first["id"],
                owner_id=self.owner_id,
                question="Why is accuracy the wrong measure?",
                answer="Because it scores the shortcut.",
                result=TurnResult(
                    question="Why is accuracy the wrong measure?",
                    answer="Because it scores the shortcut.",
                    route="retrieval_qa",
                    history_dependency="independent",
                    outcome="answer",
                ).model_dump(mode="json"),
                state=ConversationState(
                    conversation_id=str(first["id"]),
                    book_ids=[1],
                ).model_dump(mode="json"),
            )
            listed = list_reading_sessions(connection, owner_id=self.owner_id)

        self.assertEqual(
            [row["id"] for row in listed],
            [first["id"], second["id"]],
        )


if __name__ == "__main__":
    unittest.main()
