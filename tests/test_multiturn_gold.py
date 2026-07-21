import json
from pathlib import Path
import sqlite3
import unittest

from scripts.build_multiturn_report import build_html
from scripts.validate_multiturn_gold import validate


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "evaluation" / "multiturn_gold.json"
DATABASE_PATH = ROOT / "data" / "books.sqlite3"


@unittest.skipUnless(
    DATABASE_PATH.is_file(),
    "requires the local canonical book database",
)
class MultiturnGoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
        cls.connection = sqlite3.connect(
            DATABASE_PATH.resolve().as_uri() + "?mode=ro",
            uri=True,
        )
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

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
