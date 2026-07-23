import unittest

from scripts.inspect_scope import format_inspection
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.content import load_scope_content
from study.scope import (
    AmbiguousScopeError,
    ScopeNotFoundError,
    list_chapters,
    resolve_book,
    resolve_chapter,
    resolve_section,
)
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class StudyScopeTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = self._ingest(
            title="Sample Book",
            file_hash=FILE_HASH,
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _ingest(self, *, title: str, file_hash: str) -> int:
        return ingest_book(
            self.connection,
            sample_book(),
            title=title,
            author="Test Author",
            file_hash=file_hash,
            page_count=5,
            parser_version="test-v1",
        )

    def test_resolves_chapter_number_and_loads_complete_subtree(self) -> None:
        scope = resolve_chapter(
            self.connection,
            1,
            book_id=self.book_id,
        )

        self.assertEqual(scope.kind, "chapter")
        self.assertEqual(scope.display_path, "Chapter 1")
        self.assertEqual(scope.start_page, 1)
        self.assertEqual(scope.end_page, 5)
        self.assertEqual(
            [node.title for node in scope.nodes],
            ["Chapter 1", "Core idea", "Diagram"],
        )
        self.assertEqual(
            [node.level for node in scope.nodes],
            [1, 2, 3],
        )

    def test_lists_chapters_in_toc_order_and_resolves_section(self) -> None:
        chapters = list_chapters(self.connection, book_id=self.book_id)
        self.assertEqual([chapter.title for chapter in chapters], ["Chapter 1"])

        scope = resolve_section(
            self.connection,
            "Core idea",
            book_id=self.book_id,
            chapter="Chapter 1",
        )
        self.assertEqual(scope.kind, "section")
        self.assertEqual(
            [node.title for node in scope.nodes],
            ["Core idea", "Diagram"],
        )
        self.assertEqual(scope.start_page, 2)
        self.assertEqual(scope.end_page, 5)

    def test_loads_tables_and_image_provenance_without_binary_payload(self) -> None:
        scope = resolve_chapter(
            self.connection,
            "chapter 1",
            book_id=self.book_id,
        )
        evidence = load_scope_content(self.connection, scope)
        blocks = [
            block for node_content in evidence.nodes for block in node_content.blocks
        ]

        self.assertEqual(evidence.block_count, 5)
        self.assertEqual(evidence.table_count, 1)
        self.assertEqual(evidence.image_count, 1)
        table = next(block for block in blocks if block.block_type == "table")
        image = next(block for block in blocks if block.block_type == "image")
        self.assertEqual(table.text_content, "[TABLE 0]")
        self.assertEqual(table.readable_text, "A")
        self.assertEqual(image.text_content, "[IMAGE 0]")
        self.assertIsNone(image.readable_text)
        self.assertEqual(image.image_mime_type, "image/png")
        self.assertTrue(image.has_image_payload)
        self.assertFalse(hasattr(image, "base64_content"))

    def test_formats_an_auditable_hierarchy(self) -> None:
        scope = resolve_chapter(
            self.connection,
            "1",
            book_id=self.book_id,
        )
        evidence = load_scope_content(self.connection, scope)

        output = format_inspection(scope, evidence)

        self.assertIn("Scope: chapter — Chapter 1", output)
        self.assertIn("Content blocks: 5 (1 tables, 1 images)", output)
        self.assertIn("- Chapter 1 [node", output)
        self.assertIn("  - Core idea [node", output)
        self.assertIn("    - Diagram [node", output)

    def test_requires_book_disambiguation_when_multiple_books_match(self) -> None:
        second_book_id = self._ingest(
            title="Second Sample",
            file_hash="b" * 64,
        )

        with self.assertRaises(AmbiguousScopeError) as captured:
            resolve_chapter(self.connection, 1)
        self.assertEqual(len(captured.exception.candidates), 2)

        scope = resolve_chapter(
            self.connection,
            1,
            book_id=second_book_id,
        )
        self.assertEqual(scope.book_id, second_book_id)

    def test_resolves_numbered_chapter_with_a_descriptive_title(self) -> None:
        book = sample_book()
        title = "Chapter 1. Overview of Machine Learning Systems"
        for section in book.sections:
            section.path[0] = title
        book.toc[0] = (1, title, 1)
        descriptive_book_id = ingest_book(
            self.connection,
            book,
            title="Descriptive Sample",
            author="Test Author",
            file_hash="c" * 64,
            page_count=5,
            parser_version="test-v1",
        )

        scope = resolve_chapter(
            self.connection,
            1,
            book_id=descriptive_book_id,
        )

        self.assertEqual(scope.display_path, title)

    def test_reports_missing_scope_instead_of_guessing(self) -> None:
        with self.assertRaises(ScopeNotFoundError):
            resolve_chapter(
                self.connection,
                99,
                book_id=self.book_id,
            )
        with self.assertRaises(ScopeNotFoundError):
            resolve_chapter(
                self.connection,
                "Chapter 1. Wrong title",
                book_id=self.book_id,
            )
        with self.assertRaises(ScopeNotFoundError):
            resolve_section(
                self.connection,
                "Not a real section",
                book_id=self.book_id,
            )

    def test_resolves_the_complete_book(self) -> None:
        scope = resolve_book(self.connection, book_id=self.book_id)
        evidence = load_scope_content(self.connection, scope)

        self.assertEqual(scope.kind, "book")
        self.assertIsNone(scope.root_node_id)
        self.assertEqual(len(scope.nodes), 3)
        self.assertEqual(evidence.block_count, 5)


if __name__ == "__main__":
    unittest.main()
