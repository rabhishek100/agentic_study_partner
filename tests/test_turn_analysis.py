import json
from pathlib import Path
import tempfile
import unittest

from storage.sqlite import connect, ingest_book, initialize
from study.analyze import ConversationDecisionError, analyze_turn
from study.contracts import ConversationMessage, ConversationState, ScopeRef
from tests.test_scope_candidates import FILE_HASH, hierarchy_book


class FakeModel:
    def __init__(self, *outcomes):
        self.outcomes = outcomes
        self.calls = []

    def invoke(self, messages, config=None):
        self.calls.append((messages, config))
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FailIfCalled:
    def invoke(self, messages, config=None):
        raise AssertionError("deterministic routing called the model")


class ConversationDecisionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "books.sqlite3"
        with connect(self.database) as connection:
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
                for row in connection.execute("SELECT * FROM nodes")
            }

    def tearDown(self):
        self.directory.cleanup()

    def state(self, **updates):
        values = {
            "conversation_id": "conversation-1",
            "book_id": self.book_id,
        }
        values.update(updates)
        return ConversationState(**values)

    def scope(self, title, kind="section"):
        node = self.nodes[title]
        return ScopeRef(
            kind=kind,
            book_id=self.book_id,
            node_id=node["id"],
            display_path=node["path_text"],
            start_page=node["start_page"],
            end_page=node["end_page"],
        )

    def analyze(self, question, state, model):
        return analyze_turn(question, state, self.database, model=model)

    def test_explicit_hierarchy_requests_are_deterministic(self):
        summary = self.analyze(
            "Summarize Chapter 3.", self.state(), FailIfCalled()
        )
        listing = self.analyze(
            "What sections are present in Chapter 7?",
            self.state(),
            FailIfCalled(),
        )
        chapters = self.analyze(
            "what chapters does this book have",
            self.state(),
            FailIfCalled(),
        )

        self.assertEqual(summary.route, "hierarchy_summary")
        self.assertEqual(
            summary.resolved_scope.node_id,
            self.nodes["Chapter 3. Data Engineering Fundamentals"]["id"],
        )
        self.assertEqual(listing.route, "hierarchy_list")
        self.assertEqual(chapters.route, "hierarchy_list")
        self.assertEqual(chapters.resolved_scope.kind, "book")
        self.assertIsNone(chapters.resolved_scope.node_id)

    def test_model_rewrites_followup_and_selects_only_canonical_scope(self):
        active = self.scope("Low-Rank Factorization")
        model = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": (
                    "What does Low-Rank Factorization in Chapter 7 cover?"
                ),
                "scope_node_id": active.node_id,
                "reason": "The follow-up refers to the active section.",
            }
        )

        decision = self.analyze(
            "What does that section cover?",
            self.state(active_scope=active),
            model,
        )

        self.assertEqual(decision.resolved_scope, active)
        self.assertEqual(decision.history_dependency, "dependent")

    def test_ambiguous_reference_can_request_clarification(self):
        model = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "ambiguous",
                "clarification_question": "Which approach do you mean?",
                "reason": "No prior list establishes the ordinal.",
            }
        )

        decision = self.analyze(
            "Explain the second approach.", self.state(), model
        )

        self.assertEqual(decision.route, "clarify")
        self.assertEqual(decision.clarification_question, "Which approach do you mean?")

    def test_invalid_model_output_is_retried_three_times(self):
        model = FakeModel(
            {
                "route": "hierarchy_summary",
                "history_dependency": "dependent",
                "scope_node_id": 999999,
                "reason": "Invented scope.",
            }
        )

        with self.assertRaisesRegex(
            ConversationDecisionError, "after 3 attempts"
        ):
            self.analyze("Summarize that section.", self.state(), model)
        self.assertEqual(len(model.calls), 3)

    def test_prior_transform_requires_an_answer(self):
        model = FakeModel(
            {
                "route": "prior_answer_transform",
                "history_dependency": "dependent",
                "reason": "Shorten the prior response.",
            }
        )

        with self.assertRaisesRegex(
            ConversationDecisionError, "after 3 attempts"
        ):
            self.analyze("Make that shorter.", self.state(), model)

        decision = self.analyze(
            "Make that shorter.",
            self.state(previous_answer="Grounded answer."),
            model,
        )
        self.assertEqual(decision.route, "prior_answer_transform")

    def test_payload_contains_only_bounded_recent_context(self):
        messages = [
            ConversationMessage(
                role="user" if index % 2 == 0 else "assistant",
                content=f"message-{index}-" + "x" * 3000,
            )
            for index in range(10)
        ]
        model = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "Explain the referenced concept.",
                "reason": "It depends on recent history.",
            }
        )

        self.analyze(
            "Explain it.",
            self.state(messages=messages, previous_answer="a" * 4000),
            model,
        )

        payload = json.loads(model.calls[0][0][1][1])
        self.assertEqual(len(payload["recent_messages"]), 6)
        self.assertNotIn("message-0-", str(payload))
        self.assertLessEqual(len(payload["previous_answer"]), 1601)

    def test_empty_question_fails_without_model_call(self):
        model = FakeModel()
        with self.assertRaisesRegex(
            ConversationDecisionError, "cannot be empty"
        ):
            self.analyze(" ", self.state(), model)
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
