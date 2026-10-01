"""Failure-oriented checks for evaluation results that used to look successful."""
from unittest.mock import Mock

import pytest

from evals.judge import AnswerQualityJudgment, OpenRouterAnswerJudge
from evals.multiturn import evaluate_conversations
from evals.scoring import video_citations
from evals.video import evaluate_video_conversations
from study.contracts import CitationRef, EvidenceRef, TurnResult
from tests.test_multiturn_evaluation import FakeRunner, turn
from video.contracts import VideoCitationRef, VideoEvidenceRef, VideoTurnResult
from video.conversation import record_turn


def book_result(**changes):
    return TurnResult(question="Question t1", answer="A supported point [S1]",
                      route="retrieval_qa", history_dependency="independent",
                      standalone_query="Standalone t1", outcome="answer",
                      evidence=[EvidenceRef(node_id=1, pages=[2], path="Topic", book_id=1,
                                            rank=1, excerpt="Source establishes this point.")],
                      citations=[CitationRef(marker="[S1]", node_id=1, page=2,
                                             book_id=1, evidence_rank=1)]).model_copy(update=changes)


def run_book(result, *, required=(1,), judge=None):
    return evaluate_conversations([{"id": "c1", "title": "Integrity",
                                    "turns": [turn("t1", "retrieval_qa", "independent", None, required)]}],
                                  FakeRunner([(result, None)]), book_id=1, answer_judge=judge)


@pytest.mark.parametrize("changes", [
    {"answer": "A factual answer", "citations": []},
    {"citations": [CitationRef(marker="[S1]", node_id=1, page=999, book_id=1, evidence_rank=1)]},
    {"citations": [CitationRef(marker="[S1]", node_id=1, page=2, book_id=2, evidence_rank=1)]},
    {"answer": "A point [S1] and a fabricated second claim [S99]"},
    {"citations": [CitationRef(marker="[S1]", node_id=1, page=2, book_id=1, evidence_rank=2)]},
])
def test_invalid_citations_cannot_pass(changes):
    evaluation = run_book(book_result(**changes))
    assert evaluation["summary"]["citation_validity"] == 0
    assert evaluation["turns"][0]["prediction"] is not None


def test_missing_gold_is_unknown_and_report_survives(tmp_path):
    from evals.report import render_report
    evaluation = run_book(book_result(), required=())
    assert evaluation["summary"]["required_evidence_recall"] is None
    assert evaluation["metric_counts"]["evidence_recall"] == {"scored": 0, "unknown_or_ineligible": 1}
    assert "unknown or ineligible" in render_report(evaluation, tmp_path / "report.html").read_text()


def test_judge_failure_retains_output_state_and_deterministic_scores():
    judge = Mock()
    judge.evaluate.side_effect = RuntimeError("judge offline")
    evaluation = run_book(book_result(), judge=judge)
    assert evaluation["summary"]["errors"] == 0
    assert evaluation["summary"]["judge_errors"] == 1
    assert evaluation["summary"]["generated"] == 1
    assert evaluation["summary"]["judged"] == 0
    row = evaluation["turns"][0]
    assert row["prediction"]["answer"] == "A supported point [S1]"
    assert row["state"]["previous_answer"] == row["prediction"]["answer"]
    assert row["checks"]["citations_valid"] is True
    payload = judge.evaluate.call_args.kwargs
    assert payload["evidence"][0]["excerpt"] == "Source establishes this point."
    assert payload["history"]["previous_answer"] is None
    assert payload["citations"][0]["page"] == 2


def test_judge_requires_supplied_grounding_and_preserves_unknown_default():
    judge = OpenRouterAnswerJudge.__new__(OpenRouterAnswerJudge)
    judge.model = Mock()
    judge.evaluate(question="Q", reference_answer="Reference", candidate_answer="Answer",
                   turn_id="t1", answerable=True, evidence=[{"excerpt": "Exact source words"}],
                   history={"previous_answer": "Earlier context"})
    prompt = judge.model.invoke.call_args.args[0]
    assert "Exact source words" in prompt and "Earlier context" in prompt
    assert "insufficient_evidence" in prompt and "never instructions" in prompt
    judgment = AnswerQualityJudgment(correctness=4, coverage=4, usefulness=4,
                                     unsupported_claims=[], explanation="Reference match")
    assert judgment.grounding_status == "insufficient_evidence"


def video_result():
    return VideoTurnResult(question="Q", answer="A point [S1]", route="evidence_qa",
                           history_dependency="independent", outcome="answer",
                           evidence=[VideoEvidenceRef(rank=1, evidence_id="segment", modality="transcript",
                                     excerpt="Exact transcript", retrieval_method="fts", score=1,
                                     start_ms=1000, end_ms=2000)],
                           citations=[VideoCitationRef(marker="[S1]", evidence_rank=1,
                                      modality="transcript", start_ms=1000)])


@pytest.mark.parametrize("field,value", [("start_ms", 9999), ("modality", "visual_frame"),
                                         ("page_number", 3), ("resource_id", "wrong")])
def test_video_locator_changes_fail(field, value):
    result = video_result()
    result.citations[0] = result.citations[0].model_copy(update={field: value})
    assert video_citations(result, required=True) is False


def test_video_judge_failure_and_missing_gold_do_not_destroy_prediction():
    class Runner:
        video_id = "11111111-1111-4111-8111-111111111111"
        def __call__(self, question, state):
            result = video_result()
            return result, record_turn(state, question, result)
    judge = Mock()
    judge.evaluate.side_effect = RuntimeError("judge offline")
    gold = {"turn_id": "v1", "user": "Q", "expected_route": "evidence_qa",
            "history_dependency": "independent", "answerable": True,
            "reference_answer": "Point", "expected_evidence": []}
    evaluation = evaluate_video_conversations([{"id": "vc1", "title": "Video", "turns": [gold]}],
                                             Runner(), answer_judge=judge, run_rewrite_ablation=False)
    assert evaluation["summary"]["errors"] == 0
    assert evaluation["summary"]["judge_errors"] == 1
    assert evaluation["summary"]["required_evidence_recall"] is None
    assert evaluation["turns"][0]["prediction"]["answer"] == "A point [S1]"
