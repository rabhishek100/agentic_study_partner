"""Frozen candidate-profile calibration dataset and scoring gates."""

from pathlib import Path

from evals.interview_candidate_calibration import (
    evaluate_candidate_calibration,
    load_candidate_calibration_dataset,
)
from interviews.contracts import AnswerEvaluation, ScoreCard


ROOT = Path(__file__).resolve().parents[1]
DATASET = load_candidate_calibration_dataset(
    ROOT / "evaluation" / "interview_candidate_profiles.json"
)


class _LevelAwareEvaluationModel:
    def invoke(self, messages):
        prompt = messages[-1].content
        if "Candidate answer:\nLow latency." in prompt or (
            "Candidate answer:\nI would use a vector database." in prompt
        ):
            evaluation = _evaluation(
                score=2,
                complete=False,
                clarify=True,
            )
        elif "Candidate answer:\nFirst I'd clarify" in prompt or (
            "Candidate answer:\nI'd clarify the document types" in prompt
        ):
            evaluation = _evaluation(score=4, complete=True, depth=True)
        else:
            evaluation = _evaluation(score=5, complete=True)
        return {"parsed": evaluation, "raw": None}


def _evaluation(
    *,
    score: int,
    complete: bool,
    clarify: bool = False,
    depth: bool = False,
) -> AnswerEvaluation:
    marker = "[N29293:P10]"
    return AnswerEvaluation(
        classification="source_aligned" if complete else "partially_correct",
        scores=ScoreCard(
            technical_correctness=score,
            depth_completeness=score,
            reasoning_structure=score,
            tradeoff_awareness=score,
            communication_clarity=score,
            independence=score,
        ),
        strengths=["The response addresses part of the question."],
        gaps=[] if complete else ["Most requirements remain unstated."],
        concise_feedback="Thank you.",
        recommended_answer=f"A grounded answer {marker}",
        citation_markers=[marker],
        question_complete=complete,
        needs_clarifying_probe=clarify,
        clarifying_probe="Which latency target do you need?" if clarify else None,
        needs_depth_follow_up=depth,
        depth_follow_up_focus="failure behavior" if depth else None,
        topic_complete=complete and not depth,
    )


def test_dataset_has_two_complete_human_authored_ladders() -> None:
    assert DATASET.review_status == "human_authored_frozen"
    assert len(DATASET.scenarios) == 2
    assert len(DATASET.profiles) == 6

    for scenario in DATASET.scenarios:
        assert {profile.level for profile in DATASET.profiles if profile.scenario_id == scenario.id} == {
            "weak",
            "mixed",
            "strong",
        }


def test_calibration_uses_loose_gates_and_monotonic_ordering() -> None:
    evaluation = evaluate_candidate_calibration(
        DATASET,
        DATASET.profiles,
        model=_LevelAwareEvaluationModel(),
    )

    assert evaluation["summary"]["passed"] is True
    assert evaluation["summary"]["profile_pass_rate"] == 1.0
    assert evaluation["summary"]["monotonic_passes"] == 2


class _FailingModel:
    def invoke(self, _messages):
        raise TimeoutError("provider unavailable")


def test_provider_failure_is_recorded_without_aborting_the_run() -> None:
    evaluation = evaluate_candidate_calibration(
        DATASET,
        DATASET.profiles[:1],
        model=_FailingModel(),
    )

    assert evaluation["summary"]["provider_failures"] == 1
    assert "provider_error" in evaluation["profiles"][0]
