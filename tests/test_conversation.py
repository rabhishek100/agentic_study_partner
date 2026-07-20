from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from parsing.models import ParsedBook, Section, TextBlock
from storage.sqlite import connect, ingest_book, initialize
from study.contracts import (
    ConversationState,
    ScopeRef,
    StateUpdate,
    SufficiencyDecision,
    TurnAnalysis,
    TurnResult,
)
from study.conversation import (
    execute_conversation_turn,
    new_conversation_state,
)
from tests.test_query_routing import CitationSummaryModel


class FakeAnalysisModel:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def invoke(self, messages, config=None):
        self.calls.append((messages, config))
        return self.result


def conversation_book() -> ParsedBook:
    sections = [
        Section(
            path=["Chapter 1. Foundations"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(
                    text="Foundations overview.",
                    category="NarrativeText",
                    page=1,
                )
            ],
        ),
        Section(
            path=["Chapter 1. Foundations", "Dataflow Modes"],
            level=2,
            start_page=2,
            end_page=2,
            texts=[
                TextBlock(
                    text="Database, service, and event dataflow.",
                    category="NarrativeText",
                    page=2,
                )
            ],
        ),
    ]
    return ParsedBook(
        source="sources/books/conversation.pdf",
        toc=[
            (section.level, section.title, section.start_page)
            for section in sections
        ],
        sections=sections,
    )


class ConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.source_path = (
            Path(self.temporary_directory.name) / "books.sqlite3"
        )
        with connect(self.source_path) as connection:
            initialize(connection)
            self.book_id = ingest_book(
                connection,
                conversation_book(),
                title="Conversation Book",
                author="Test Author",
                file_hash="d" * 64,
                page_count=2,
                parser_version="test-v1",
            )
            self.nodes = {
                row["title"]: dict(row)
                for row in connection.execute(
                    "SELECT * FROM nodes ORDER BY toc_index"
                )
            }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def scope(self, title: str, *, kind: str) -> ScopeRef:
        node = self.nodes[title]
        return ScopeRef(
            kind=kind,
            book_id=self.book_id,
            node_id=node["id"],
            display_path=node["path_text"],
            start_page=node["start_page"],
            end_page=node["end_page"],
        )

    def retrieval_result(self, question: str) -> TurnResult:
        return TurnResult(
            question=question,
            answer="Grounded answer. [S1]",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query=question,
            scope_behavior="global",
            sufficiency=SufficiencyDecision(status="sufficient"),
            outcome="answer",
            retrieval_mode="hybrid",
        )

    def test_explicit_summary_sets_active_scope_and_records_turn(self):
        result, state = execute_conversation_turn(
            "Summarize Chapter 1.",
            new_conversation_state(
                book_id=self.book_id,
                conversation_id="conversation-1",
            ),
            source_path=self.source_path,
            book_id=self.book_id,
            generation_model=CitationSummaryModel(),
        )

        self.assertEqual(result.route, "hierarchy_summary")
        self.assertEqual(
            state.active_scope.node_id,
            self.nodes["Chapter 1. Foundations"]["id"],
        )
        self.assertEqual(result.state_update.active_scope, "set_active_scope")
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.previous_answer, result.answer)

    def test_ordinary_followup_uses_rewrite_but_retains_active_scope(self):
        chapter = self.scope("Chapter 1. Foundations", kind="chapter")
        state = new_conversation_state(
            book_id=self.book_id,
            conversation_id="conversation-2",
        ).model_copy(update={"active_scope": chapter})
        analysis_model = FakeAnalysisModel(
            TurnAnalysis(
                route="retrieval_qa",
                history_dependency="dependent",
                standalone_query=(
                    "Compare database, service, and event dataflow "
                    "in Chapter 1."
                ),
                scope_behavior="prefer_scope",
                resolved_scope=chapter,
                state_update=StateUpdate(
                    active_scope="set_active_scope"
                ),
                decision_reason="The follow-up refers to the chapter modes.",
                decision_source="llm",
            )
        )

        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("rewritten"),
        ) as execute:
            result, updated = execute_conversation_turn(
                "Compare the three modes.",
                state,
                source_path=self.source_path,
                book_id=self.book_id,
                analysis_model=analysis_model,
            )

        self.assertEqual(
            execute.call_args.args[0],
            "Compare database, service, and event dataflow in Chapter 1.",
        )
        self.assertEqual(updated.active_scope, chapter)
        self.assertIsNone(result.resolved_scope)
        self.assertEqual(result.scope_behavior, "global")
        self.assertEqual(result.state_update.active_scope, "retain")

    def test_independent_topic_switch_does_not_replace_saved_scope(self):
        chapter = self.scope("Chapter 1. Foundations", kind="chapter")
        state = new_conversation_state(
            book_id=self.book_id,
            conversation_id="conversation-3",
        ).model_copy(update={"active_scope": chapter})
        analysis_model = FakeAnalysisModel(
            TurnAnalysis(
                route="retrieval_qa",
                history_dependency="independent",
                standalone_query="How does reservoir sampling work?",
                scope_behavior="global",
                state_update=StateUpdate(active_scope="retain"),
                decision_reason="This is a new topic.",
                decision_source="llm",
            )
        )

        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("reservoir"),
        ):
            result, updated = execute_conversation_turn(
                "How does reservoir sampling work?",
                state,
                source_path=self.source_path,
                book_id=self.book_id,
                analysis_model=analysis_model,
            )

        self.assertEqual(result.history_dependency, "independent")
        self.assertEqual(updated.active_scope, chapter)

    def test_ambiguous_reference_returns_clarification_without_retrieval(self):
        analysis_model = FakeAnalysisModel(
            TurnAnalysis(
                route="clarify",
                history_dependency="ambiguous",
                scope_behavior="clarify",
                state_update=StateUpdate(
                    pending_clarification="set"
                ),
                clarification_question=(
                    "Which approaches are you referring to?"
                ),
                decision_reason="There is no prior list.",
                decision_source="llm",
            )
        )

        with patch("study.conversation.execute_query") as execute:
            result, state = execute_conversation_turn(
                "Explain the second approach.",
                new_conversation_state(
                    book_id=self.book_id,
                    conversation_id="conversation-4",
                ),
                source_path=self.source_path,
                book_id=self.book_id,
                analysis_model=analysis_model,
            )

        execute.assert_not_called()
        self.assertEqual(result.outcome, "clarify")
        self.assertEqual(
            result.answer,
            "Which approaches are you referring to?",
        )
        self.assertEqual(
            state.pending_clarification,
            "Explain the second approach.",
        )

    def test_resolved_clarification_is_cleared_after_answer(self):
        state = new_conversation_state(
            book_id=self.book_id,
            conversation_id="conversation-5",
        ).model_copy(
            update={"pending_clarification": "Which approach?"}
        )
        analysis_model = FakeAnalysisModel(
            TurnAnalysis(
                route="retrieval_qa",
                history_dependency="dependent",
                standalone_query="Explain service dataflow in Chapter 1.",
                scope_behavior="global",
                state_update=StateUpdate(),
                decision_reason="The user supplied the missing referent.",
                decision_source="llm",
            )
        )

        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("service dataflow"),
        ):
            result, updated = execute_conversation_turn(
                "I mean service dataflow.",
                state,
                source_path=self.source_path,
                book_id=self.book_id,
                analysis_model=analysis_model,
            )

        self.assertIsNone(updated.pending_clarification)
        self.assertEqual(
            result.state_update.pending_clarification,
            "clear",
        )

    def test_changing_book_starts_a_fresh_internal_conversation(self):
        original = ConversationState(
            conversation_id="old",
            book_id=99,
        )
        analysis_model = FakeAnalysisModel(
            TurnAnalysis(
                route="retrieval_qa",
                history_dependency="independent",
                standalone_query="What is dataflow?",
                scope_behavior="global",
                state_update=StateUpdate(),
                decision_reason="Independent question.",
                decision_source="llm",
            )
        )

        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("dataflow"),
        ):
            _, updated = execute_conversation_turn(
                "What is dataflow?",
                original,
                source_path=self.source_path,
                book_id=self.book_id,
                analysis_model=analysis_model,
            )

        self.assertNotEqual(updated.conversation_id, "old")
        self.assertEqual(updated.book_id, self.book_id)
        self.assertEqual(len(updated.messages), 2)


if __name__ == "__main__":
    unittest.main()
