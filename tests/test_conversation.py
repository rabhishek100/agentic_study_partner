import unittest
from unittest.mock import patch

import langsmith as ls
from langchain_core.callbacks import BaseCallbackHandler

from parsing.models import ParsedBook, Section, TextBlock
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.contracts import (
    ConversationState,
    EvidenceRef,
    ScopeRef,
    TurnDecision,
    TurnResult,
)
from study.conversation import (
    _hierarchy_query,
    execute_conversation_turn,
    new_conversation_state,
    record_turn,
)
from study.graph import StudyGraphContext, study_turn_graph
from tests.postgres import PostgresOwnerMixin
from tests.test_query_routing import CitationSummaryModel


class FakeModel:
    def __init__(self, result):
        self.result = result

    def invoke(self, messages, config=None):
        return self.result


class TraceRecorder(BaseCallbackHandler):
    def __init__(self):
        self.starts = []

    def on_chain_start(
        self,
        serialized,
        inputs,
        *,
        run_id,
        parent_run_id=None,
        name=None,
        metadata=None,
        **kwargs,
    ):
        del serialized, inputs, kwargs
        self.starts.append(
            {
                "name": name,
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "metadata": metadata or {},
            }
        )


class HierarchyQueryRenderingTests(unittest.TestCase):
    def test_paper_section_is_not_reconstructed_as_a_chapter_request(self):
        scope = ScopeRef(
            kind="section",
            book_id=1,
            node_id=10,
            display_path="1 Introduction :: 1.1 Contributions",
            start_page=1,
            end_page=2,
        )
        decision = TurnDecision(
            route="hierarchy_summary",
            history_dependency="independent",
            standalone_query="Explain the contributions.",
            resolved_scope=scope,
            reason="The paper section was selected explicitly.",
        )

        self.assertEqual(
            _hierarchy_query(decision),
            "Summarize 1 Introduction :: 1.1 Contributions.",
        )


