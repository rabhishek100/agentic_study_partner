"""Canonical Postgres storage lifecycle and losslessness tests."""

import unittest

from storage.database import connection as database_connection
from storage.postgres import (
    BookAlreadyExistsError,
    InvalidBookError,
    ingest_book,
    restore_book,
)
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class PostgresStorageTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.database = self.database_context.__enter__()

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def ingest(self, book=None, **overrides) -> int:
        arguments = {
            "owner_id": self.owner_id,
            "title": "Sample",
            "author": "Test Author",
            "file_hash": FILE_HASH,
            "page_count": 5,
            "parser_version": "test-v1",
            "metadata": {"edition": 1},
        }
        arguments.update(overrides)
        return ingest_book(self.database, book or sample_book(), **arguments)

    def test_a_top_level_entry_is_a_chapter_whatever_it_is_called(self) -> None:
        """Regression: chapters numbered without the word were lost.

        `_node_type` read the role off the title, so "Chapter 1 Introduction"
        was a chapter and "1 Introduction" was `other`. Scope search excludes
        `other`, so a 613-page book whose outline numbered its chapters
        without the word had all thirteen of them invisible to the question
        "what sections are present in Chapter 1?".
        """

        from parsing.models import Section
        from storage.postgres import _node_type

        def top_level(title):
            return Section(path=[title], level=1, start_page=1, end_page=2)

        for title in ("Chapter 1 Introduction", "1 Introduction", "Preface"):
            with self.subTest(title=title):
                self.assertEqual(_node_type(top_level(title)), "chapter")

        self.assertEqual(_node_type(top_level("Appendix A Data")), "appendix")

    def test_depth_names_the_lower_levels(self) -> None:
        from parsing.models import Section
        from storage.postgres import _node_type

        for level, expected in ((2, "section"), (3, "subsection"), (4, "nested_section")):
            with self.subTest(level=level):
                section = Section(
                    path=["Chapter"] * level, level=level, start_page=1, end_page=2
                )
                self.assertEqual(_node_type(section), expected)

    def test_lossless_round_trip_and_hierarchy(self) -> None:
        original = sample_book()
        book_id = self.ingest(original)

        restored = restore_book(self.database, book_id, owner_id=self.owner_id)
        self.assertEqual(original.model_dump(), restored.model_dump())

        nodes = self.database.execute(
            """
            select child.toc_index, child.node_type,
                   parent.toc_index as parent_index
            from nodes as child
            left join nodes as parent
              on parent.id = child.parent_id
             and parent.owner_id = child.owner_id
            where child.owner_id = %s and child.book_id = %s
            order by child.toc_index
            """,
            (self.owner_id, book_id),
        ).fetchall()
        self.assertEqual(
            [
                (row["toc_index"], row["node_type"], row["parent_index"])
                for row in nodes
            ],
            [
                (0, "chapter", None),
                (1, "section", 0),
                (2, "subsection", 1),
            ],
        )

        block_types = [
            row["block_type"]
            for row in self.database.execute(
                """
                select block_type from content_blocks
                where owner_id = %s and book_id = %s
                order by id
                """,
                (self.owner_id, book_id),
            )
        ]
        self.assertEqual(
            block_types,
            ["text", "text", "table", "text", "image"],
        )
        self.assertEqual(
            self.database.execute(
                """
                select count(*) as count from table_blocks
                where owner_id = %s and book_id = %s
                """,
                (self.owner_id, book_id),
            ).fetchone()["count"],
            1,
        )
        self.assertEqual(
            self.database.execute(
                """
                select count(*) as count from image_blocks
                where owner_id = %s and book_id = %s
                """,
                (self.owner_id, book_id),
            ).fetchone()["count"],
            1,
        )

    def test_duplicate_requires_explicit_replace(self) -> None:
        self.ingest()
        with self.assertRaises(BookAlreadyExistsError):
            self.ingest(title="Changed")

        stored_title = self.database.execute(
            "select title from books where owner_id = %s",
            (self.owner_id,),
        ).fetchone()["title"]
        self.assertEqual(stored_title, "Sample")

        self.ingest(title="Changed", replace=True)
        rows = self.database.execute(
            "select title from books where owner_id = %s",
            (self.owner_id,),
        ).fetchall()
        self.assertEqual([row["title"] for row in rows], ["Changed"])

    def test_invalid_placeholder_rolls_back_everything(self) -> None:
        book = sample_book()
        book.sections[1].texts[1].text = "[TABLE 9]"

        with self.assertRaises(InvalidBookError):
            self.ingest(book)

        count = self.database.execute(
            "select count(*) as count from books where owner_id = %s",
            (self.owner_id,),
        ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_cascade_delete_removes_canonical_content(self) -> None:
        book_id = self.ingest()
        self.database.execute(
            "delete from books where owner_id = %s and id = %s",
            (self.owner_id, book_id),
        )

        for table in ("nodes", "content_blocks", "table_blocks", "image_blocks"):
            with self.subTest(table=table):
                count = self.database.execute(
                    f"""
                    select count(*) as count from {table}
                    where owner_id = %s and book_id = %s
                    """,
                    (self.owner_id, book_id),
                ).fetchone()["count"]
                self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
