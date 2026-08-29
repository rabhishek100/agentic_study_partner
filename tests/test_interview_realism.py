"""Research-backed interview-sequence policy and evaluation gates."""

from pathlib import Path
from unittest.mock import patch

import pytest

from evals.interview_realism import (
    design_opening_invites_clarification,
    duration_question_range,
    evaluate_interview_realism,
    load_interview_realism_dataset,
    score_interview_sequence,
)
from interviews.contracts import InterviewQuestion
from interviews.evaluation import InterviewValidationError
from interviews.question_generation import validate_question_focus
from interviews.realism import (
    design_question_target,
    infer_interview_track,
    minimum_design_moves,
    planned_interview_move,
    topic_for_design_move,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = load_interview_realism_dataset(
    ROOT / "evaluation" / "interview_realism_seed.json"
)


def _question(case, index: int) -> InterviewQuestion:
    item = case.topics[index]
    move = case.expected_moves[index]
    templates = {
        "fundamentals": f"How does {item.label} affect an engineering decision?",
        "mechanism": f"How does {item.label} work in this system?",
        "application": f"When would you apply {item.label} in production?",
        "requirements": (
            f"You are designing {case.scope_title}. What would you clarify "
            "before proposing architecture?"
        ),
        "architecture": f"How would you design {item.label} for this system?",
        "tradeoff": f"What trade-off matters most when choosing {item.label}?",
        "diagnosis": f"How would you diagnose a failure involving {item.label}?",
        "evaluation": f"How would you validate {item.label} before launch?",
    }
    return InterviewQuestion(
        topic_key=item.key,
        topic_label=item.label,
        text=templates[move],
        expected_points=["Give one relevant technical decision."],
        suggested_answer=f"{item.evidence} {item.marker}",
        citation_markers=[item.marker],
        difficulty=case.target_level,
    )


def test_dataset_spans_real_role_families_and_public_sources() -> None:
    roles = {case.role for case in DATASET.cases}
    source_hosts = {source.url.split("/")[2] for source in DATASET.sources}

    assert {
        "machine learning engineer",
        "AI engineer",
        "software engineer",
        "senior machine learning engineer",
        "senior software engineer",
    } <= roles
    assert {"openai.com", "amazon.jobs", "news.microsoft.com", "github.com"} <= source_hosts


@pytest.mark.parametrize("case", DATASET.cases, ids=lambda case: case.id)
def test_frozen_expected_moves_match_runtime_progression(case) -> None:
    inventory = case.inventory()
    actual = [
        planned_interview_move(
            inventory,
            interview_format=case.interview_format,
            target_level=case.target_level,
            areas_visited=index,
            planned_area_count=len(case.topics),
        ).move
        for index in range(len(case.topics))
    ]

    assert actual == case.expected_moves


def test_track_detection_distinguishes_ai_from_software_systems() -> None:
    ml_case = next(case for case in DATASET.cases if case.id == "real-ai-mid")
    swe_case = next(
        case for case in DATASET.cases if case.id == "real-swe-system-senior"
    )

    assert infer_interview_track(ml_case.inventory()) == "machine_learning_ai"
    assert infer_interview_track(swe_case.inventory()) == "software_engineering"


def test_system_design_opening_invites_candidate_clarification() -> None:
    case = next(item for item in DATASET.cases if item.interview_format == "system_design")

    assert design_opening_invites_clarification(_question(case, 0))


def test_thirty_minute_design_has_a_five_phase_floor() -> None:
    assert minimum_design_moves(15) == 3
    assert minimum_design_moves(30) == 5
    assert design_question_target(
        planned_area_count=2,
        maximum_duration_minutes=30,
    ) == 5


def test_rubric_has_role_specific_duration_ranges() -> None:
    concept = next(item for item in DATASET.cases if item.interview_format == "concept")
    design = next(item for item in DATASET.cases if item.interview_format == "system_design")

    assert duration_question_range(concept)[0] == 3
    assert duration_question_range(design)[0] == 5


def test_large_design_scope_does_not_repeat_the_opening_requirements_move() -> None:
    case = next(item for item in DATASET.cases if item.interview_format == "system_design")
    moves = [
        planned_interview_move(
            case.inventory(),
            interview_format="system_design",
            target_level="senior",
            areas_visited=index,
            planned_area_count=7,
        ).move
        for index in range(7)
    ]

    assert moves == [
        "requirements",
        "architecture",
        "tradeoff",
        "tradeoff",
        "diagnosis",
        "evaluation",
        "evaluation",
    ]


def test_design_phase_reuse_requires_source_support() -> None:
    case = next(item for item in DATASET.cases if item.id == "real-swe-system-senior")
    inventory = case.inventory()

    selected = topic_for_design_move(inventory, move="diagnosis")

    assert selected is not None
    assert "fail" in selected.evidence_text.casefold() or "outage" in selected.evidence_text.casefold()


def test_judge_failure_is_reported_without_losing_the_evaluation_artifact() -> None:
    case = DATASET.cases[0]

    class FailingJudge:
        def evaluate(self, **_values):
            raise ValueError("truncated judgment")

    questions = [_question(case, index) for index in range(len(case.topics))]
    with patch(
        "evals.interview_realism.generate_case_questions",
        return_value=(questions, 0.0),
    ):
        result = evaluate_interview_realism(
            DATASET,
            [case],
            judge=FailingJudge(),
        )

    assert result["cases"][0]["semantic_judge_error"] == "truncated judgment"
    assert result["summary"]["pass_rate"] == 0.0


@pytest.mark.parametrize("case", DATASET.cases, ids=lambda case: case.id)
def test_research_profile_sequences_pass_deterministic_realism_gates(case) -> None:
    result = score_interview_sequence(
        case,
        [_question(case, index) for index in range(len(case.topics))],
    )

    assert result["hard_pass"]
    assert result["quality_pass"]
    assert result["passed"]


def test_book_dependent_question_is_a_hard_failure() -> None:
    case = DATASET.cases[0]
    leaked = _question(case, 0).model_copy(
        update={"text": "According to the chapter, what is bias and variance?"}
    )

    with pytest.raises(InterviewValidationError, match="source recall|study source"):
        validate_question_focus(leaked)

    result = score_interview_sequence(
        case,
        [leaked, *[_question(case, index) for index in range(1, len(case.topics))]],
    )
    assert result["summary"]["source_independence_rate"] < 1.0
    assert not result["hard_pass"]


def test_repeated_fallback_shape_fails_sequence_quality() -> None:
    case = DATASET.cases[0]
    questions = [
        _question(case, index).model_copy(
            update={
                "text": f"How would you use {item.label} in a practical system?",
                "interviewer_note": "Deterministic continuity fallback.",
            }
        )
        for index, item in enumerate(case.topics)
    ]

    result = score_interview_sequence(case, questions)

    assert result["summary"]["fallback_rate"] == 1.0
    assert result["summary"]["near_duplicate_question_shapes"] > 0
    assert not result["quality_pass"]
    assert not result["generation_robustness_pass"]
