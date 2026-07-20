import re
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

from app import respond
from storage.sqlite import connect, ingest_book, initialize
from study.query import answer_query
from tests.test_storage import FILE_HASH, sample_book


class CitationSummaryModel:
    """Return a minimal summary citing every evidence-bearing node."""

    def invoke(self, messages):
        markers = re.findall(r"\[N(\d+):P(\d+):B\d+]", messages[-1][1])
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id, page in dict.fromkeys(markers)
        )
        return SimpleNamespace(
            content=f"# Chapter 1\n\nComplete grounded summary. {citations}"
        )


class StaticAnswerModel:
    def invoke(self, messages):
        del messages
        return SimpleNamespace(content="A grounded retrieval answer. [S1]")


class QueryRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.source_path = (
            f"{self.temporary_directory.name}/books.sqlite3"
        )
        with connect(self.source_path) as connection:
            initialize(connection)
            self.book_id = ingest_book(
                connection,
                sample_book(),
                title="Sample Book",
                author="Test Author",
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_section_listing_uses_canonical_hierarchy_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            answer = answer_query(
                "What sections are present in Chapter 1?",
                source_path=self.source_path,
                book_id=self.book_id,
            )

        retriever.assert_not_called()
        self.assertIn("# Chapter 1", answer)
        self.assertIn("- Core idea", answer)
        self.assertIn("PDF pp. 2", answer)

    def test_chapter_summary_uses_complete_scope_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            answer = answer_query(
                "Summarize Chapter 1",
                source_path=self.source_path,
                book_id=self.book_id,
                model=CitationSummaryModel(),
            )

        retriever.assert_not_called()
        self.assertIn("Scope route: complete chapter subtree", answer)
        self.assertIn("Complete grounded summary", answer)
        self.assertIn("## References", answer)
        self.assertIn("Sample Book → Chapter 1", answer)

    def test_section_summary_maps_within_chapter_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            answer = answer_query(
                "Summarize section Core idea in Chapter 1",
                source_path=self.source_path,
                book_id=self.book_id,
                model=CitationSummaryModel(),
            )

        retriever.assert_not_called()
        self.assertIn("Scope route: complete section subtree", answer)
        self.assertIn("Chapter 1 → Core idea", answer)
        self.assertIn("Chapter 1 → Core idea → Diagram", answer)

    def test_unmatched_named_summary_falls_back_to_retrieval(self):
        document = SimpleNamespace(
            page_content="Reservoir sampling keeps a uniform stream sample.",
            metadata={
                "book_id": self.book_id,
                "path": "Chapter 1 :: Core idea",
                "start_page": 2,
                "end_page": 2,
            },
        )
        with patch("study.query.BookRetriever") as retriever:
            retriever.return_value.invoke.return_value = [document]
            answer = answer_query(
                "Summarize reservoir sampling",
                source_path=self.source_path,
                book_id=self.book_id,
                model=StaticAnswerModel(),
            )

        retriever.assert_called_once()
        self.assertIn("A grounded retrieval answer. [S1]", answer)
        self.assertIn("_Retrieval: hybrid_", answer)
        self.assertIn("Sample Book → Chapter 1 → Core idea", answer)

    def test_gradio_handler_uses_the_same_router_and_book_filter(self):
        with patch("app.answer_query", return_value="routed") as routed:
            answer = respond("Summarize Chapter 1", [], "hybrid", 1.0)

        self.assertEqual(answer, "routed")
        routed.assert_called_once_with(
            "Summarize Chapter 1",
            retrieval_mode="hybrid",
            book_id=1,
        )


if __name__ == "__main__":
    unittest.main()
