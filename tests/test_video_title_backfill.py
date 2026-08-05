"""Naming the conversations created before the first turn learned to name them."""

import json
import unittest
from uuid import UUID, uuid4

from scripts.backfill_video_conversation_titles import main
from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.conversation_store import PLACEHOLDER_TITLE


class VideoTitleBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@title-backfill.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s", (self.owner,)
            )

    def _conversation(self, title: str, question: str | None) -> UUID:
        with connection(self.database_url) as database:
            row = database.execute(
                """
                insert into video.conversations (
                    owner_id, video_id, title, state_json
                ) values (%s, %s, %s, '{}'::jsonb)
                returning id
                """,
                (self.owner, self.video.video_id, title),
            ).fetchone()
            if question:
                database.execute(
                    """
                    insert into video.conversation_turns (
                        owner_id, conversation_id, video_id,
                        ingestion_version_id, turn_index, status, question,
                        rewritten_query, answer, result_json, actual_cost_usd,
                        completed_at
                    ) values (
                        %s, %s, %s, %s, 0, 'complete', %s, %s, 'an answer',
                        %s, 0, now()
                    )
                    """,
                    (
                        self.owner,
                        row["id"],
                        self.video.video_id,
                        self.video.version_id,
                        question,
                        question.strip(),
                        json.dumps({"outcome": "answer"}),
                    ),
                )
            return row["id"]

    def _titles(self, *ids: UUID) -> list[str]:
        with connection(self.database_url, readonly=True) as database:
            rows = database.execute(
                "select id, title from video.conversations where id = any(%s)",
                (list(ids),),
            ).fetchall()
            by_id = {row["id"]: row["title"] for row in rows}
        return [by_id[identifier] for identifier in ids]

    def _run(self, *arguments: str) -> None:
        import sys

        original = sys.argv
        sys.argv = ["backfill", *arguments]
        try:
            main()
        finally:
            sys.argv = original

    def test_a_dry_run_changes_nothing(self) -> None:
        placeholder = self._conversation(PLACEHOLDER_TITLE, "What is attention?")

        self._run()

        self.assertEqual(self._titles(placeholder), [PLACEHOLDER_TITLE])

    def test_a_placeholder_is_named_after_its_first_question(self) -> None:
        placeholder = self._conversation(
            PLACEHOLDER_TITLE, "  What   does the diagram show? "
        )

        self._run("--apply")

        # Normalized the way a title created today would be.
        self.assertEqual(
            self._titles(placeholder), ["What does the diagram show?"]
        )

    def test_a_later_turn_never_supplies_the_name(self) -> None:
        placeholder = self._conversation(PLACEHOLDER_TITLE, "First question?")
        with connection(self.database_url) as database:
            database.execute(
                """
                insert into video.conversation_turns (
                    owner_id, conversation_id, video_id, ingestion_version_id,
                    turn_index, status, question, rewritten_query, answer,
                    result_json, actual_cost_usd, completed_at
                ) values (
                    %s, %s, %s, %s, 1, 'complete', 'Second question?',
                    'Second question?', 'a', %s, 0, now()
                )
                """,
                (
                    self.owner,
                    placeholder,
                    self.video.video_id,
                    self.video.version_id,
                    json.dumps({"outcome": "answer"}),
                ),
            )

        self._run("--apply")

        self.assertEqual(self._titles(placeholder), ["First question?"])

    def test_a_name_the_reader_chose_is_never_overwritten(self) -> None:
        named = self._conversation("Attention thread", "What is attention?")

        self._run("--apply")

        self.assertEqual(self._titles(named), ["Attention thread"])

    def test_a_conversation_with_no_turns_is_left_alone(self) -> None:
        empty = self._conversation(PLACEHOLDER_TITLE, None)

        self._run("--apply")

        # Nothing to name it after, and deleting a reader's conversation to
        # tidy a list is not this script's decision.
        self.assertEqual(self._titles(empty), [PLACEHOLDER_TITLE])

    def test_running_it_twice_is_a_no_op(self) -> None:
        placeholder = self._conversation(PLACEHOLDER_TITLE, "What is attention?")

        self._run("--apply")
        self._run("--apply")

        self.assertEqual(self._titles(placeholder), ["What is attention?"])


if __name__ == "__main__":
    unittest.main()
