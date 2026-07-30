import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.interview import (
    ProjectInterviewRunner,
    evaluate_interview_cases,
    rejudge_interview_evaluation,
    score_case,
)
from evals.interview_dataset import load_interview_dataset
from evals.interview_report import render_interview_report
from evals.judge import InterviewAnswerJudgment
from study.contracts import (
    CitationRef,
    ConversationState,
    EvidenceRef,
    TurnResult,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = load_interview_dataset(ROOT / "evaluation" / "interview_answer_seed.json")


class FakeRunner:
    def __init__(self, results):
        self.results = iter(results)
        self.book_ids = []

    def __call__(self, case, *, book_id):
        self.book_ids.append(book_id)
        result = next(self.results)
        return result, ConversationState(
            conversation_id=f"eval-{case.id}",
            book_ids=[book_id],
        )


class FakeJudge:
    def evaluate(self, **values):
        return InterviewAnswerJudgment(
            grounded_correctness=4,
            interview_readiness=3,
            coverage=4,
            depth_adherence=4,
            clarity_memorability=3,
            follow_up_quality=3,
            citation_quality=4,
            must_cover_results=[
                {
                    "criterion": criterion,
                    "met": True,
                    "explanation": "Covered.",
                }
                for criterion in values["case"]["must_cover"]
            ],
            failure_guard_violations=[],
            unsupported_claims=[],
            explanation="Useful grounded answer.",
        )


def answer_for(case):
    evidence = [
        EvidenceRef(
            node_id=anchor.node_id,
            pages=anchor.pages,
            path=anchor.path,
        )
        for anchor in case.candidate_evidence
    ]
    first = evidence[0]
    return TurnResult(
        question=case.prompt,
        answer=f"Grounded answer [N{first.node_id}:P{first.pages[0]}]",
        route=case.expected_route,
        history_dependency="independent",
        standalone_query=case.prompt,
        evidence=evidence,
        citations=[
            CitationRef(
                marker=f"[N{first.node_id}:P{first.pages[0]}]",
                node_id=first.node_id,
                page=first.pages[0],
            )
        ],
        outcome="answer",
        answer_archetype=case.expected_archetype,
        response_depth=case.expected_depth,
        prompt_profile_version="interview-v1:test",
    )


class InterviewEvaluationTests(unittest.TestCase):
    def test_runner_hydrates_full_chunks_for_answer_judging(self):
        result = TurnResult(
            question="Explain the idea.",
            answer="Grounded answer. [S1]",
            route="retrieval_qa",
            history_dependency="independent",
            outcome="answer",
            evidence=[
                EvidenceRef(
                    node_id=1,
                    pages=[2],
                    path="Chapter 1 :: Idea",
                    chunk_id="chunk-1",
                    excerpt="Short reader preview.",
                )
            ],
        )
        runner = ProjectInterviewRunner(
            owner_id="00000000-0000-0000-0000-000000000001"
        )
        with patch("evals.interview.database_connection") as connect:
            source = connect.return_value.__enter__.return_value
            source.execute.return_value.fetchall.return_value = [
                {"id": "chunk-1", "text": "Complete evidence shown to generation."}
            ]
            hydrated = runner._hydrate_retrieval_evidence(result)

        self.assertEqual(
            hydrated.evidence[0].excerpt,
            "Complete evidence shown to generation.",
        )
        self.assertEqual(result.evidence[0].excerpt, "Short reader preview.")

    def test_scores_routing_depth_archetype_evidence_and_judge(self):
        case = next(item for item in DATASET.cases if item.id == "int-001")
        runner = FakeRunner([answer_for(case)])

        evaluation = evaluate_interview_cases(
            DATASET,
            [case],
            runner,
            book_ids={"islp": 530},
            answer_judge=FakeJudge(),
        )

        summary = evaluation["summary"]
        self.assertEqual(summary["route_accuracy"], 1.0)
        self.assertEqual(summary["depth_accuracy"], 1.0)
        self.assertEqual(summary["archetype_accuracy"], 1.0)
        self.assertEqual(summary["required_evidence_recall"], 1.0)
        self.assertEqual(summary["citation_validity"], 1.0)
        self.assertEqual(summary["judge"]["judged_cases"], 1)
        self.assertEqual(runner.book_ids, [530])

    def test_unanswerable_case_scores_abstention_without_evidence_recall(self):
        case = next(item for item in DATASET.cases if item.id == "int-010")
        result = TurnResult(
            question=case.prompt,
            answer="The selected book cannot establish the latest release default.",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query=case.prompt,
            outcome="abstain",
            answer_archetype="concept_explanation",
            response_depth="interview",
            prompt_profile_version="interview-v1:test",
        )

        evaluation = evaluate_interview_cases(
            DATASET,
            [case],
            FakeRunner([result]),
            book_ids={"islp": 530},
        )

        checks = evaluation["cases"][0]["checks"]
        self.assertTrue(checks["outcome"])
        self.assertTrue(checks["abstention_has_no_citations"])
        self.assertNotIn("evidence_recall", checks)

    def test_answer_prefacing_itself_with_evidence_is_flagged(self):
        case = next(item for item in DATASET.cases if item.id == "int-001")
        result = answer_for(case).model_copy(
            update={"answer": "Here is an explanation based on provided evidence."}
        )

        checks = score_case(case, result)

        self.assertFalse(checks["avoids_evidence_preface"])

    def test_parent_anchor_is_covered_by_retrieved_descendant(self):
        case = next(item for item in DATASET.cases if item.id == "int-026")
        evidence = []
        for anchor in case.candidate_evidence:
            if anchor.node_id == 2848:
                evidence.append(
                    EvidenceRef(
                        node_id=999_001,
                        pages=[244],
                        path=anchor.path + " :: Content-bearing child",
                    )
                )
            else:
                evidence.append(
                    EvidenceRef(
                        node_id=anchor.node_id,
                        pages=anchor.pages,
                        path=anchor.path,
                    )
                )
        result = TurnResult(
            question=case.prompt,
            answer="Grounded chapter review. [N2843:P235]",
            route="hierarchy_summary",
            history_dependency="independent",
            evidence=evidence,
            citations=[
                CitationRef(
                    marker="[N2843:P235]",
                    node_id=2843,
                    page=235,
                )
            ],
            outcome="answer",
            answer_archetype="chapter_review",
            response_depth="deep",
            prompt_profile_version="interview-v2:test",
        )

        self.assertEqual(score_case(case, result)["evidence_recall"], 1.0)

    def test_saved_results_can_be_rejudged_without_rerunning_generation(self):
        case = next(item for item in DATASET.cases if item.id == "int-001")
        evaluation = evaluate_interview_cases(
            DATASET,
            [case],
            FakeRunner([answer_for(case)]),
            book_ids={"islp": 530},
        )

        rejudged = rejudge_interview_evaluation(evaluation, FakeJudge())

        self.assertEqual(rejudged["summary"]["judge"]["judged_cases"], 1)
        self.assertEqual(
            rejudged["cases"][0]["answer_judgment"]["grounded_correctness"],
            4,
        )

    def test_resume_rows_are_kept_and_not_executed_again(self):
        first = next(item for item in DATASET.cases if item.id == "int-001")
        second = next(item for item in DATASET.cases if item.id == "int-002")
        initial = evaluate_interview_cases(
            DATASET,
            [first],
            FakeRunner([answer_for(first)]),
            book_ids={"islp": 530},
        )["cases"]
        runner = FakeRunner([answer_for(second)])
        checkpoints = []

        resumed = evaluate_interview_cases(
            DATASET,
            [first, second],
            runner,
            book_ids={"islp": 530},
            initial_rows=initial,
            on_checkpoint=checkpoints.append,
        )

        self.assertEqual(
            [row["case_id"] for row in resumed["cases"]],
            [
                "int-001",
                "int-002",
            ],
        )
        self.assertEqual(runner.book_ids, [530])
        self.assertEqual(checkpoints[-1]["summary"]["cases"], 2)

    def test_execution_errors_are_recorded_without_stopping_the_run(self):
        class BrokenRunner:
            def __call__(self, case, *, book_id):
                raise RuntimeError(f"provider unavailable for {case.id}/{book_id}")

        case = next(item for item in DATASET.cases if item.id == "int-001")
        evaluation = evaluate_interview_cases(
            DATASET,
            [case],
            BrokenRunner(),
            book_ids={"islp": 530},
        )

        self.assertEqual(evaluation["summary"]["errors"], 1)
        self.assertFalse(evaluation["cases"][0]["checks"]["outcome"])

    def test_case_deadline_records_timeout_and_continues(self):
        class SlowRunner:
            def __call__(self, case, *, book_id):
                time.sleep(0.05)

        case = next(item for item in DATASET.cases if item.id == "int-001")
        evaluation = evaluate_interview_cases(
            DATASET,
            [case],
            SlowRunner(),
            book_ids={"islp": 530},
            case_timeout_seconds=0.01,
        )

        self.assertEqual(evaluation["summary"]["errors"], 1)
        self.assertIn("evaluation deadline", evaluation["cases"][0]["error"])

    def test_report_is_filterable_and_escapes_case_content(self):
        case = next(item for item in DATASET.cases if item.id == "int-001")
        evaluation = evaluate_interview_cases(
            DATASET,
            [case],
            FakeRunner([answer_for(case)]),
            book_ids={"islp": 530},
        )
        evaluation["cases"][0]["case"]["title"] = "<unsafe>"

        with tempfile.TemporaryDirectory() as directory:
            path = render_interview_report(
                evaluation,
                Path(directory) / "report.html",
            )
            html = path.read_text(encoding="utf-8")

        self.assertIn('id="search"', html)
        self.assertIn('id="book"', html)
        self.assertIn("&lt;unsafe&gt;", html)
        self.assertNotIn("<unsafe>", html)


if __name__ == "__main__":
    unittest.main()
