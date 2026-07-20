import json
from pathlib import Path
import tempfile
import unittest

from storage.sqlite import connect, ingest_book, initialize
from study.analyze import TurnAnalysisError, analyze_turn
from study.contracts import (
    ConversationMessage,
    ConversationState,
    EvidenceRef,
    ScopeRef,
    StateUpdate,
    TurnAnalysis,
)
from tests.test_scope_candidates import FILE_HASH, hierarchy_book


class FakeAnalysisModel:
    def __init__(self, *outcomes) -> None:
        self.outcomes = outcomes
        self.calls = []

    def invoke(self, messages, config=None):
        self.calls.append((messages, config))
        index = min(len(self.calls) - 1, len(self.outcomes) - 1)
        outcome = self.outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FailIfCalled:
    def invoke(self, messages, config=None):
        del messages, config
        raise AssertionError("the deterministic path called the model")


class TurnAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "books.sqlite3"
        )
        with connect(self.database_path) as connection:
            initialize(connection)
            self.book_id = ingest_book(
                connection,
                hierarchy_book(),
                title="Hierarchy Book",
                author="Test Author",
                file_hash=FILE_HASH,
                page_count=10,
                parser_version="test-v1",
            )
            self.nodes = {
                row["title"]: dict(row)
                for row in connection.execute(
                    """
                    SELECT
                        id, book_id, title, path_text,
                        start_page, end_page
                    FROM nodes
                    """
                )
            }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def state(self, **updates) -> ConversationState:
        values = {
            "conversation_id": "conversation-1",
            "book_id": self.book_id,
        }
        values.update(updates)
        return ConversationState(**values)

    def scope(
        self,
        title: str,
        *,
        kind: str = "section",
    ) -> ScopeRef:
        node = self.nodes[title]
        return ScopeRef(
            kind=kind,
            book_id=node["book_id"],
            node_id=node["id"],
            display_path=node["path_text"],
            start_page=node["start_page"],
            end_page=node["end_page"],
        )

    def analyze(
        self,
        question: str,
        state: ConversationState,
        model,
    ) -> TurnAnalysis:
        return analyze_turn(
            question,
            state,
            self.database_path,
            model=model,
        )

    def test_explicit_summary_uses_deterministic_fast_path(self):
        analysis = self.analyze(
            "Summarize Chapter 3.",
            self.state(),
            FailIfCalled(),
        )

        self.assertEqual(analysis.route, "hierarchy_summary")
        self.assertEqual(analysis.decision_source, "deterministic")
        self.assertEqual(analysis.history_dependency, "independent")
        self.assertEqual(analysis.scope_behavior, "hard_filter")
        self.assertEqual(
            analysis.resolved_scope.node_id,
            self.nodes["Chapter 3. Data Engineering Fundamentals"]["id"],
        )
        self.assertEqual(
            (
                analysis.resolved_scope.start_page,
                analysis.resolved_scope.end_page,
            ),
            (2, 4),
        )
        self.assertEqual(
            analysis.state_update.active_scope,
            "set_active_scope",
        )

    def test_explicit_listing_uses_deterministic_fast_path(self):
        analysis = self.analyze(
            "What sections are present in Chapter 7?",
            self.state(),
            FailIfCalled(),
        )

        self.assertEqual(analysis.route, "hierarchy_list")
        self.assertIn(
            "Chapter 7. Model Deployment and Prediction Service",
            analysis.standalone_query,
        )

    def test_dependent_follow_up_is_rewritten_and_scope_is_canonicalized(self):
        active_scope = self.scope("Low-Rank Factorization")
        model = FakeAnalysisModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": (
                    "What does the Low-Rank Factorization subsection "
                    "in Chapter 7 cover?"
                ),
                "scope_behavior": "prefer_scope",
                "resolved_scope": {
                    "kind": "section",
                    "book_id": 999,
                    "node_id": active_scope.node_id,
                    "display_path": "fabricated",
                    "start_page": 1,
                    "end_page": 1,
                },
                "state_update": {
                    "active_scope": "retain",
                    "pending_clarification": "none",
                },
                "clarification_question": None,
                "decision_reason": (
                    "The phrase refers to the active subsection."
                ),
                "decision_source": "llm",
            }
        )
        state = self.state(
            active_scope=active_scope,
            messages=[
                ConversationMessage(
                    role="user",
                    content=(
                        "Could the low-rank factorization section answer it?"
                    ),
                    turn_id="t1",
                ),
                ConversationMessage(
                    role="assistant",
                    content="It discusses compression rather than LoRA.",
                    turn_id="t1",
                ),
            ],
            previous_evidence=[
                EvidenceRef(
                    node_id=active_scope.node_id,
                    pages=[10],
                    path=active_scope.display_path,
                )
            ],
        )

        analysis = self.analyze(
            "Then what does that section actually cover?",
            state,
            model,
        )

        self.assertEqual(analysis.history_dependency, "dependent")
        self.assertEqual(
            analysis.resolved_scope,
            active_scope,
        )
        self.assertEqual(len(model.calls), 1)

    def test_ambiguous_ordinal_requests_clarification(self):
        model = FakeAnalysisModel(
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
                decision_reason=(
                    "No prior list establishes a second approach."
                ),
                decision_source="llm",
            )
        )

        analysis = self.analyze(
            "Explain the second approach.",
            self.state(),
            model,
        )

        self.assertEqual(analysis.route, "clarify")
        self.assertEqual(
            analysis.state_update.pending_clarification,
            "set",
        )

    def test_independent_topic_switch_can_use_global_retrieval(self):
        active_scope = self.scope(
            "Chapter 3. Data Engineering Fundamentals",
            kind="chapter",
        )
        model = FakeAnalysisModel(
            TurnAnalysis(
                route="retrieval_qa",
                history_dependency="independent",
                standalone_query="How does reservoir sampling work?",
                scope_behavior="global",
                state_update=StateUpdate(active_scope="retain"),
                decision_reason=(
                    "This is an independent concept outside the active scope."
                ),
                decision_source="llm",
            )
        )

        analysis = self.analyze(
            "How does reservoir sampling work?",
            self.state(active_scope=active_scope),
            model,
        )

        self.assertEqual(analysis.scope_behavior, "global")
        self.assertIsNone(analysis.resolved_scope)
        self.assertEqual(analysis.state_update.active_scope, "retain")

    def test_unresolved_explicit_scope_falls_through_to_model(self):
        model = FakeAnalysisModel(
            TurnAnalysis(
                route="clarify",
                history_dependency="independent",
                scope_behavior="clarify",
                state_update=StateUpdate(
                    pending_clarification="set"
                ),
                clarification_question=(
                    "There is no Chapter 99. Which chapter did you mean?"
                ),
                decision_reason=(
                    "The explicit chapter does not exist in the book."
                ),
                decision_source="llm",
            )
        )

        analysis = self.analyze(
            "Summarize Chapter 99.",
            self.state(),
            model,
        )

        self.assertEqual(analysis.route, "clarify")
        self.assertEqual(len(model.calls), 1)

    def test_transient_failure_retries_the_same_model(self):
        valid = TurnAnalysis(
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query="What is ETL?",
            scope_behavior="global",
            state_update=StateUpdate(),
            decision_reason="The question is independent and explicit.",
            decision_source="llm",
        )
        model = FakeAnalysisModel(RuntimeError("temporary"), valid)

        analysis = self.analyze(
            "What is ETL?",
            self.state(),
            model,
        )

        self.assertEqual(analysis.route, "retrieval_qa")
        self.assertEqual(len(model.calls), 2)
        self.assertIs(model.calls[0][0], model.calls[1][0])

    def test_invented_scope_is_rejected_after_bounded_attempts(self):
        invalid = TurnAnalysis(
            route="retrieval_qa",
            history_dependency="dependent",
            standalone_query="Explain the invented section.",
            scope_behavior="prefer_scope",
            resolved_scope=ScopeRef(
                kind="section",
                book_id=self.book_id,
                node_id=999999,
                display_path="Invented",
                start_page=1,
                end_page=1,
            ),
            state_update=StateUpdate(active_scope="retain"),
            decision_reason="The section was mentioned previously.",
            decision_source="llm",
        )
        model = FakeAnalysisModel(invalid)

        with self.assertRaisesRegex(
            TurnAnalysisError,
            "after 3 attempts",
        ):
            self.analyze(
                "Explain that section.",
                self.state(),
                model,
            )

        self.assertEqual(len(model.calls), 3)

    def test_prior_transform_requires_a_previous_answer(self):
        invalid = TurnAnalysis(
            route="prior_answer_transform",
            history_dependency="dependent",
            scope_behavior="reuse_prior_answer",
            state_update=StateUpdate(),
            decision_reason="The user requested a shorter version.",
            decision_source="llm",
        )
        model = FakeAnalysisModel(invalid)

        with self.assertRaisesRegex(
            TurnAnalysisError,
            "requires a previous answer",
        ):
            self.analyze(
                "Make that shorter.",
                self.state(),
                model,
            )

    def test_prior_transform_reuses_an_existing_answer(self):
        model = FakeAnalysisModel(
            TurnAnalysis(
                route="prior_answer_transform",
                history_dependency="dependent",
                scope_behavior="reuse_prior_answer",
                state_update=StateUpdate(active_scope="retain"),
                decision_reason=(
                    "The user requested only a shorter presentation."
                ),
                decision_source="llm",
            )
        )

        analysis = self.analyze(
            "Make that shorter.",
            self.state(previous_answer="A grounded prior answer."),
            model,
        )

        self.assertEqual(analysis.route, "prior_answer_transform")
        self.assertEqual(analysis.scope_behavior, "reuse_prior_answer")

    def test_payload_bounds_history_and_previous_answer(self):
        messages = [
            ConversationMessage(
                role="user" if index % 2 == 0 else "assistant",
                content=(f"message-{index}-" + "x" * 3000),
                turn_id=f"t{index // 2}",
            )
            for index in range(10)
        ]
        model = FakeAnalysisModel(
            TurnAnalysis(
                route="retrieval_qa",
                history_dependency="dependent",
                standalone_query="Explain the referenced concept.",
                scope_behavior="global",
                state_update=StateUpdate(),
                decision_reason="The message depends on recent history.",
                decision_source="llm",
            )
        )

        self.analyze(
            "Explain it.",
            self.state(
                messages=messages,
                previous_answer="a" * 4000,
            ),
            model,
        )

        human = model.calls[0][0][1][1]
        payload = json.loads(human.split("\n", 1)[1])
        self.assertEqual(len(payload["recent_messages"]), 6)
        self.assertNotIn("message-0-", human)
        self.assertIn("message-9-", human)
        self.assertLessEqual(
            len(payload["recent_messages"][0]["content"]),
            2001,
        )
        self.assertLessEqual(
            len(payload["state"]["previous_answer_excerpt"]),
            2001,
        )
        self.assertIn("canonical_scope_candidates", payload)

    def test_empty_question_fails_before_model(self):
        model = FakeAnalysisModel()

        with self.assertRaisesRegex(
            TurnAnalysisError,
            "question cannot be empty",
        ):
            self.analyze("   ", self.state(), model)

        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
