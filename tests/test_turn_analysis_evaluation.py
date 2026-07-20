import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.judge import QueryMeaningJudgment
from evals.turn_analysis import evaluate_turn_analysis
from evals.turn_analysis_report import build_turn_analysis_html
from parsing.models import ParsedBook, Section, TextBlock
from scripts.evaluate_turn_analysis import (
    execution_plan,
    select_turns,
    validate_live_environment,
)
from storage.sqlite import connect, ingest_book, initialize
from study.contracts import StateUpdate, TurnAnalysis


def sample_book() -> ParsedBook:
    sections = [
        Section(
            path=["Chapter 1. Foundations"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(
                    text="Foundations content.",
                    category="NarrativeText",
                    page=1,
                )
            ],
        ),
        Section(
            path=["Chapter 1. Foundations", "Data Validation"],
            level=2,
            start_page=2,
            end_page=2,
            texts=[
                TextBlock(
                    text="Validation content.",
                    category="NarrativeText",
                    page=2,
                )
            ],
        ),
    ]
    return ParsedBook(
        source="sources/books/test.pdf",
        toc=[
            (section.level, section.title, section.start_page)
            for section in sections
        ],
        sections=sections,
    )


class FakeAnalyzer:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.states = []

    def invoke(self, question, *, state):
        self.states.append(state.model_copy(deep=True))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeQueryJudge:
    def __init__(self, preserves_meaning=True) -> None:
        self.preserves_meaning = preserves_meaning
        self.calls = []

    def evaluate(self, **values):
        self.calls.append(values)
        return QueryMeaningJudgment(
            preserves_meaning=self.preserves_meaning,
            missing_concepts=[] if self.preserves_meaning else ["scope"],
            added_assumptions=[],
            explanation="Equivalent." if self.preserves_meaning else "Scope lost.",
        )


class TurnAnalysisEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "books.sqlite3"
        )
        with connect(self.database_path) as connection:
            initialize(connection)
            self.book_id = ingest_book(
                connection,
                sample_book(),
                title="Test Book",
                author="Test Author",
                file_hash="c" * 64,
                page_count=2,
                parser_version="test-v1",
            )
            self.node_ids = {
                row["title"]: row["id"]
                for row in connection.execute(
                    "SELECT id, title FROM nodes"
                )
            }
        self.chapter_id = self.node_ids["Chapter 1. Foundations"]
        self.section_id = self.node_ids["Data Validation"]
        self.gold = self._gold()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _turn(
        self,
        turn_id,
        user,
        *,
        route,
        dependency,
        query,
        scope,
        behavior,
        state_update,
        answerable=True,
        reference="Reference answer.",
        evidence=None,
        clarification_update="none",
    ):
        return {
            "turn_id": turn_id,
            "user": user,
            "expected_route": route,
            "history_dependency": dependency,
            "scope_behavior": behavior,
            "expected_standalone_query": query,
            "expected_scope": scope,
            "state_update": state_update,
            "pending_clarification_update": clarification_update,
            "answerable": answerable,
            "expected_evidence": evidence or [],
            "reference_answer": reference,
        }

    def _gold(self):
        chapter_scope = {"kind": "chapter", "node_id": self.chapter_id}
        return {
            "set_id": "test-multiturn",
            "book": {
                "database_book_id": self.book_id,
                "source_file_sha256": "c" * 64,
                "title": "Test Book",
            },
            "conversations": [
                {
                    "id": "mt-test",
                    "title": "State isolation",
                    "category": "test",
                    "tags": ["test"],
                    "turns": [
                        self._turn(
                            "mt-test-t1",
                            "Summarize Chapter 1.",
                            route="hierarchy_summary",
                            dependency="independent",
                            query="Summarize Chapter 1: Foundations.",
                            scope=chapter_scope,
                            behavior="hard_filter",
                            state_update="set_active_scope",
                            reference="Chapter reference.",
                            evidence=[
                                {
                                    "node_id": self.chapter_id,
                                    "pages": [1],
                                    "role": "required",
                                }
                            ],
                        ),
                        self._turn(
                            "mt-test-t2",
                            "What does its validation section cover?",
                            route="retrieval_qa",
                            dependency="dependent",
                            query=(
                                "What does the Data Validation section "
                                "in Chapter 1 cover?"
                            ),
                            scope=chapter_scope,
                            behavior="prefer_scope",
                            state_update="retain",
                            evidence=[
                                {
                                    "node_id": self.section_id,
                                    "pages": [2],
                                    "role": "required",
                                }
                            ],
                        ),
                    ],
                }
            ],
        }

    def analysis(self, *, query=None):
        return TurnAnalysis(
            route="retrieval_qa",
            history_dependency="dependent",
            standalone_query=query
            or "What does the Data Validation section in Chapter 1 cover?",
            scope_behavior="prefer_scope",
            resolved_scope=None,
            state_update=StateUpdate(active_scope="retain"),
            decision_reason="The question refers to the active chapter.",
            decision_source="llm",
        )

    def test_selected_turn_uses_reconstructed_gold_state(self):
        analysis = self.analysis()
        chapter = self.gold["conversations"][0]["turns"][1][
            "expected_scope"
        ]
        analysis.resolved_scope = None
        analyzer = FakeAnalyzer(analysis)

        result = evaluate_turn_analysis(
            self.gold,
            analyzer=analyzer,
            source_path=self.database_path,
            scored_turn_ids={"mt-test-t2"},
        )

        state = analyzer.states[0]
        self.assertEqual(len(analyzer.states), 1)
        self.assertEqual(state.active_scope.node_id, chapter["node_id"])
        self.assertEqual(state.previous_route, "hierarchy_summary")
        self.assertEqual(state.previous_answer, "Chapter reference.")
        self.assertEqual(
            [item.node_id for item in state.previous_evidence],
            [self.chapter_id],
        )
        self.assertEqual(
            [message.role for message in state.messages],
            ["user", "assistant"],
        )
        turn = result["conversations"][0]["turns"][0]
        self.assertEqual(turn["turn_id"], "mt-test-t2")
        self.assertFalse(turn["judgment"]["scope_correct"])

    def test_semantic_judge_scores_paraphrase_and_exact_match_skips_it(self):
        paraphrase = self.analysis(
            query="Within Chapter 1, explain its data-validation section."
        )
        exact = self.analysis()
        judge = FakeQueryJudge()

        paraphrase_result = evaluate_turn_analysis(
            self.gold,
            analyzer=FakeAnalyzer(paraphrase),
            source_path=self.database_path,
            scored_turn_ids={"mt-test-t2"},
            query_judge=judge,
        )
        exact_result = evaluate_turn_analysis(
            self.gold,
            analyzer=FakeAnalyzer(exact),
            source_path=self.database_path,
            scored_turn_ids={"mt-test-t2"},
            query_judge=judge,
        )

        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(
            paraphrase_result["conversations"][0]["turns"][0][
                "query_meaning"
            ]["method"],
            "judge",
        )
        self.assertEqual(
            exact_result["conversations"][0]["turns"][0][
                "query_meaning"
            ]["method"],
            "exact",
        )

    def test_analysis_error_is_recorded_without_stopping_the_run(self):
        analyzer = FakeAnalyzer(
            RuntimeError("provider unavailable"),
            self.analysis(),
        )

        result = evaluate_turn_analysis(
            self.gold,
            analyzer=analyzer,
            source_path=self.database_path,
        )

        self.assertEqual(result["summary"]["analysis_error_count"], 1)
        turns = result["conversations"][0]["turns"]
        self.assertIsNone(turns[0]["prediction"])
        self.assertEqual(turns[0]["analysis_error"], "provider unavailable")
        self.assertIsNotNone(turns[1]["prediction"])
        self.assertEqual(
            analyzer.states[1].active_scope.node_id,
            self.chapter_id,
        )

    def test_report_is_detailed_and_result_is_json_serializable(self):
        result = evaluate_turn_analysis(
            self.gold,
            analyzer=FakeAnalyzer(self.analysis()),
            source_path=self.database_path,
            scored_turn_ids={"mt-test-t2"},
        )
        result["run"] = {
            "run_id": "test-run",
            "generated_at": "2026-07-20T00:00:00+00:00",
            "model": "fake",
            "reasoning": "none",
            "langsmith_project": "test",
        }

        report = build_turn_analysis_html(result)

        json.dumps(result)
        self.assertIn("Expected decision", report)
        self.assertIn("Observed decision", report)
        self.assertIn("Gold pre-turn state", report)
        self.assertIn("Canonical scope candidates", report)
        self.assertIn("Standalone-query meaning", report)

    def test_selection_and_execution_plan_are_bounded(self):
        args = argparse.Namespace(
            all=False,
            conversation=None,
            turn=["mt-test-t2"],
            limit=None,
        )

        turns, conversations, selection = select_turns(self.gold, args)
        plan = execution_plan(
            self.gold,
            scored_turn_ids=turns,
            conversation_ids=conversations,
        )

        self.assertEqual(turns, {"mt-test-t2"})
        self.assertEqual(conversations, {"mt-test"})
        self.assertEqual(selection["mode"], "turn")
        self.assertEqual(plan["scored_turn_count"], 1)
        self.assertEqual(plan["openrouter_calls_upper_bound"], 2)

    def test_live_environment_requires_tracing_and_keys(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                validate_live_environment()
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test",
                "LANGSMITH_API_KEY": "test",
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_PROJECT": "study-partner",
            },
            clear=True,
        ):
            environment = validate_live_environment()

        self.assertEqual(environment["project"], "study-partner")
        self.assertEqual(environment["control_model"], "x-ai/grok-4.5")


if __name__ == "__main__":
    unittest.main()
