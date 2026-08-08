"""Side chat persistence and its invariants, against a real database."""

import unittest
from uuid import uuid4

from psycopg.errors import CheckViolation

from storage.conversations import (
    append_turn,
    create_conversation,
    delete_conversation,
    list_conversations,
    list_side_chats,
    load_conversation,
    set_conversation_state,
    update_conversation,
)
from storage.database import connection as database_connection
from study.contracts import ConversationState, TurnResult
from tests.postgres import PostgresOwnerMixin


def turn_result(answer: str = "Grounded answer [S1]") -> TurnResult:
    return TurnResult(
        question="What is skew?",
        answer=answer,
        route="retrieval_qa",
        history_dependency="independent",
        standalone_query="What is skew?",
        outcome="answer",
        retrieval_mode="hybrid",
    )


ANCHOR = {
    "anchor_id": "anchor-one",
    "parent_turn_index": 0,
    "quoted_text": "features differ between training and serving [S1]",
}


class SideChatStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def parent(self, title: str = "Main study session"):
        with database_connection(self.database_url) as connection:
            return create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(1,),
                retrieval_mode="hybrid",
                title=title,
            )

    def side_chat(self, parent_id, *, anchors=(ANCHOR,), state=None):
        with database_connection(self.database_url) as connection:
            return create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=(1,),
                retrieval_mode="hybrid",
                title="What does that mean?",
                parent_conversation_id=parent_id,
                anchors=anchors,
                state=state,
            )

    def test_a_side_chat_records_its_parent_and_its_anchors(self):
        parent = self.parent()

        side = self.side_chat(parent["id"])

        self.assertEqual(side["parent_conversation_id"], parent["id"])
        self.assertEqual(side["anchors_json"], [ANCHOR])

    def test_a_root_conversation_has_no_parent_and_no_anchors(self):
        parent = self.parent()

        self.assertIsNone(parent["parent_conversation_id"])
        self.assertEqual(parent["anchors_json"], [])

    def test_anchors_without_a_parent_are_refused_before_reaching_the_database(self):
        with self.assertRaises(ValueError):
            with database_connection(self.database_url) as connection:
                create_conversation(
                    connection,
                    owner_id=self.owner_id,
                    book_ids=(1,),
                    retrieval_mode="hybrid",
                    title="Orphan anchor",
                    anchors=(ANCHOR,),
                )

    def test_a_side_chat_of_a_side_chat_is_rejected(self):
        parent = self.parent()
        side = self.side_chat(parent["id"])

        with self.assertRaises(CheckViolation):
            self.side_chat(side["id"])

    def test_a_conversation_with_side_chats_cannot_become_one(self):
        parent = self.parent()
        self.side_chat(parent["id"])
        other = self.parent("Another session")

        with self.assertRaises(CheckViolation):
            with database_connection(self.database_url) as connection:
                connection.execute(
                    """
                    update conversations set parent_conversation_id = %s
                    where id = %s and owner_id = %s
                    """,
                    (other["id"], parent["id"], self.owner_id),
                )

    def test_a_conversation_cannot_be_its_own_parent(self):
        parent = self.parent()

        with self.assertRaises(CheckViolation):
            with database_connection(self.database_url) as connection:
                connection.execute(
                    """
                    update conversations set parent_conversation_id = id
                    where id = %s and owner_id = %s
                    """,
                    (parent["id"], self.owner_id),
                )

    def test_another_owners_conversation_is_not_a_usable_parent(self):
        parent = self.parent()
        stranger = str(uuid4())
        with database_connection(self.database_url) as connection:
            connection.execute(
                """
                insert into auth.users (id, email, raw_user_meta_data)
                values (%s, %s, '{}'::jsonb)
                """,
                (stranger, f"{stranger}@test.local"),
            )
        try:
            # Two independent defences refuse this: the depth trigger, which
            # looks the parent up by (id, owner_id) and finds nothing, and the
            # composite foreign key on the same pair. The trigger is a BEFORE
            # trigger, so it is the one that reports.
            with self.assertRaises(CheckViolation):
                with database_connection(self.database_url) as connection:
                    create_conversation(
                        connection,
                        owner_id=stranger,
                        book_ids=(1,),
                        retrieval_mode="hybrid",
                        title="Reaching across owners",
                        parent_conversation_id=parent["id"],
                        anchors=(ANCHOR,),
                    )
        finally:
            with database_connection(self.database_url) as connection:
                connection.execute(
                    "delete from auth.users where id = %s",
                    (stranger,),
                )

    def test_the_conversation_list_shows_roots_and_counts_their_side_chats(self):
        parent = self.parent()
        self.side_chat(parent["id"])
        self.side_chat(parent["id"], anchors=({**ANCHOR, "anchor_id": "anchor-two"},))

        with database_connection(self.database_url) as connection:
            listed = list_conversations(connection, owner_id=self.owner_id)

        self.assertEqual([row["id"] for row in listed], [parent["id"]])
        self.assertEqual(listed[0]["side_thread_count"], 2)

    def test_side_chats_are_listed_for_their_parent_with_turn_counts(self):
        parent = self.parent()
        side = self.side_chat(parent["id"])
        with database_connection(self.database_url) as connection:
            append_turn(
                connection,
                side["id"],
                owner_id=self.owner_id,
                question="What does that mean?",
                answer="It means this [S1]",
                result=turn_result().model_dump(mode="json"),
                state={},
            )
            listed = list_side_chats(connection, parent["id"], owner_id=self.owner_id)

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], side["id"])
        self.assertEqual(listed[0]["turn_count"], 1)
        self.assertEqual(listed[0]["anchors_json"], [ANCHOR])

    def test_deleting_a_conversation_removes_its_side_chats(self):
        parent = self.parent()
        side = self.side_chat(parent["id"])

        with database_connection(self.database_url) as connection:
            self.assertTrue(
                delete_conversation(connection, parent["id"], owner_id=self.owner_id)
            )
            self.assertIsNone(
                load_conversation(connection, side["id"], owner_id=self.owner_id)
            )

    def test_deleting_a_side_chat_leaves_its_parent_alone(self):
        parent = self.parent()
        side = self.side_chat(parent["id"])

        with database_connection(self.database_url) as connection:
            self.assertTrue(
                delete_conversation(connection, side["id"], owner_id=self.owner_id)
            )
            self.assertIsNotNone(
                load_conversation(connection, parent["id"], owner_id=self.owner_id)
            )

    def test_anchors_are_replaced_wholesale(self):
        parent = self.parent()
        side = self.side_chat(parent["id"])
        replacement = {
            "anchor_id": "anchor-two",
            "parent_turn_index": 0,
            "quoted_text": "a different sentence",
        }

        with database_connection(self.database_url) as connection:
            updated = update_conversation(
                connection,
                side["id"],
                owner_id=self.owner_id,
                anchors=(replacement,),
            )

        self.assertEqual(updated["anchors_json"], [replacement])

    def test_seeded_state_is_stored_without_promoting_the_side_chat(self):
        parent = self.parent()
        side = self.side_chat(parent["id"])
        state = ConversationState(
            conversation_id=str(side["id"]),
            book_ids=[1],
            previous_answer="The answer being asked about [S1]",
        )

        with database_connection(self.database_url) as connection:
            set_conversation_state(
                connection,
                side["id"],
                owner_id=self.owner_id,
                state=state.model_dump(mode="json"),
            )
            stored = load_conversation(connection, side["id"], owner_id=self.owner_id)

        self.assertEqual(
            ConversationState.model_validate(stored["state_json"]).previous_answer,
            "The answer being asked about [S1]",
        )
        # Seeding is not use: the side chat must not jump above a conversation
        # the reader is actually reading.
        self.assertEqual(stored["updated_at"], side["updated_at"])


if __name__ == "__main__":
    unittest.main()
