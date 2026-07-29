"""Owner-scoped prompt preferences are canonical and isolated."""

import unittest

from storage.database import connection as database_connection
from storage.preferences import load_prompt_profile, save_prompt_profile
from study.prompts import DEFAULT_PROMPT_PROFILE
from tests.postgres import PostgresOwnerMixin


class PromptPreferenceStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def test_missing_preferences_use_application_defaults_upstream(self) -> None:
        with database_connection(self.database_url, readonly=True) as connection:
            stored = load_prompt_profile(connection, owner_id=self.owner_id)
        self.assertIsNone(stored)

    def test_prompt_profile_round_trips(self) -> None:
        profile = DEFAULT_PROMPT_PROFILE.model_copy(
            update={
                "interview_instructions": (
                    DEFAULT_PROMPT_PROFILE.interview_instructions
                    + "\nPrefer probability examples."
                )
            }
        )
        with database_connection(self.database_url) as connection:
            saved = save_prompt_profile(
                connection,
                owner_id=self.owner_id,
                profile=profile.model_dump(mode="json"),
            )
        with database_connection(self.database_url, readonly=True) as connection:
            loaded = load_prompt_profile(connection, owner_id=self.owner_id)

        self.assertEqual(saved, loaded)
        self.assertIn("probability examples", loaded["interview_instructions"])

    def test_saving_again_replaces_the_same_owner_row(self) -> None:
        first = DEFAULT_PROMPT_PROFILE.model_dump(mode="json")
        second = {
            **first,
            "concept_template": first["concept_template"] + "\nUse one analogy.",
        }
        with database_connection(self.database_url) as connection:
            save_prompt_profile(
                connection,
                owner_id=self.owner_id,
                profile=first,
            )
            save_prompt_profile(
                connection,
                owner_id=self.owner_id,
                profile=second,
            )
            count = connection.execute(
                "select count(*) as total from study_preferences where owner_id = %s",
                (self.owner_id,),
            ).fetchone()["total"]

        self.assertEqual(count, 1)
        with database_connection(self.database_url, readonly=True) as connection:
            loaded = load_prompt_profile(connection, owner_id=self.owner_id)
        self.assertEqual(loaded["concept_template"], second["concept_template"])


if __name__ == "__main__":
    unittest.main()
