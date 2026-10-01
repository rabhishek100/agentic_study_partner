import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.contracts import ConversationMessage, ConversationState
from study.query import answer_query, execute_query
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class CitationSummaryModel:
    """Return a minimal summary citing every evidence-bearing node."""

    def invoke(self, messages):
        self.messages = messages
        evidence = messages[-1][1].split("Book evidence:\n", 1)[1]
        markers = re.findall(r"\[N(\d+):P(\d+)]", evidence)
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
        evidence = messages[-1][1].split("Book evidence:\n", 1)[1]
        markers = re.findall(r"\[N(\d+):P(\d+)]", evidence)
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


class ProviderCitationAnswerModel:
    def invoke(self, messages):
        del messages
        return SimpleNamespace(
            content="A grounded retrieval answer. \ue200cite\ue202S1\ue201"
        )


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
                "What are the sections under Chapter 1?",
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
            result = execute_query(
                "Summarize Chapter 1",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=CitationSummaryModel(),
            )

        retriever.assert_not_called()
        self.assertIn("Complete grounded summary", result.answer)
        self.assertEqual(result.resolved_scope.kind, "chapter")
        self.assertEqual(result.resolved_scope.display_path, "Chapter 1")
        self.assertTrue(
            any(reference.path.startswith("Chapter 1") for reference in result.evidence)
        )
        self.assertTrue(
            all(
                reference.book_title == "Sample Book"
                and reference.book_id == self.book_id
                for reference in result.evidence
            )
        )

    def test_explain_pdf_loads_a_papers_complete_scope_without_retrieval(self):
        with database_connection(self.database_url) as connection:
            paper_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Sample Paper",
                author="Test Author",
                file_hash="f" * 64,
                page_count=5,
                parser_version="test-v1",
                document_type="paper",
            )

        model = CitationSummaryModel()
        with patch("study.query.BookRetriever") as retriever:
            result = execute_query(
                "Explain this PDF",
                database_url=self.database_url,
                book_id=paper_id,
                owner_id=self.owner_id,
                model=model,
            )

        retriever.assert_not_called()
        self.assertEqual(result.route, "hierarchy_summary")
        self.assertEqual(result.resolved_scope.kind, "book")
        self.assertEqual(result.resolved_scope.display_path, "Sample Paper")
        self.assertEqual(len(result.evidence), 3)
        self.assertIn("Paper: Sample Paper", model.messages[-1][1])
        self.assertIn(
            "Question:\nExplain the complete paper Sample Paper.",
            model.messages[-1][1],
        )

    def test_summary_answer_carries_no_rendered_scope_or_reference_block(self):
        """Presentation belongs to the interface, not to the answer string.

        Scope, references, and retrieval mode are all first-class fields on
        the result; concatenating them into markdown forced the client to
        parse prose to recover data the server already had.
        """

        with patch("study.query.BookRetriever"):
            result = execute_query(
                "Summarize Chapter 1",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=CitationSummaryModel(),
            )

        self.assertNotIn("## References", result.answer)
        self.assertNotIn("Scope route", result.answer)
        self.assertNotIn("_Retrieval:", result.answer)
        # The inline grounding markers are the contract and must survive.
        self.assertRegex(result.answer, r"\[N\d+:P\d+]")
        self.assertTrue(result.citations)

    def test_section_summary_maps_within_chapter_without_retrieval(self):
        with patch("study.query.BookRetriever") as retriever:
            result = execute_query(
                "Summarize section Core idea in Chapter 1",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=CitationSummaryModel(),
            )

        retriever.assert_not_called()
        self.assertEqual(result.resolved_scope.kind, "section")
        paths = {reference.path for reference in result.evidence}
        self.assertIn("Chapter 1 :: Core idea", paths)
        self.assertIn("Chapter 1 :: Core idea :: Diagram", paths)

    def test_invalid_summary_is_regenerated_once_with_validation_feedback(self):
        model = RepairingCitationSummaryModel()

        result = execute_query(
            "Summarize Chapter 1",
            database_url=self.database_url,
            book_id=self.book_id,
            owner_id=self.owner_id,
            model=model,
        )

        self.assertEqual(model.calls, 2)
        self.assertIn("Repaired summary", result.answer)
        self.assertNotIn("Bad citation", result.answer)
        # The repair is reported as a warning rather than pasted into the
        # answer, so the interface can present it as metadata.
        self.assertTrue(
            any("automatically repaired" in warning for warning in result.warnings),
            result.warnings,
        )

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
            result = execute_query(
                "Summarize reservoir sampling",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=StaticAnswerModel(),
            )

        retriever.assert_called_once()
        self.assertIn("A grounded retrieval answer. [S1]", result.answer)
        self.assertNotIn("### Sources", result.answer)
        self.assertNotIn("_Retrieval:", result.answer)
        self.assertEqual(result.retrieval_mode, "hybrid")
        self.assertEqual(
            [(reference.book_title, reference.path) for reference in result.evidence],
            [("Sample Book", "Chapter 1 :: Core idea")],
        )

    def test_retrieval_normalizes_provider_citation_syntax(self):
        document = SimpleNamespace(
            page_content="Reservoir sampling keeps a uniform stream sample.",
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
                "Summarize reservoir sampling",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=ProviderCitationAnswerModel(),
            )

        self.assertIn("A grounded retrieval answer. [S1]", result.answer)
        self.assertEqual([citation.marker for citation in result.citations], ["[S1]"])

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
                allow_external_fallback=False,
            )

        self.assertEqual(result.route, "retrieval_qa")
        self.assertEqual(result.outcome, "abstain")
        self.assertIn("evidence is insufficient", result.answer)
        self.assertNotIn("INSUFFICIENT_EVIDENCE", result.answer)

    def test_contracted_refusal_is_not_published_as_an_answer(self):
        document = SimpleNamespace(page_content="Low-rank factorization compresses tensors.",
            metadata={"book_id": self.book_id, "node_id": 1, "path": "Core idea",
                      "start_page": 2, "end_page": 2})
        for text, outcome in (
            ("There isn't enough evidence here to prescribe LoRA rank. [S1]", "abstain"),
            ("There isn’t enough evidence here to choose target modules. [S1]", "abstain"),
            ("There is not enough evidence for that recommendation. [S1]", "abstain"),
            ("The evidence is sufficient to explain tensor factorization. [S1]", "answer"),
        ):
            with self.subTest(text=text), patch("study.query.BookRetriever") as retriever:
                retriever.return_value.invoke.return_value = [document]
                model = MagicMock()
                model.invoke.return_value = SimpleNamespace(content=text)
                result = execute_query("How should I choose LoRA rank?", database_url=self.database_url,
                    book_id=self.book_id, owner_id=self.owner_id, model=model, allow_external_fallback=False)
                self.assertEqual(result.outcome, outcome)

    def test_model_falls_back_to_external_qa_when_evidence_insufficient(self):
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
        mock_model = MagicMock()
        mock_model.invoke.side_effect = [
            MagicMock(content="INSUFFICIENT_EVIDENCE: LoRA target modules are not discussed."),
            MagicMock(content='{"is_sufficient": true, "reason": "General ML concept"}'),
            MagicMock(content="ℹ️ **General Model Knowledge**: Target modules depend on task requirements."),
        ]
        with patch("study.query.BookRetriever") as retriever:
            retriever.return_value.invoke.return_value = [document]
            result = execute_query(
                "How should I choose LoRA target modules?",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=mock_model,
                allow_external_fallback=True,
            )

        self.assertEqual(result.route, "external_qa")
        self.assertEqual(result.source_type, "model_knowledge")

    def test_external_fallback_carries_the_conversation(self):
        """A follow-up that escalates still knows what it is a follow-up to.

        Both fallbacks used to construct an empty `ConversationState`, so a
        question that reached the model or the web arrived with no history at
        all — which is why "add more detail on this part" came back asking
        which part was meant.
        """

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
        conversation = ConversationState(
            conversation_id="conversation-1",
            messages=[
                ConversationMessage(role="user", content="Explain LoRA adapters."),
                ConversationMessage(
                    role="assistant",
                    content="LoRA injects low-rank matrices into attention projections.",
                ),
            ],
            previous_answer="LoRA injects low-rank matrices into attention projections.",
        )
        mock_model = MagicMock()
        mock_model.invoke.side_effect = [
            MagicMock(content="INSUFFICIENT_EVIDENCE: target modules are not discussed."),
            MagicMock(content='{"is_sufficient": true, "reason": "General ML concept"}'),
            MagicMock(content="ℹ️ **General Model Knowledge**: Target modules vary."),
        ]
        with patch("study.query.BookRetriever") as retriever:
            retriever.return_value.invoke.return_value = [document]
            result = execute_query(
                "Which target modules should I pick for it?",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=mock_model,
                allow_external_fallback=True,
                conversation=conversation,
            )

        self.assertEqual(result.route, "external_qa")
        replayed = " ".join(
            str(message.content)
            for message in mock_model.invoke.call_args_list[-1].args[0]
        )
        self.assertIn("Explain LoRA adapters.", replayed)
        self.assertIn("low-rank matrices into attention projections", replayed)

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

    def test_retrieval_budget_follows_response_depth(self):
        document = SimpleNamespace(
            page_content="A supported concept.",
            metadata={
                "book_id": self.book_id,
                "node_id": 1,
                "path": "Chapter 1 :: Core idea",
                "start_page": 2,
                "end_page": 2,
            },
        )
        for depth, expected_k in (("quick", 5), ("interview", 8), ("deep", 8)):
            with (
                self.subTest(depth=depth),
                patch("study.query.BookRetriever") as retriever,
            ):
                retriever.return_value.invoke.return_value = [document]
                execute_query(
                    "Explain the core idea.",
                    database_url=self.database_url,
                    book_id=self.book_id,
                    owner_id=self.owner_id,
                    model=StaticAnswerModel(),
                    force_retrieval=True,
                    response_depth=depth,
                )

            self.assertEqual(retriever.call_args.kwargs["k"], expected_k)
            self.assertTrue(retriever.call_args.kwargs["unique_nodes"])

    def test_system_design_allows_several_chunks_from_one_scope_node(self):
        document = SimpleNamespace(
            page_content="A supported architecture.",
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
            execute_query(
                "Design an API rate limiter.",
                database_url=self.database_url,
                book_id=self.book_id,
                owner_id=self.owner_id,
                model=StaticAnswerModel(),
                force_retrieval=True,
                response_depth="interview",
            )

        self.assertEqual(retriever.call_args.kwargs["k"], 8)
        self.assertFalse(retriever.call_args.kwargs["unique_nodes"])


if __name__ == "__main__":
    unittest.main()
