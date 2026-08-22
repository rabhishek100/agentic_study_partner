"""Canonical Postgres storage lifecycle and losslessness tests."""

import unittest

from storage.database import connection as database_connection
from storage.postgres import (
    BookAlreadyExistsError,
    InvalidBookError,
    _postgres_json,
    _postgres_text,
    canonical_text,
    ingest_book,
    restore_book,
)
from study.scope import list_chapters
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class PostgresTextTests(unittest.TestCase):
    def test_nul_is_removed_from_text_and_nested_json(self) -> None:
        self.assertEqual(canonical_text("before\x00after"), "beforeafter")
        self.assertEqual(_postgres_text("before\x00after"), "beforeafter")
        self.assertEqual(
            _postgres_json({"path": ["one\x00", {"title": "two\x00three"}]}),
            {"path": ["one", {"title": "twothree"}]},
        )


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

    def test_chapters_are_stored_from_the_detected_chapter_level(self) -> None:
        """Regression: a book grouping chapters under Parts stored Part I as a
        chapter and Chapter 1 as a section, so no chapter answered to a number.

        Role naming is a property of the whole outline, not of one entry, so it
        is asserted here through what actually reaches the nodes table.
        """

        from parsing.models import ParsedBook, Section

        outline = [
            (1, "Copyright"),
            (1, "Part I. Foundations"),
            (2, "Chapter 1. Reliable Applications"),
            (3, "Thinking About Data Systems"),
            (2, "Chapter 2. Data Models"),
            (2, "Chapter 3. Storage and Retrieval"),
            (1, "Index"),
        ]
        sections = []
        active_path: list[str] = []
        for level, title in outline:
            active_path[level - 1 :] = [title]
            sections.append(
                Section(
                    path=list(active_path),
                    level=level,
                    start_page=1,
                    end_page=1,
                )
            )
        book = ParsedBook(
            source="sources/books/parts.pdf",
            toc=[(level, title, 1) for level, title in outline],
            sections=sections,
        )
        book_id = self.ingest(book, page_count=1, file_hash="b" * 64)

        rows = self.database.execute(
            """
            select title, node_type from nodes
            where owner_id = %s and book_id = %s order by toc_index
            """,
            (self.owner_id, book_id),
        ).fetchall()
        self.assertEqual(
            [(row["title"], row["node_type"]) for row in rows],
            [
                ("Copyright", "front_matter"),
                ("Part I. Foundations", "part"),
                ("Chapter 1. Reliable Applications", "chapter"),
                ("Thinking About Data Systems", "section"),
                ("Chapter 2. Data Models", "chapter"),
                ("Chapter 3. Storage and Retrieval", "chapter"),
                ("Index", "back_matter"),
            ],
        )

    def test_contradictory_chapter_numbering_is_refused(self) -> None:
        """A duplicate chapter number would resolve a reference to the wrong
        pages, which is worse than refusing the book.

        The classifier cannot produce this today - the run it types is
        consecutive by construction - so the roles are supplied directly, which
        is what a regression in the classifier would look like from here.
        """

        from parsing.models import Section
        from storage.postgres import _validate_chapters

        titles = [
            "Chapter 1. One",
            "Chapter 2. Two",
            "Chapter 3. Three",
            "Chapter 3. Three Again",
        ]
        sections = [
            Section(path=[title], level=1, start_page=1, end_page=1) for title in titles
        ]
        with self.assertRaises(InvalidBookError) as caught:
            _validate_chapters(sections, ["chapter"] * 4)
        self.assertIn("not unique", str(caught.exception))

        gapped = [
            Section(path=[title], level=1, start_page=1, end_page=1)
            for title in [
                "Chapter 1. One",
                "Chapter 2. Two",
                "Chapter 3. Three",
                "Chapter 9. Nine",
            ]
        ]
        with self.assertRaises(InvalidBookError) as caught:
            _validate_chapters(gapped, ["chapter"] * 4)
        self.assertIn("not consecutive", str(caught.exception))

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

    def test_paper_outline_is_stored_as_sections_without_chapters(self) -> None:
        paper_id = self.ingest(
            document_type="paper",
            title="Sample Paper",
            file_hash="e" * 64,
        )

        rows = self.database.execute(
            """
            select title, node_type from nodes
            where owner_id = %s and book_id = %s order by toc_index
            """,
            (self.owner_id, paper_id),
        ).fetchall()

        self.assertEqual(
            [(row["title"], row["node_type"]) for row in rows],
            [
                ("Chapter 1", "section"),
                ("Core idea", "subsection"),
                ("Diagram", "nested_section"),
            ],
        )
        self.assertEqual(
            list_chapters(
                self.database,
                owner_id=self.owner_id,
                book_id=paper_id,
            ),
            (),
        )

    def test_document_type_is_validated_before_storage(self) -> None:
        with self.assertRaisesRegex(ValueError, "document_type"):
            self.ingest(document_type="article")

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
