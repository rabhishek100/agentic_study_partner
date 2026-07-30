"""Owner-scoped conversation persistence against a real database."""

import unittest

from storage.conversations import (
    append_turn,
    create_conversation,
    delete_conversation,
    derive_title,
    list_conversations,
    load_conversation,
    load_turns,
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


class DeriveTitleTests(unittest.TestCase):
    def test_a_short_question_becomes_the_title_verbatim(self):
        self.assertEqual(derive_title("What is skew?"), "What is skew?")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(derive_title("  What   is\nskew? "), "What is skew?")

    def test_a_long_question_is_cut_on_a_word_boundary(self):
        title = derive_title(
            "Explain in detail how training-serving skew arises in production "
            "machine learning systems and what to do about it"
        )
        self.assertLessEqual(len(title), 61)
        self.assertTrue(title.endswith("…"))
        self.assertFalse(title.rstrip("…").endswith(" "))

    def test_an_empty_question_still_yields_a_usable_name(self):
        self.assertEqual(derive_title("   "), "New conversation")


class ConversationStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def create(
        self,
        title: str = "First",
        book_ids=(1,),
        prompt_profile: dict | None = None,
    ):
        with database_connection(self.database_url) as connection:
            return create_conversation(
                connection,
                owner_id=self.owner_id,
                book_ids=book_ids,
                retrieval_mode="hybrid",
                title=title,
                prompt_profile=prompt_profile,
            )

    def test_a_conversation_round_trips(self):
        created = self.create(title="Skew")

        with database_connection(self.database_url, readonly=True) as connection:
            loaded = load_conversation(
                connection, created["id"], owner_id=self.owner_id
            )

        self.assertEqual(loaded["title"], "Skew")
        self.assertEqual(list(loaded["book_ids"]), [1])
        self.assertEqual(loaded["retrieval_mode"], "hybrid")

    def test_a_conversation_snapshots_and_updates_its_prompt_profile(self):
        created = self.create(
            prompt_profile={"interview_instructions": "Initial instructions"}
        )

        with database_connection(self.database_url) as connection:
            updated = update_conversation(
                connection,
                created["id"],
                owner_id=self.owner_id,
                prompt_profile={"interview_instructions": "Updated instructions"},
            )

        self.assertEqual(
            created["prompt_profile_json"]["interview_instructions"],
            "Initial instructions",
        )
        self.assertEqual(
            updated["prompt_profile_json"]["interview_instructions"],
            "Updated instructions",
        )

    def test_a_conversation_must_be_scoped_to_at_least_one_book(self):
        with database_connection(self.database_url) as connection:
            with self.assertRaises(ValueError):
                create_conversation(
                    connection,
                    owner_id=self.owner_id,
                    book_ids=[],
                    retrieval_mode="hybrid",
                    title="Empty",
                )

    def test_turns_are_appended_in_order_without_being_told_the_index(self):
        created = self.create()
        state = ConversationState(
            conversation_id=str(created["id"]),
            book_ids=[1],
        )

        with database_connection(self.database_url) as connection:
            first = append_turn(
                connection,
                created["id"],
                owner_id=self.owner_id,
                question="One?",
                answer="A",
                result=turn_result("A").model_dump(mode="json"),
                state=state.model_dump(mode="json"),
            )
            second = append_turn(
                connection,
                created["id"],
                owner_id=self.owner_id,
                question="Two?",
                answer="B",
                result=turn_result("B").model_dump(mode="json"),
                state=state.model_dump(mode="json"),
            )

        self.assertEqual((first, second), (0, 1))

        with database_connection(self.database_url, readonly=True) as connection:
            turns = load_turns(connection, created["id"], owner_id=self.owner_id)

        self.assertEqual([turn["question"] for turn in turns], ["One?", "Two?"])

    def test_a_stored_turn_rebuilds_its_full_result(self):
        """Resuming must render references and the inspector, not just text."""

        created = self.create()
        original = turn_result()
        with database_connection(self.database_url) as connection:
            append_turn(
                connection,
                created["id"],
                owner_id=self.owner_id,
                question="What is skew?",
                answer=original.answer,
                result=original.model_dump(mode="json"),
                state={"conversation_id": str(created["id"]), "book_ids": [1]},
            )
            turns = load_turns(connection, created["id"], owner_id=self.owner_id)

        restored = TurnResult.model_validate(turns[0]["result_json"])
        self.assertEqual(restored, original)

    def test_appending_a_turn_refreshes_the_resume_checkpoint(self):
        created = self.create()
        state = ConversationState(
            conversation_id=str(created["id"]),
            book_ids=[1],
            previous_answer="A",
        )

        with database_connection(self.database_url) as connection:
            append_turn(
                connection,
                created["id"],
                owner_id=self.owner_id,
                question="One?",
                answer="A",
                result=turn_result("A").model_dump(mode="json"),
                state=state.model_dump(mode="json"),
            )
            loaded = load_conversation(
                connection, created["id"], owner_id=self.owner_id
            )

        resumed = ConversationState.model_validate(loaded["state_json"])
        self.assertEqual(resumed.previous_answer, "A")
        self.assertGreaterEqual(loaded["updated_at"], loaded["created_at"])

    def test_listing_orders_by_recent_use_and_counts_turns(self):
        first = self.create(title="Older")
        second = self.create(title="Newer")
        with database_connection(self.database_url) as connection:
            append_turn(
                connection,
                first["id"],
                owner_id=self.owner_id,
                question="One?",
                answer="A",
                result=turn_result().model_dump(mode="json"),
                state={},
            )

        with database_connection(self.database_url, readonly=True) as connection:
            listed = list_conversations(connection, owner_id=self.owner_id)

        # The one that just took a turn sorts first despite being created first.
        self.assertEqual(listed[0]["title"], "Older")
        self.assertEqual(listed[0]["turn_count"], 1)
        self.assertEqual(listed[1]["title"], "Newer")
        self.assertEqual(listed[1]["turn_count"], 0)
        del second

    def test_renaming_keeps_the_rest_of_the_conversation(self):
        created = self.create(title="Untitled")
        with database_connection(self.database_url) as connection:
            renamed = update_conversation(
                connection,
                created["id"],
                owner_id=self.owner_id,
                title="Tree ensembles",
            )

        self.assertEqual(renamed["title"], "Tree ensembles")
        self.assertEqual(list(renamed["book_ids"]), [1])

    def test_deleting_a_conversation_removes_its_turns(self):
        created = self.create()
        with database_connection(self.database_url) as connection:
            append_turn(
                connection,
                created["id"],
                owner_id=self.owner_id,
                question="One?",
                answer="A",
                result=turn_result().model_dump(mode="json"),
                state={},
            )
            self.assertTrue(
                delete_conversation(
                    connection, created["id"], owner_id=self.owner_id
                )
            )
            remaining = connection.execute(
                "select count(*) as total from conversation_turns "
                "where conversation_id = %s",
                (created["id"],),
            ).fetchone()

        self.assertEqual(remaining["total"], 0)

    def test_deleting_something_that_is_not_there_reports_it(self):
        with database_connection(self.database_url) as connection:
            removed = delete_conversation(
                connection,
                "00000000-0000-4000-8000-000000000000",
                owner_id=self.owner_id,
            )
        self.assertFalse(removed)


if __name__ == "__main__":
    unittest.main()
