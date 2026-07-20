import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.judge import AnswerQualityJudgment
from evals.multiturn import evaluate_conversations
from evals.report import build_html
from scripts.evaluate_multiturn import (
    _execution_plan,
    _selection,
    _validate_live_environment,
    build_argument_parser,
)
from storage.sqlite import connect, ingest_book, initialize
from study.contracts import (
    CitationRef,
    ConversationState,
    EvidenceRef,
    ScopeRef,
    StateUpdate,
    TurnResult,
)
from tests.test_storage import FILE_HASH, sample_book


class FakeExecutor:
    def __init__(self, chapter_id: int, section_id: int) -> None:
        self.chapter_id = chapter_id
        self.section_id = section_id
        self.seen_states: list[ConversationState] = []

    def invoke(
        self,
        question: str,
        *,
        state: ConversationState,
    ) -> TurnResult:
        self.seen_states.append(state.model_copy(deep=True))
        if question.startswith("List"):
            return TurnResult(
                question=question,
                answer="# Chapter 1\n\n- Core idea",
                route="hierarchy_list",
                history_dependency="independent",
                standalone_query="List sections in Chapter 1.",
                scope_behavior="hard_filter",
                resolved_scope=ScopeRef(
                    kind="chapter",
                    book_id=state.book_id or 1,
                    node_id=self.chapter_id,
                    display_path="Chapter 1",
                    start_page=1,
                    end_page=5,
                ),
                state_update=StateUpdate(
                    active_scope="set_active_scope"
                ),
                outline_node_ids=[self.section_id],
                outcome="answer",
            )
        return TurnResult(
            question=question,
            answer="The core idea appears on page 2. [N2:P2]",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query=question,
            scope_behavior="global",
            evidence=[
                EvidenceRef(
                    node_id=self.section_id,
                    pages=[2],
                    path="Chapter 1 :: Core idea",
                    rank=1,
                    chunk_id="chunk-1",
                    retrieval_method="fake",
                )
            ],
            citations=[
                CitationRef(
                    marker=f"[N{self.section_id}:P2]",
                    node_id=self.section_id,
                    page=2,
                )
            ],
            outcome="answer",
        )


class FakeJudge:
    def evaluate(self, **kwargs) -> AnswerQualityJudgment:
        del kwargs
        return AnswerQualityJudgment(
            correctness=3,
            required_point_coverage=3,
            usefulness=4,
            unsupported_claims=[],
            explanation="Semantically aligned.",
        )


class FakeOracleRetriever:
    def __init__(self, section_id: int) -> None:
        self.section_id = section_id

    def invoke(self, turn: dict) -> list[EvidenceRef]:
        if turn["expected_route"] != "retrieval_qa":
            return []
        return [
            EvidenceRef(
                node_id=self.section_id,
                pages=[2],
                path="Chapter 1 :: Core idea",
                rank=1,
                chunk_id="oracle-chunk",
                retrieval_method="fake-oracle",
            )
        ]


class MultiturnEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "books.sqlite3"
        with connect(self.database_path) as connection:
            initialize(connection)
            self.book_id = ingest_book(
                connection,
                sample_book(),
                title="Sample",
                author="Test Author",
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
            rows = connection.execute(
                "SELECT id, node_type FROM nodes ORDER BY toc_index"
            ).fetchall()
            self.chapter_id = rows[0]["id"]
            self.section_id = rows[1]["id"]
        self.gold = {
            "set_id": "sample-multiturn-v1",
            "book": {
                "database_book_id": self.book_id,
                "title": "Sample",
                "source_file_sha256": FILE_HASH,
            },
            "conversations": [
                {
                    "id": "sample-001",
                    "title": "Hierarchy then follow-up",
                    "category": "test",
                    "tags": ["hierarchy", "dependency"],
                    "turns": [
                        {
                            "turn_id": "sample-001-t1",
                            "user": "List sections in Chapter 1.",
                            "expected_route": "hierarchy_list",
                            "history_dependency": "independent",
                            "expected_standalone_query": (
                                "List sections in Chapter 1."
                            ),
                            "scope_behavior": "hard_filter",
                            "expected_scope": {
                                "kind": "chapter",
                                "node_id": self.chapter_id,
                            },
                            "state_update": "set_active_scope",
                            "answerable": True,
                            "expected_outline_node_ids": [self.section_id],
                            "expected_evidence": [],
                            "near_miss_evidence": [],
                            "reference_answer": "Chapter 1 contains Core idea.",
                        },
                        {
                            "turn_id": "sample-001-t2",
                            "depends_on_turn_ids": ["sample-001-t1"],
                            "user": "Explain that section.",
                            "expected_route": "retrieval_qa",
                            "history_dependency": "dependent",
                            "expected_standalone_query": (
                                "Explain Core idea in Chapter 1."
                            ),
                            "scope_behavior": "prefer_scope",
                            "expected_scope": {
                                "kind": "chapter",
                                "node_id": self.chapter_id,
                            },
                            "state_update": "retain",
                            "answerable": True,
                            "expected_evidence": [
                                {
                                    "node_id": self.section_id,
                                    "pages": [2],
                                    "role": "required",
                                }
                            ],
                            "near_miss_evidence": [],
                            "reference_answer": (
                                f"Core idea is on page 2 "
                                f"[N{self.section_id}:P2]."
                            ),
                        },
                    ],
                }
            ],
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def evaluate(self, *, scored_turn_ids=None):
        executor = FakeExecutor(self.chapter_id, self.section_id)
        result = evaluate_conversations(
            self.gold,
            executor=executor,
            source_path=self.database_path,
            scored_turn_ids=scored_turn_ids,
            answer_judge=FakeJudge(),
            oracle_retriever=FakeOracleRetriever(self.section_id),
        )
        result["run"] = {
            "run_id": "test-run",
            "generated_at": "2026-07-20T00:00:00+00:00",
            "system": "fake",
            "models": {
                "generation": "fake-generation",
                "control": "fake-control",
                "control_reasoning": "none",
            },
            "langsmith_project": "test",
        }
        return executor, result

    def test_replay_scores_components_and_carries_explicit_state(self):
        executor, result = self.evaluate()

        summary = result["summary"]
        self.assertEqual(summary["scored_turn_count"], 2)
        self.assertEqual(summary["route_accuracy"], 1.0)
        self.assertEqual(summary["history_dependency_accuracy"], 0.5)
        self.assertEqual(summary["scope_accuracy"], 0.5)
        self.assertEqual(summary["state_update_accuracy"], 0.5)
        self.assertEqual(summary["mean_required_evidence_recall"], 1.0)
        self.assertEqual(
            summary["oracle_query_retrieval"][
                "mean_required_evidence_recall"
            ],
            1.0,
        )
        self.assertEqual(summary["safety_violation_count"], 0)
        self.assertEqual(
            executor.seen_states[1].active_scope.node_id,
            self.chapter_id,
        )

    def test_single_turn_selection_replays_prior_turn_as_setup(self):
        _, result = self.evaluate(
            scored_turn_ids={"sample-001-t2"},
        )

        turns = result["conversations"][0]["turns"]
        self.assertEqual([turn["scored"] for turn in turns], [False, True])
        self.assertEqual(result["summary"]["scored_turn_count"], 1)
        self.assertEqual(
            turns[1]["state_before"]["active_scope"]["node_id"],
            self.chapter_id,
        )

    def test_html_contains_gold_prediction_evidence_and_state(self):
        _, result = self.evaluate()

        rendered = build_html(result)

        self.assertIn("Multi-turn baseline report", rendered)
        self.assertIn("Reference response", rendered)
        self.assertIn("Generated response", rendered)
        self.assertIn("Expected evidence", rendered)
        self.assertIn("State snapshots", rendered)
        self.assertIn("sample-001-t2", rendered)
        self.assertIn("<strong>Chapter", build_html({
            **result,
            "conversations": [
                {
                    **result["conversations"][0],
                    "turns": [
                        {
                            **result["conversations"][0]["turns"][0],
                            "gold": {
                                **result["conversations"][0]["turns"][0]["gold"],
                                "reference_answer": "**Chapter** reference",
                            },
                        }
                    ],
                }
            ],
        }))

    def test_result_is_json_serializable(self):
        _, result = self.evaluate()

        serialized = json.dumps(result)

        self.assertIn("sample-multiturn-v1", serialized)

    def test_progress_callback_receives_each_replayed_turn(self):
        executor = FakeExecutor(self.chapter_id, self.section_id)
        completed = []

        evaluate_conversations(
            self.gold,
            executor=executor,
            source_path=self.database_path,
            on_turn_complete=completed.append,
        )

        self.assertEqual(
            [turn["turn_id"] for turn in completed],
            ["sample-001-t1", "sample-001-t2"],
        )

    def test_cli_accepts_repeated_conversation_selection(self):
        args = build_argument_parser().parse_args(
            [
                "--conversation",
                "sample-001",
                "--conversation",
                "sample-002",
            ]
        )

        self.assertEqual(
            args.conversation,
            ["sample-001", "sample-002"],
        )

    def test_turn_selection_replays_only_required_prefix(self):
        args = build_argument_parser().parse_args(
            ["--turn", "sample-001-t2"]
        )
        scored, conversations, _ = _selection(self.gold, args)

        plan = _execution_plan(
            self.gold,
            scored_turn_ids=scored,
            conversation_ids=conversations,
        )

        self.assertEqual(plan["replayed_turn_count"], 2)
        self.assertEqual(plan["scored_turn_count"], 1)
        self.assertEqual(plan["generation_calls_upper_bound"], 1)
        self.assertEqual(plan["control_judge_calls"], 1)
        self.assertEqual(plan["estimated_openrouter_calls_upper_bound"], 2)

    def test_live_environment_requires_tracing_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                ValueError,
                "OPENROUTER_API_KEY",
            ):
                _validate_live_environment()

        configured = {
            "OPENROUTER_API_KEY": "not-a-real-key",
            "LANGSMITH_API_KEY": "not-a-real-key",
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_PROJECT": "test-project",
        }
        with patch.dict(os.environ, configured, clear=True):
            environment = _validate_live_environment()

        self.assertEqual(environment["project"], "test-project")
        self.assertEqual(
            environment["generation_model"],
            "openai/gpt-5.6-luna",
        )


if __name__ == "__main__":
    unittest.main()
