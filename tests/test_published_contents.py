"""The published chapter lists are usable, and say where they came from.

The comparison itself needs a live library, so it runs from
`scripts/evaluate_outlines.py` against a database. What CI can check is that
the reference data stays well-formed: a corpus file that quietly loses its
sources, or gains an entry nothing can match, turns the one check that caught a
real outline error into one that silently passes.
"""

import json
import unittest
from pathlib import Path

from scripts.evaluate_outlines import DEFAULT_CONTENTS, _matches


class PublishedContentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(Path(DEFAULT_CONTENTS).read_text())

    def test_every_book_records_where_its_chapter_list_came_from(self) -> None:
        """An unsourced list is an assertion, not a reference."""

        for book in self.data["books"]:
            with self.subTest(book=book["title"]):
                self.assertTrue(book["source"].startswith("http"))
                self.assertTrue(book["chapters"])
                self.assertTrue(book["match_on"])

    def test_chapters_are_distinct_within_a_book(self) -> None:
        """A duplicated entry would make recall unreachable."""

        for book in self.data["books"]:
            with self.subTest(book=book["title"]):
                self.assertEqual(
                    len(book["chapters"]), len(set(book["chapters"]))
                )

    def test_no_two_chapters_of_a_book_match_each_other(self) -> None:
        """Titles close enough to match would let one extraction satisfy two."""

        for book in self.data["books"]:
            chapters = book["chapters"]
            for index, first in enumerate(chapters):
                for second in chapters[index + 1 :]:
                    with self.subTest(book=book["title"], pair=(first, second)):
                        self.assertFalse(_matches(first, second))

    def test_the_match_key_is_specific_to_one_book(self) -> None:
        keys = [book["match_on"].casefold() for book in self.data["books"]]
        for index, key in enumerate(keys):
            for other in keys[index + 1 :]:
                self.assertNotIn(key, other)
                self.assertNotIn(other, key)


if __name__ == "__main__":
    unittest.main()
