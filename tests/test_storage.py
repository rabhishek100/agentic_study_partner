import unittest

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from storage.sqlite import (
    BookAlreadyExistsError,
    InvalidBookError,
    connect,
    ingest_book,
    initialize,
    restore_book,
)


FILE_HASH = "a" * 64


def sample_book() -> ParsedBook:
    sections = [
        Section(
            path=["Chapter 1"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(text="Chapter introduction", category="NarrativeText", page=1)
            ],
        ),
        Section(
            path=["Chapter 1", "Core idea"],
            level=2,
            start_page=2,
            end_page=2,
            texts=[
                TextBlock(text="Before table", category="NarrativeText", page=2),
                TextBlock(text="[TABLE 0]", category="TablePlaceholder", page=2),
                TextBlock(text="After table", category="NarrativeText", page=2),
            ],
            tables=[
                TableBlock(
                    html="<table><tr><td>A</td></tr></table>",
                    text="A",
                    page=2,
                )
            ],
        ),
        Section(
            path=["Chapter 1", "Core idea", "Diagram"],
            level=3,
            start_page=3,
            end_page=5,
            texts=[TextBlock(text="[IMAGE 0]", category="ImagePlaceholder", page=3)],
            images=[ImageBlock(base64="aW1hZ2U=", mime="image/png", page=3)],
        ),
    ]
    return ParsedBook(
        source="sources/books/sample.pdf",
        toc=[
            (1, "Chapter 1", 1),
            (2, "Core idea", 2),
            (3, "Diagram", 3),
        ],
        sections=sections,
    )


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def ingest(self, book: ParsedBook | None = None, **overrides) -> int:
        arguments = {
            "title": "Sample",
            "author": "Test Author",
            "file_hash": FILE_HASH,
            "page_count": 5,
            "parser_version": "test-v1",
            "metadata": {"edition": 1},
        }
        arguments.update(overrides)
        return ingest_book(self.connection, book or sample_book(), **arguments)

    def test_lossless_round_trip_and_hierarchy(self) -> None:
        original = sample_book()
        book_id = self.ingest(original)

        restored = restore_book(self.connection, book_id)
        self.assertEqual(original.model_dump(), restored.model_dump())

        nodes = self.connection.execute(
            """
            SELECT child.toc_index, child.node_type, parent.toc_index AS parent_index
            FROM nodes AS child
            LEFT JOIN nodes AS parent ON parent.id = child.parent_id
            ORDER BY child.toc_index
            """
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
            for row in self.connection.execute(
                "SELECT block_type FROM content_blocks ORDER BY id"
            )
        ]
        self.assertEqual(
            block_types,
            ["text", "text", "table", "text", "image"],
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM table_blocks").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM image_blocks").fetchone()[0],
            1,
        )

    def test_duplicate_requires_explicit_replace(self) -> None:
        self.ingest()
        with self.assertRaises(BookAlreadyExistsError):
            self.ingest(title="Changed")

        stored_title = self.connection.execute("SELECT title FROM books").fetchone()[0]
        self.assertEqual(stored_title, "Sample")

        self.ingest(title="Changed", replace=True)
        rows = self.connection.execute("SELECT title FROM books").fetchall()
        self.assertEqual([row["title"] for row in rows], ["Changed"])

    def test_invalid_placeholder_rolls_back_everything(self) -> None:
        book = sample_book()
        book.sections[1].texts[1].text = "[TABLE 9]"

        with self.assertRaises(InvalidBookError):
            self.ingest(book)

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            0,
        )

    def test_cascade_delete_removes_canonical_content(self) -> None:
        book_id = self.ingest()
        self.connection.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self.connection.commit()

        for table in ("nodes", "content_blocks", "table_blocks", "image_blocks"):
            with self.subTest(table=table):
                count = self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
