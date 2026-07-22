from pathlib import Path
import tempfile
import unittest

from evals.multiturn import evaluate_conversations
from evals.report import render_report
from study.contracts import (
    CitationRef,
    EvidenceRef,
    ScopeRef,
    TurnResult,
)


class FakeRunner:
    def __init__(self, results):
        self.results = iter(results)
        self.received_states = []

    def __call__(self, question, state):
        self.received_states.append(state.model_copy(deep=True))
        result, scope = next(self.results)
        updated = state.model_copy(deep=True)
        updated.active_scope = scope or updated.active_scope
        updated.previous_answer = result.answer
        return result, updated


def turn(turn_id, route, dependency, scope_id, evidence):
    return {
        "turn_id": turn_id,
        "user": f"Question {turn_id}",
        "expected_route": route,
        "history_dependency": dependency,
        "expected_standalone_query": f"Standalone {turn_id}",
        "expected_scope": (
            {"kind": "chapter", "node_id": scope_id} if scope_id else None
        ),
        "answerable": True,
        "expected_evidence": [
            {"node_id": node_id, "pages": [node_id], "role": "required"}
            for node_id in evidence
        ],
        "reference_answer": "Reference answer.",
    }


class MultiturnEvaluationTests(unittest.TestCase):
    def test_null_expected_query_is_valid_for_clarification(self):
        result = TurnResult(
            question="Explain the second approach.",
            answer="Which approach do you mean?",
            route="clarify",
            history_dependency="ambiguous",
            outcome="clarify",
        )
        gold = turn("t1", "clarify", "ambiguous", None, [])
        gold["expected_standalone_query"] = None
        gold["answerable"] = False
        conversations = [{"id": "c1", "title": "Clarify", "turns": [gold]}]

        evaluation = evaluate_conversations(
            conversations,
            FakeRunner([(result, None)]),
            book_id=1,
        )

        self.assertEqual(evaluation["summary"]["errors"], 0)
        self.assertEqual(evaluation["summary"]["standalone_exact_accuracy"], 1.0)

    def test_unanswerable_retrieval_scores_abstention_without_evidence_recall(self):
        result = TurnResult(
            question="Unsupported question",
            answer="Insufficient evidence.",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query="Unsupported question",
            outcome="abstain",
        )
        gold = turn("t1", "retrieval_qa", "independent", None, [])
        gold["user"] = "Unsupported question"
        gold["expected_standalone_query"] = "Unsupported question"
        gold["answerable"] = False

        evaluation = evaluate_conversations(
            [{"id": "c1", "title": "Abstain", "turns": [gold]}],
            FakeRunner([(result, None)]),
            book_id=1,
        )

        row = evaluation["turns"][0]
        self.assertTrue(row["checks"]["route"])
        self.assertTrue(row["checks"]["outcome"])
        self.assertNotIn("evidence_recall", row["checks"])

    def test_replays_predicted_state_and_scores_observable_behavior(self):
        scope = ScopeRef(
            kind="chapter",
            book_id=1,
            node_id=10,
            display_path="Chapter 1",
            start_page=1,
            end_page=4,
        )
        first = TurnResult(
            question="Question t1",
            answer="Summary [N10:P1]",
            route="hierarchy_summary",
            history_dependency="independent",
            standalone_query="Standalone t1",
            resolved_scope=scope,
            evidence=[EvidenceRef(node_id=10, pages=[1], path="Chapter 1")],
            citations=[CitationRef(marker="[N10:P1]", node_id=10, page=1)],
            outcome="answer",
        )
        second = TurnResult(
            question="Question t2",
            answer="Answer [S1]",
            route="retrieval_qa",
            history_dependency="dependent",
            standalone_query="Standalone t2",
            evidence=[EvidenceRef(node_id=20, pages=[20], path="Chapter 1 :: Topic")],
            citations=[
                CitationRef(marker="[S1]", node_id=20, page=20, evidence_rank=1)
            ],
            outcome="answer",
        )
        runner = FakeRunner([(first, scope), (second, None)])
        conversations = [
            {
                "id": "c1",
                "title": "A conversation",
                "turns": [
                    turn("t1", "hierarchy_summary", "independent", 10, [10]),
                    turn("t2", "retrieval_qa", "dependent", 10, [20]),
                ],
            }
        ]

        evaluation = evaluate_conversations(conversations, runner, book_id=1)

        self.assertEqual(evaluation["summary"]["route_accuracy"], 1.0)
        self.assertEqual(evaluation["summary"]["required_evidence_recall"], 1.0)
        self.assertEqual(runner.received_states[1].active_scope, scope)

    def test_failures_are_recorded_without_stopping_the_run(self):
        class BrokenRunner:
            def __call__(self, question, state):
                raise RuntimeError("provider unavailable")

        conversations = [
            {
                "id": "c1",
                "title": "Broken",
                "turns": [turn("t1", "retrieval_qa", "independent", None, [1])],
            }
        ]

        evaluation = evaluate_conversations(conversations, BrokenRunner(), book_id=1)

        self.assertEqual(evaluation["summary"]["errors"], 1)
        self.assertEqual(evaluation["summary"]["route_accuracy"], 0.0)

    def test_html_report_is_searchable_and_escapes_source_text(self):
        evaluation = {
            "summary": {
                "turns": 1,
                "route_accuracy": 1.0,
                "history_dependency_accuracy": 1.0,
                "scope_accuracy": 1.0,
                "outcome_accuracy": 1.0,
                "required_evidence_recall": 1.0,
                "citation_validity": 1.0,
                "standalone_exact_accuracy": 1.0,
                "errors": 0,
            },
            "turns": [
                {
                    "conversation_id": "c1",
                    "conversation_title": "<unsafe>",
                    "turn_id": "t1",
                    "gold": turn("t1", "retrieval_qa", "independent", None, [1]),
                    "prediction": {
                        "answer": "**Grounded**",
                        "route": "retrieval_qa",
                        "history_dependency": "independent",
                        "standalone_query": "Standalone t1",
                        "resolved_scope": None,
                    },
                    "checks": {
                        "route": True,
                        "history_dependency": True,
                        "scope": True,
                        "outcome": True,
                        "evidence_recall": 1.0,
                        "citations_valid": True,
                        "standalone_exact": True,
                    },
                    "answer_judgment": None,
                }
            ],
        }
        evaluation["turns"][0]["gold"]["expected_standalone_query"] = None
        with tempfile.TemporaryDirectory() as directory:
            output = render_report(evaluation, Path(directory) / "report.html")
            html = output.read_text(encoding="utf-8")

        self.assertIn('id="search"', html)
        self.assertIn("&lt;unsafe&gt;", html)
        self.assertNotIn("<unsafe>", html)
        self.assertIn("<dt>Query</dt><dd>—</dd>", html)


if __name__ == "__main__":
    unittest.main()
