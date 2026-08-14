"""Unit coverage for deterministic canonical-library chat requests."""

import unittest
from unittest.mock import patch

from study.analyze import analyze_turn
from study.contracts import ConversationState, TurnDecision
from study.conversation import execute_decision


class FailIfCalled:
    def invoke(self, messages, config=None):
        raise AssertionError("a deterministic library listing called a model")


class LibraryListingTests(unittest.TestCase):
    def state(self) -> ConversationState:
        return ConversationState(
            conversation_id="library-c1",
            book_ids=[3, 7],
        )

    def test_natural_library_requests_bypass_turn_analysis_model(self):
        for question in (
            "list all the papers uploaded",
            "Show me all papers in my library.",
            "What papers do I have?",
        ):
            decision = analyze_turn(
                question,
                self.state(),
                owner_id="00000000-0000-0000-0000-000000000001",
                model=FailIfCalled(),
            )

            self.assertEqual(decision.route, "library_list")
            self.assertEqual(decision.history_dependency, "independent")
            self.assertIsNone(decision.resolved_scope)

    @patch("study.conversation.list_books")
    @patch("study.conversation.database_connection")
    @patch("study.conversation.execute_query")
    def test_listing_uses_only_selected_ready_paper_metadata(
        self,
        execute_query,
        database_connection,
        list_canonical_books,
    ):
        database_connection.return_value.__enter__.return_value = object()
        list_canonical_books.return_value = [
            {
                "id": 3,
                "title": "Selected Paper",
                "author": "A. Researcher",
                "page_count": 12,
                "document_type": "paper",
            },
            {
                "id": 99,
                "title": "Unselected Paper",
                "author": None,
                "page_count": 4,
                "document_type": "paper",
            },
        ]
        decision = TurnDecision(
            route="library_list",
            history_dependency="independent",
            reason="The request asks for canonical library metadata.",
        )

        result = execute_decision(
            "list all the papers uploaded",
            decision,
            self.state(),
            database_url="postgresql://unused",
            owner_id="00000000-0000-0000-0000-000000000001",
            retrieval_mode="hybrid",
            model=FailIfCalled(),
        )

        execute_query.assert_not_called()
        list_canonical_books.assert_called_once_with(
            database_connection.return_value.__enter__.return_value,
            owner_id="00000000-0000-0000-0000-000000000001",
            document_type="paper",
        )
        self.assertEqual(result.route, "library_list")
        self.assertIn("1 ready paper is available", result.answer)
        self.assertIn("Selected Paper — A. Researcher, 12 pages", result.answer)
        self.assertNotIn("Unselected Paper", result.answer)
        self.assertEqual(result.evidence, [])
        self.assertEqual(result.citations, [])


if __name__ == "__main__":
    unittest.main()
