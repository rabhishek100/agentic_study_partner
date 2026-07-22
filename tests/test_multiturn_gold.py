import json
from pathlib import Path
import unittest

from scripts.build_multiturn_report import build_html
from scripts.validate_multiturn_gold import validate
from storage.database import connection as database_connection


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "evaluation" / "multiturn_gold.json"


class MultiturnGoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
        cls.database_context = database_connection(readonly=True)
        cls.connection = cls.database_context.__enter__()
        book_id = cls.gold["book"]["database_book_id"]
        if (
            cls.connection.execute(
                "select 1 from books where id = %s",
                (book_id,),
            ).fetchone()
            is None
        ):
            cls.database_context.__exit__(None, None, None)
            raise unittest.SkipTest("requires the locally backfilled canonical book")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database_context.__exit__(None, None, None)

    def test_frozen_dataset_passes_canonical_validation(self):
        result = validate(self.gold, self.connection, allow_pending=False)

        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["conversation_count"], 11)
        self.assertEqual(result["turn_count"], 44)
        self.assertEqual(result["unanswerable_count"], 5)

    def test_report_contains_inspection_controls_and_all_turns(self):
        validation = validate(
            self.gold,
            self.connection,
            allow_pending=False,
        )

        rendered = build_html(self.gold, validation, self.connection)

        self.assertIn("Model-adjudicated, not human-verified", rendered)
        self.assertIn('id="search"', rendered)
        self.assertIn('id="route"', rendered)
        self.assertIn("Expected evidence", rendered)
        for conversation in self.gold["conversations"]:
            self.assertIn(f'id="{conversation["id"]}"', rendered)
            for turn in conversation["turns"]:
                self.assertIn(f'id="{turn["turn_id"]}"', rendered)


if __name__ == "__main__":
    unittest.main()
