import re
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.query import answer_query, execute_query
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class CitationSummaryModel:
    """Return a minimal summary citing every evidence-bearing node."""

    def invoke(self, messages):
        markers = re.findall(r"\[N(\d+):P(\d+):B\d+]", messages[-1][1])
        citations = " ".join(
            f"[N{node_id}:P{page}]" for node_id, page in dict.fromkeys(markers)
        )
        return SimpleNamespace(
            content=f"# Chapter 1\n\nComplete grounded summary. {citations}"
        )


class RepairingCitationSummaryModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content="# Chapter 1\n\nBad citation. [N999:P999]",
                response_metadata={"finish_reason": "stop"},
            )
        markers = re.findall(r"\[N(\d+):P(\d+):B\d+]", messages[-1][1])
        citations = " ".join(
            f"[N{node_id}:P{page}]" for node_id, page in dict.fromkeys(markers)
        )
        return SimpleNamespace(
            content=f"# Chapter 1\n\nRepaired summary. {citations}",
            response_metadata={"finish_reason": "stop"},
        )


class StaticAnswerModel:
    def invoke(self, messages):
        del messages
        return SimpleNamespace(content="A grounded retrieval answer. [S1]")


class InsufficientAnswerModel:
    def invoke(self, messages):
        del messages
        return SimpleNamespace(
            content=(
                "The evidence is insufficient because the retrieved section "
                "discusses model compression, not LoRA configuration."
            )
        )


class QueryRoutingTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
            self.book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Sample Book",
                author="Test Author",
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def test_section_listing_uses_canonical_hierarchy_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            answer = answer_query(
                "What sections are present in Chapter 1?",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
            )

        retriever.assert_not_called()
        self.assertIn("# Chapter 1", answer)
        self.assertIn("- Core idea", answer)
        self.assertIn("PDF pp. 2", answer)

    def test_chapter_listing_uses_the_book_toc_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            result = execute_query(
                "what chapters does this book have",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
            )

        retriever.assert_not_called()
        self.assertEqual(result.route, "hierarchy_list")
        self.assertEqual(result.resolved_scope.kind, "book")
        self.assertIn("# Sample Book", result.answer)
        self.assertIn("Chapters:", result.answer)
        self.assertIn("- Chapter 1", result.answer)
        self.assertIn("PDF pp. 1–5", result.answer)
        self.assertEqual(len(result.outline_node_ids), 1)

    def test_structured_interface_exposes_hierarchy_decision(self):
        with patch("study.query.BookRetriever") as retriever:
            result = execute_query(
                "What sections are present in Chapter 1?",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
            )

        retriever.assert_not_called()
        self.assertEqual(result.route, "hierarchy_list")
        self.assertEqual(result.outcome, "answer")
        self.assertEqual(result.resolved_scope.kind, "chapter")
        self.assertEqual(result.resolved_scope.display_path, "Chapter 1")
        self.assertTrue(result.outline_node_ids)

    def test_chapter_summary_uses_complete_scope_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            answer = answer_query(
                "Summarize Chapter 1",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
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
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=CitationSummaryModel(),
            )

        retriever.assert_not_called()
        self.assertIn("Scope route: complete section subtree", answer)
        self.assertIn("Chapter 1 → Core idea", answer)
        self.assertIn("Chapter 1 → Core idea → Diagram", answer)

    def test_invalid_summary_is_regenerated_once_with_validation_feedback(self):
        model = RepairingCitationSummaryModel()

        answer = answer_query(
            "Summarize Chapter 1",
            database_url=self.database_url,
            book_id=self.book_id,
            owner_id=self.owner_id,
            model=model,
        )

        self.assertEqual(model.calls, 2)
        self.assertIn("Validation repair", answer)
        self.assertIn("Repaired summary", answer)
        self.assertIn("## References", answer)

    def test_summary_stream_exposes_only_the_validated_repaired_answer(self):
        model = RepairingCitationSummaryModel()
        events = []

        result = execute_query(
            "Summarize Chapter 1",
            database_url=self.database_url,
            book_id=self.book_id,
            owner_id=self.owner_id,
            model=model,
            token_callback=lambda kind, text: events.append((kind, text)),
        )

        self.assertEqual(model.calls, 2)
        self.assertEqual(events, [("token", result.answer)])
        self.assertNotIn("Bad citation", events[0][1])
        self.assertNotIn("restart", [kind for kind, _ in events])

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
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=StaticAnswerModel(),
            )

        retriever.assert_called_once()
        self.assertIn("A grounded retrieval answer. [S1]", answer)
        self.assertIn("_Retrieval: hybrid_", answer)
        self.assertIn("Sample Book → Chapter 1 → Core idea", answer)

    def test_model_can_mark_retrieved_evidence_insufficient(self):
        document = SimpleNamespace(
            page_content="Low-rank factorization can compress model tensors.",
            metadata={
                "book_id": self.book_id,
                "node_id": 1,
                "path": "Chapter 1 :: Core idea",
                "start_page": 2,
                "end_page": 2,
            },
        )
        with patch("study.query.BookRetriever") as retriever:
            retriever.return_value.invoke.return_value = [document]
            result = execute_query(
                "How should I choose LoRA target modules?",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=InsufficientAnswerModel(),
            )

        self.assertEqual(result.route, "retrieval_qa")
        self.assertEqual(result.outcome, "abstain")
        self.assertIn("evidence is insufficient", result.answer)
        self.assertNotIn("INSUFFICIENT_EVIDENCE", result.answer)

    def test_forced_retrieval_does_not_reparse_query_as_hierarchy(self):
        document = SimpleNamespace(
            page_content="The chapter has a core idea section.",
            metadata={
                "book_id": self.book_id,
                "node_id": 1,
                "path": "Chapter 1 :: Core idea",
                "start_page": 2,
                "end_page": 2,
            },
        )
        with patch("study.query.BookRetriever") as retriever:
            retriever.return_value.invoke.return_value = [document]
            result = execute_query(
                "What sections are present in Chapter 1?",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=StaticAnswerModel(),
                force_retrieval=True,
            )

        retriever.assert_called_once()
        self.assertEqual(result.route, "retrieval_qa")


if __name__ == "__main__":
    unittest.main()