def conversation_book():
    sections = [
        Section(
            path=["Chapter 1. Foundations"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(
                    text="Foundations overview.", category="NarrativeText", page=1
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
            (section.level, section.title, section.start_page) for section in sections
        ],
        sections=sections,
    )


class ConversationTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self):
        tracing = ls.tracing_context(enabled=False)
        tracing.__enter__()
        self.addCleanup(tracing.__exit__, None, None, None)
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
            self.book_id = ingest_book(
                connection,
                conversation_book(),
                owner_id=self.owner_id,
                title="Conversation Book",
                author="Test Author",
                file_hash="d" * 64,
                page_count=2,
                parser_version="test-v1",
            )
            self.chapter = dict(
                connection.execute(
                    """
                    SELECT * FROM nodes
                    WHERE node_type = 'chapter' AND owner_id = %s
                    """,
                    (self.owner_id,),
                ).fetchone()
            )

    def tearDown(self):
        self.tearDownPostgresOwner()

    def state(self, **updates):
        values = {
            "conversation_id": "conversation-1",
            "book_ids": [self.book_id],
        }
        values.update(updates)
        return ConversationState(**values)

    def scope(self):
        return ScopeRef(
            kind="chapter",
            book_id=self.book_id,
            node_id=self.chapter["id"],
            display_path=self.chapter["path_text"],
            start_page=1,
            end_page=2,
        )

    def retrieval_result(self, question):
        return TurnResult(
            question=question,
            answer="Grounded answer. [S1]",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query=question,
            outcome="answer",
            retrieval_mode="hybrid",
        )

    def test_summary_sets_scope_and_records_answer(self):
        result, state = execute_conversation_turn(
            "Summarize Chapter 1.",
            new_conversation_state(book_ids=[self.book_id], conversation_id="c1"),
            database_url=self.database_url,
            owner_id=self.owner_id,
            generation_model=CitationSummaryModel(),
        )

        self.assertEqual(result.route, "hierarchy_summary")
        self.assertEqual(state.active_scope.node_id, self.chapter["id"])
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.previous_answer, result.answer)

    def test_followup_uses_rewritten_query_and_retains_scope(self):
        scope = self.scope()
        analysis = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "Compare the dataflow modes in Chapter 1.",
                "scope_node_id": scope.node_id,
                "reason": "The question refers to the active chapter.",
            }
        )
        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("rewritten"),
        ) as execute:
            result, state = execute_conversation_turn(
                "Compare the modes.",
                self.state(active_scope=scope),
                database_url=self.database_url,
                owner_id=self.owner_id,
                analysis_model=analysis,
            )

        self.assertEqual(
            execute.call_args.args[0], "Compare the dataflow modes in Chapter 1."
        )
        self.assertEqual(state.active_scope, scope)
        self.assertEqual(result.history_dependency, "dependent")
        self.assertEqual(result.resolved_scope, scope)
        self.assertTrue(execute.call_args.kwargs["force_retrieval"])
        self.assertEqual(execute.call_args.kwargs["response_depth"], "interview")
        self.assertEqual(
            execute.call_args.kwargs["routing_reason"],
            "The question refers to the active chapter.",
        )

    def test_explicit_deep_followup_overrides_the_composer_depth(self):
        analysis = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "Explain service dataflow in more detail.",
                "reason": "The user requested a deeper explanation.",
            }
        )
        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("service"),
        ) as execute:
            execute_conversation_turn(
                "Go deeper on service dataflow.",
                self.state(previous_answer="Service dataflow overview."),
                database_url=self.database_url,
                owner_id=self.owner_id,
                analysis_model=analysis,
                response_depth="quick",
            )

        self.assertEqual(execute.call_args.kwargs["response_depth"], "deep")

    def test_answer_archetype_uses_original_question_before_rewrite(self):
        analysis = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "independent",
                "standalone_query": "Explain the algorithm and failure trade-offs.",
                "reason": "Retrieve the relevant design evidence.",
            }
        )
        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("rate limiter"),
        ) as execute:
            execute_conversation_turn(
                "Design a distributed API rate limiter.",
                self.state(),
                database_url=self.database_url,
                owner_id=self.owner_id,
                analysis_model=analysis,
            )

        self.assertEqual(
            execute.call_args.kwargs["answer_archetype"],
            "system_design",
        )

    def test_ambiguity_clarifies_without_retrieval(self):
        analysis = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "ambiguous",
                "clarification_question": "Which approach do you mean?",
                "reason": "There is no prior list.",
            }
        )
        with patch("study.conversation.execute_query") as execute:
            result, state = execute_conversation_turn(
                "Explain the second approach.",
                self.state(),
                database_url=self.database_url,
                owner_id=self.owner_id,
                analysis_model=analysis,
            )

        execute.assert_not_called()
        self.assertEqual(result.outcome, "clarify")
        self.assertEqual(state.pending_clarification, "Explain the second approach.")

    def test_answer_clears_pending_clarification(self):
        analysis = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "Explain service dataflow.",
                "reason": "The user supplied the referent.",
            }
        )
        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("service"),
        ):
            _, state = execute_conversation_turn(
                "I mean service dataflow.",
                self.state(pending_clarification="Which approach?"),
                database_url=self.database_url,
                owner_id=self.owner_id,
                analysis_model=analysis,
            )
        self.assertIsNone(state.pending_clarification)

    def test_abstention_retains_retrieved_evidence_for_followup_resolution(self):
        result = self.retrieval_result("unsupported").model_copy(
            update={
                "answer": "Insufficient evidence.",
                "outcome": "abstain",
                "evidence": [
                    EvidenceRef(
                        node_id=self.chapter["id"],
                        pages=[1],
                        path=self.chapter["path_text"],
                    )
                ],
            }
        )

        state = record_turn(self.state(), "Unsupported question?", result)

        self.assertEqual(state.previous_answer, "Insufficient evidence.")
        self.assertEqual(state.previous_evidence, result.evidence)

    def test_section_hierarchy_decision_serializes_a_parseable_query(self):
        section = ScopeRef(
            kind="section",
            book_id=self.book_id,
            node_id=self.chapter["id"] + 1,
            display_path="Chapter 1. Foundations :: Dataflow Modes",
            start_page=2,
            end_page=2,
        )
        analysis = FakeModel(
            {
                "route": "hierarchy_summary",
                "history_dependency": "dependent",
                "scope_node_id": section.node_id,
                "reason": "Summarize the active section.",
            }
        )
        summary = TurnResult(
            question="serialized",
            answer="Summary.",
            route="hierarchy_summary",
            history_dependency="independent",
            standalone_query="serialized",
            resolved_scope=section,
            outcome="answer",
        )
        with patch("study.conversation.execute_query", return_value=summary) as execute:
            execute_conversation_turn(
                "Summarize it.",
                self.state(active_scope=section),
                database_url=self.database_url,
                owner_id=self.owner_id,
                analysis_model=analysis,
            )

        self.assertEqual(
            execute.call_args.args[0],
            "Summarize section Dataflow Modes in chapter 1. Foundations.",
        )

    def test_changing_books_starts_a_new_conversation(self):
        analysis = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "independent",
                "standalone_query": "What is dataflow?",
                "reason": "Independent question.",
            }
        )
        with patch(
            "study.conversation.execute_query",
            return_value=self.retrieval_result("dataflow"),
        ):
            _, state = execute_conversation_turn(
                "What is dataflow?",
                ConversationState(conversation_id="old", book_ids=[99]),
                database_url=self.database_url,
                owner_id=self.owner_id,
                book_id=self.book_id,
                analysis_model=analysis,
            )
        self.assertNotEqual(state.conversation_id, "old")
        self.assertEqual(state.book_ids, [self.book_id])

    def test_graph_groups_the_turn_and_steps_under_one_trace(self):
        recorder = TraceRecorder()
        output = study_turn_graph.invoke(
            {
                "question": "What sections are present in Chapter 1?",
                "conversation": self.state(),
            },
            config={
                "run_name": "study_turn",
                "callbacks": [recorder],
                "metadata": {"thread_id": "conversation-1"},
            },
            context=StudyGraphContext(
                owner_id=self.owner_id,
                database_url=self.database_url,
                retrieval_mode="bm25",
            ),
        )

        starts = {item["name"]: item for item in recorder.starts}
        root = starts["study_turn"]
        self.assertIsNone(root["parent_run_id"])
        for name in (
            "plan_turn",
            "route_turn",
            "execute_hierarchy",
            "update_state",
        ):
            self.assertIsNotNone(starts[name]["parent_run_id"])
            self.assertEqual(
                starts[name]["metadata"]["thread_id"],
                "conversation-1",
            )
        self.assertEqual(output["result"].route, "hierarchy_list")


if __name__ == "__main__":
    unittest.main()
