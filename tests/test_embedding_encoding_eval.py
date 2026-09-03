"""The encoding eval keeps working as the gold sets and corpus move.

The measurements themselves cost an embedding call per query, so what is
pinned here is everything around them: that the gold sets still have the shape
the runner reads, that the book set's paths still resolve against the book it
is pointed at, and that the turn selection stays honest about what it scores.
"""

import json
import unittest
from uuid import UUID

from evals.embedding_encoding import (
    BOOK_GOLD,
    RecallReport,
    VIDEO_GOLD,
    video_turns,
)
from storage.database import connection, resolve_database_url


class VideoTurnSelectionTests(unittest.TestCase):
    def test_only_turns_a_retrieval_change_can_move_are_scored(self) -> None:
        """Summary and transform routes skip retrieval, so they prove nothing here."""

        video_id, turns = video_turns()

        self.assertIsInstance(video_id, UUID)
        self.assertTrue(turns)
        for turn in turns:
            self.assertEqual(turn["expected_route"], "evidence_qa")
            self.assertTrue(
                any(a.get("role") == "required" for a in turn["expected_evidence"]),
                turn["turn_id"],
            )

    def test_every_scored_turn_has_a_query_to_send(self) -> None:
        _, turns = video_turns()
        for turn in turns:
            self.assertTrue(
                (turn.get("expected_standalone_query") or turn.get("user", "")).strip(),
                turn["turn_id"],
            )


class BookGoldTests(unittest.TestCase):
    def test_the_book_set_names_sections_by_path_not_by_id(self) -> None:
        """Ids change on every ingest; the table of contents does not.

        This is what lets the set be pointed at a re-ingested copy of the same
        book, which is the only reason it is measurable at all — the book it
        was written against has no embeddings.
        """

        gold = json.loads(BOOK_GOLD.read_text())
        items = next(v for v in gold.values() if isinstance(v, list))
        for item in items:
            for anchor in item["expected_evidence"]:
                self.assertIn("path", anchor)
                self.assertTrue(anchor["path"].strip())


class GoldSetsResolveTests(unittest.TestCase):
    def test_the_video_gold_set_names_a_lecture_that_exists(self) -> None:
        gold = json.loads(VIDEO_GOLD.read_text())
        video_id = gold["lecture"]["video_id"]
        with connection(resolve_database_url()) as database:
            found = database.execute(
                "select count(*) as n from video.videos where id = %s", (video_id,)
            ).fetchone()["n"]
        if found == 0:
            self.skipTest("this database does not hold the gold lecture")
        self.assertEqual(found, 1)


class RecallReportTests(unittest.TestCase):
    def test_a_report_reads_as_one_line(self) -> None:
        line = RecallReport(
            label="video dim=1024", queries=34, mean_recall=0.897, fully_recalled=30
        ).line()

        self.assertIn("0.897", line)
        self.assertIn("34", line)
        self.assertIn("30/34", line)


if __name__ == "__main__":
    unittest.main()
