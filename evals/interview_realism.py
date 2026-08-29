"""Source-backed evaluation of candidate-facing interview question sequences."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, model_validator

from decks.topics import ScopeInventory, Topic
from interviews.contracts import (
    ContractModel,
    InterviewFormat,
    InterviewQuestion,
    TargetLevel,
)
from interviews.evaluation import InterviewValidationError, validate_question
from interviews.question_generation import (
    generate_question,
    validate_question_focus,
    validate_question_progression,
)
from interviews.realism import (
    InterviewMove,
    interview_move_purpose,
    planned_interview_move,
    question_is_source_dependent,
)


class RealismSource(ContractModel):
    title: str
    url: str
    supports: list[str] = Field(min_length=1)


class RealismTopic(ContractModel):
    key: str
    label: str
    evidence: str
    marker: str


class InterviewRealismCase(ContractModel):
    id: str
    role: str
    source_title: str
    scope_title: str
    interview_format: InterviewFormat
    target_level: TargetLevel
    maximum_duration_minutes: int = Field(default=30, ge=15, le=120)
    topics: list[RealismTopic] = Field(min_length=3)
    expected_moves: list[InterviewMove]

    @model_validator(mode="after")
    def one_move_per_topic(self) -> "InterviewRealismCase":
        if len(self.expected_moves) != len(self.topics):
            raise ValueError("expected_moves must contain one move per topic")
        if len({topic.key for topic in self.topics}) != len(self.topics):
            raise ValueError("topic keys must be unique within a case")
        return self

    def inventory(self) -> ScopeInventory:
        topics = tuple(
            Topic(
                key=item.key,
                ordinal=index,
                label=item.label,
                required=True,
                evidence_text=f"{item.marker}\n{item.evidence}",
                allowed_markers=frozenset({item.marker}),
            )
            for index, item in enumerate(self.topics)
        )
        return ScopeInventory(
            source_kind="book",
            scope_key=f"realism:{self.id}",
            title=self.scope_title,
            source_title=self.source_title,
            outline="\n".join(f"- {topic.label}" for topic in topics),
            topics=topics,
        )


class InterviewRealismDataset(ContractModel):
    dataset_id: str
    version: int = Field(ge=1)
    review_status: str
    methodology: str
    sources: list[RealismSource] = Field(min_length=3)
    cases: list[InterviewRealismCase] = Field(min_length=4)


class InterviewSequenceJudgment(ContractModel):
    realism: int = Field(ge=0, le=4)
    progression: int = Field(ge=0, le=4)
    coherence: int = Field(ge=0, le=4)
    pacing: int = Field(ge=0, le=4)
    role_relevance: int = Field(ge=0, le=4)
    source_independence: int = Field(ge=0, le=4)
    interview_signal: int = Field(ge=0, le=4)
    violations: list[str] = Field(max_length=5)
    explanation: str = Field(max_length=1_200)


class SequenceJudge(Protocol):
    def evaluate(self, **values) -> InterviewSequenceJudgment: ...


SEMANTIC_DIMENSIONS = (
    "realism",
    "progression",
    "coherence",
    "pacing",
    "role_relevance",
    "source_independence",
    "interview_signal",
)


MOVE_PATTERNS: dict[InterviewMove, re.Pattern[str]] = {
    "fundamentals": re.compile(
        r"\b(?:what (?:is|are|do|does)|explain|distinguish|compare|why (?:is|does))\b",
        re.IGNORECASE,
    ),
    "mechanism": re.compile(
        r"\b(?:how|why)\b.{0,100}\b(?:work|use|map|find|handle|make|produce|"
        r"cause|affect|influence|lead|happen|change|improve|reduce)\w*\b",
        re.IGNORECASE,
    ),
    "application": re.compile(
        r"\b(?:given|in practice|practical|production|scenario|when would|"
        r"would you|choose|apply|use)\b",
        re.IGNORECASE,
    ),
    "requirements": re.compile(
        r"\b(?:assumption|constraint|goal|requirement|scope|slo|objective)\w*\b",
        re.IGNORECASE,
    ),
    "architecture": re.compile(
        r"\b(?:architect|component|data flow|design|interface|pipeline|service|"
        r"storage|system)\w*\b",
        re.IGNORECASE,
    ),
    "tradeoff": re.compile(
        r"\b(?:trade[ -]?off|versus|vs\.?|balance|cost|latency|throughput|"
        r"accuracy|precision|recall|choose between)\b",
        re.IGNORECASE,
    ),
    "diagnosis": re.compile(
        r"\b(?:debug|diagnos|drift|edge case|fail|incident|regress|bottleneck|"
        r"degrad|goes wrong|unexpected)\w*\b",
        re.IGNORECASE,
    ),
    "evaluation": re.compile(
        r"\b(?:evaluate|experiment|metric|monitor|test|validate|verification|"
        r"measure|success)\w*\b",
        re.IGNORECASE,
    ),
}

MOVE_COMPATIBILITY: dict[InterviewMove, frozenset[InterviewMove]] = {
    "fundamentals": frozenset({"fundamentals", "mechanism"}),
    "mechanism": frozenset({"mechanism", "fundamentals"}),
    "application": frozenset({"application", "architecture"}),
    "requirements": frozenset({"requirements", "application"}),
    "architecture": frozenset({"architecture", "application"}),
    "tradeoff": frozenset({"tradeoff", "application"}),
    "diagnosis": frozenset({"diagnosis", "evaluation"}),
    "evaluation": frozenset({"evaluation", "diagnosis"}),
}

DESIGN_OPENING = re.compile(
    r"\b(?:clarif\w*|requirement\w*|constraint\w*|assumption\w*)\b"
    r".{0,120}\b(?:architect\w*|design\w*|system|service)\b|"
    r"\b(?:architect\w*|design\w*|system|service)\b"
    r".{0,120}\b(?:clarif\w*|requirement\w*|constraint\w*|assumption\w*)\b",
    re.IGNORECASE,
)


def load_interview_realism_dataset(path: str | Path) -> InterviewRealismDataset:
    return InterviewRealismDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def classify_question_moves(question: InterviewQuestion) -> set[InterviewMove]:
    text = " ".join(
        value for value in [question.text, question.work_sample_prompt or ""] if value
    )
    matches = {
        move for move, pattern in MOVE_PATTERNS.items() if pattern.search(text)
    }
    if question.work_sample == "architecture_diagram":
        matches.add("architecture")
    if question.work_sample == "code":
        matches.add("application")
    return matches


def design_opening_invites_clarification(question: InterviewQuestion) -> bool:
    """Whether a system-design opener lets the candidate establish the contract."""

    return bool(DESIGN_OPENING.search(question.text))


def duration_question_range(case: InterviewRealismCase) -> tuple[int, int]:
    """A coarse pacing gate; answer length and follow-ups remain runtime signals."""

    minutes = case.maximum_duration_minutes
    if case.interview_format == "system_design":
        return (
            3 if minutes <= 15 else 5,
            5 if minutes <= 15 else min(10, 5 + minutes // 15),
        )
    return (
        3,
        min(10, max(5, minutes // 5)),
    )


def _question_shape(question: InterviewQuestion) -> str:
    """Normalize away topic words so repeated question templates remain visible."""

    text = question.text.casefold()
    topic_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", question.topic_label.casefold())
        if len(token) > 2
    }
    shape = " ".join(
        "<topic>" if token in topic_tokens else token
        for token in re.findall(r"[a-z0-9]+", text)
    )
    return re.sub(r"(?:<topic>\s*)+", "<topic> ", shape).strip()


def _safe_check(check) -> tuple[bool, str | None]:
    try:
        check()
    except InterviewValidationError as error:
        return False, str(error)
    return True, None


def score_interview_sequence(
    case: InterviewRealismCase,
    questions: list[InterviewQuestion],
) -> dict[str, Any]:
    inventory = case.inventory()
    topics = {topic.key: topic for topic in inventory.topics}
    question_rows: list[dict[str, Any]] = []
    recent: list[InterviewQuestion] = []
    for index, question in enumerate(questions):
        topic = topics.get(question.topic_key)
        focused, focus_error = _safe_check(lambda: validate_question_focus(question))
        def check_grounding() -> None:
            if topic is None:
                raise InterviewValidationError("question uses an unknown topic")
            validate_question(question, topic)

        grounded, grounding_error = _safe_check(check_grounding)
        progressive, progression_error = _safe_check(
            lambda: validate_question_progression(question, recent)
        )
        moves = classify_question_moves(question)
        expected = case.expected_moves[index] if index < len(case.expected_moves) else None
        aligned = bool(
            expected is not None and moves & MOVE_COMPATIBILITY[expected]
        )
        question_rows.append(
            {
                "index": index,
                "topic_key": question.topic_key,
                "text": question.text,
                "expected_move": expected,
                "observed_moves": sorted(moves),
                "checks": {
                    "focused": focused,
                    "grounded": grounded,
                    "source_independent": not question_is_source_dependent(question.text),
                    "non_repeating": progressive,
                    "move_aligned": aligned,
                },
                "errors": [
                    error
                    for error in [focus_error, grounding_error, progression_error]
                    if error
                ],
            }
        )
        recent.append(question)

    def rate(name: str) -> float:
        return (
            sum(bool(row["checks"][name]) for row in question_rows)
            / len(question_rows)
            if question_rows
            else 0.0
        )

    adjacent_work_samples = any(
        left.work_sample != "none" and right.work_sample != "none"
        for left, right in zip(questions, questions[1:])
    )
    observed = [move for question in questions for move in classify_question_moves(question)]
    practical = {
        "application",
        "requirements",
        "architecture",
        "tradeoff",
        "diagnosis",
        "evaluation",
    }
    practical_rate = (
        sum(bool(classify_question_moves(question) & practical) for question in questions)
        / len(questions)
        if questions
        else 0.0
    )
    unique_topics = len({question.topic_key for question in questions})
    shapes = [_question_shape(question) for question in questions]
    near_duplicate_shapes = sum(
        SequenceMatcher(None, left, right).ratio() >= 0.9
        for index, left in enumerate(shapes)
        for right in shapes[index + 1 :]
    )
    fallback_rate = (
        sum("fallback" in question.interviewer_note.casefold() for question in questions)
        / len(questions)
        if questions
        else 0.0
    )
    minimum_questions, maximum_questions = duration_question_range(case)
    summary = {
        "question_count": len(questions),
        "focused_rate": rate("focused"),
        "grounded_rate": rate("grounded"),
        "source_independence_rate": rate("source_independent"),
        "non_repetition_rate": rate("non_repeating"),
        "move_alignment_rate": rate("move_aligned"),
        "practical_question_rate": practical_rate,
        "move_diversity": len(set(observed)),
        "question_shape_diversity": len(set(shapes)),
        "near_duplicate_question_shapes": near_duplicate_shapes,
        "fallback_rate": fallback_rate,
        "topic_breadth_rate": unique_topics / len(questions) if questions else 0.0,
        "no_adjacent_work_samples": not adjacent_work_samples,
        "candidate_led_design_opening": (
            bool(questions and design_opening_invites_clarification(questions[0]))
            if case.interview_format == "system_design"
            else True
        ),
        "duration_fit": minimum_questions <= len(questions) <= maximum_questions,
    }
    hard_pass = all(
        summary[name] == 1.0
        for name in (
            "focused_rate",
            "grounded_rate",
            "source_independence_rate",
            "non_repetition_rate",
            "topic_breadth_rate",
        )
    ) and summary["no_adjacent_work_samples"]
    quality_pass = (
        summary["move_alignment_rate"] >= 0.75
        and summary["practical_question_rate"] >= 0.5
        and summary["move_diversity"] >= min(3, len(questions))
        and summary["question_shape_diversity"] >= min(3, len(questions))
        and summary["near_duplicate_question_shapes"] == 0
        and summary["candidate_led_design_opening"]
        and summary["duration_fit"]
    )
    generation_robustness_pass = summary["fallback_rate"] <= 0.25
    return {
        "case_id": case.id,
        "role": case.role,
        "questions": question_rows,
        "summary": summary,
        "hard_pass": hard_pass,
        "quality_pass": quality_pass,
        "generation_robustness_pass": generation_robustness_pass,
        "passed": hard_pass and quality_pass,
    }


def generate_case_questions(
    case: InterviewRealismCase,
    *,
    model: Any | None = None,
) -> tuple[list[InterviewQuestion], float]:
    inventory = case.inventory()
    recent: list[InterviewQuestion] = []
    total_cost = 0.0
    for index, topic in enumerate(inventory.topics):
        plan = planned_interview_move(
            inventory,
            interview_format=case.interview_format,
            target_level=case.target_level,
            areas_visited=index,
            planned_area_count=len(inventory.topics),
        )
        question, cost = generate_question(
            inventory=inventory,
            topic=topic,
            interview_format=case.interview_format,
            target_level=case.target_level,
            kind="primary",
            recent_questions=recent,
            purpose=(
                "This is a frozen interview-realism evaluation case. "
                + (
                    "Open one coherent design problem and invite the candidate "
                    "to clarify its requirements before architecture. "
                    if index == 0 and case.interview_format == "system_design"
                    else (
                        "Continue the same design problem rather than starting a "
                        "new scenario. "
                        if case.interview_format == "system_design"
                        else ""
                    )
                )
                + interview_move_purpose(plan)
            ),
            planned_move=plan.move,
            model=model,
        )
        recent.append(question)
        total_cost += cost
    return recent, total_cost


def evaluate_interview_realism(
    dataset: InterviewRealismDataset,
    cases: list[InterviewRealismCase],
    *,
    model: Any | None = None,
    judge: SequenceJudge | None = None,
) -> dict[str, Any]:
    rows = []
    total_cost = 0.0
    for case in cases:
        questions, cost = generate_case_questions(case, model=model)
        total_cost += cost
        row = score_interview_sequence(case, questions)
        row["generated_questions"] = [
            question.model_dump(mode="json") for question in questions
        ]
        row["generation_cost_usd"] = cost
        if judge is not None:
            candidate_questions = [
                {
                    "text": question.text,
                    "work_sample": question.work_sample,
                    "work_sample_prompt": question.work_sample_prompt,
                    "coding_exercise": (
                        {
                            "language": question.coding_exercise.language,
                            "starter_code": question.coding_exercise.starter_code,
                            "visible_tests": question.coding_exercise.visible_tests,
                        }
                        if question.coding_exercise is not None
                        else None
                    ),
                }
                for question in questions
            ]
            try:
                judgment = judge.evaluate(
                    case=case.model_dump(mode="json"),
                    questions=candidate_questions,
                    deterministic_summary=row["summary"],
                    dataset_id=dataset.dataset_id,
                ).model_dump(mode="json")
            except Exception as error:
                row["semantic_judge_error"] = str(error)
                row["semantic_pass"] = False
                row["passed"] = False
                rows.append(row)
                continue
            row["semantic_judgment"] = judgment
            row["semantic_pass"] = min(
                judgment[name]
                for name in SEMANTIC_DIMENSIONS
            ) >= 3
            row["passed"] = row["passed"] and row["semantic_pass"]
        rows.append(row)

    return _evaluation_result(
        dataset=dataset,
        rows=rows,
        total_cost=total_cost,
    )


def _evaluation_result(
    *,
    dataset: InterviewRealismDataset,
    rows: list[dict[str, Any]],
    total_cost: float,
) -> dict[str, Any]:
    judgments = [row.get("semantic_judgment") for row in rows]
    judged = [
        item
        for item in judgments
        if item is not None and all(name in item for name in SEMANTIC_DIMENSIONS)
    ]
    dimensions = SEMANTIC_DIMENSIONS
    return {
        "dataset_id": dataset.dataset_id,
        "review_status": dataset.review_status,
        "cases": rows,
        "summary": {
            "case_count": len(rows),
            "passed_cases": sum(row["passed"] for row in rows),
            "pass_rate": sum(row["passed"] for row in rows) / len(rows) if rows else 0.0,
            "hard_pass_rate": sum(row["hard_pass"] for row in rows) / len(rows) if rows else 0.0,
            "quality_pass_rate": (
                sum(row["quality_pass"] for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            "generation_robustness_pass_rate": (
                sum(row["generation_robustness_pass"] for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            "semantic_means_0_to_4": {
                name: sum(item[name] for item in judged) / len(judged)
                for name in dimensions
            } if judged else {},
            "generation_cost_usd": round(total_cost, 6),
        },
    }


def rescore_interview_realism(
    dataset: InterviewRealismDataset,
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Re-run deterministic checks over saved generations without new calls."""

    cases_by_id = {case.id: case for case in dataset.cases}
    rows: list[dict[str, Any]] = []
    for saved in evaluation.get("cases", []):
        case = cases_by_id.get(saved.get("case_id"))
        if case is None:
            raise ValueError(f"saved evaluation contains unknown case {saved.get('case_id')!r}")
        questions = [
            InterviewQuestion.model_validate(value)
            for value in saved.get("generated_questions", [])
        ]
        row = score_interview_sequence(case, questions)
        row["generated_questions"] = saved.get("generated_questions", [])
        row["generation_cost_usd"] = float(saved.get("generation_cost_usd", 0))
        judgment = saved.get("semantic_judgment")
        if judgment is not None:
            row["semantic_judgment"] = judgment
            missing = [name for name in SEMANTIC_DIMENSIONS if name not in judgment]
            if missing:
                row["semantic_judge_error"] = (
                    "saved judgment predates rubric dimensions: "
                    + ", ".join(missing)
                )
                row["semantic_pass"] = False
            else:
                row["semantic_pass"] = min(
                    judgment[name] for name in SEMANTIC_DIMENSIONS
                ) >= 3
            row["passed"] = row["passed"] and row["semantic_pass"]
        rows.append(row)
    return _evaluation_result(
        dataset=dataset,
        rows=rows,
        total_cost=sum(row["generation_cost_usd"] for row in rows),
    )


def judge_saved_interview_realism(
    dataset: InterviewRealismDataset,
    evaluation: dict[str, Any],
    *,
    judge: SequenceJudge,
) -> dict[str, Any]:
    """Judge frozen generations without issuing any new generation calls."""

    rescored = rescore_interview_realism(dataset, evaluation)
    cases_by_id = {case.id: case for case in dataset.cases}
    for row in rescored["cases"]:
        case = cases_by_id[row["case_id"]]
        questions = [
            InterviewQuestion.model_validate(value)
            for value in row.get("generated_questions", [])
        ]
        candidate_questions = [
            {
                "text": question.text,
                "work_sample": question.work_sample,
                "work_sample_prompt": question.work_sample_prompt,
                "coding_exercise": (
                    {
                        "language": question.coding_exercise.language,
                        "starter_code": question.coding_exercise.starter_code,
                        "visible_tests": question.coding_exercise.visible_tests,
                    }
                    if question.coding_exercise is not None
                    else None
                ),
            }
            for question in questions
        ]
        try:
            judgment = judge.evaluate(
                case=case.model_dump(mode="json"),
                questions=candidate_questions,
                deterministic_summary=row["summary"],
                dataset_id=dataset.dataset_id,
            ).model_dump(mode="json")
        except Exception as error:
            row.pop("semantic_judgment", None)
            row["semantic_judge_error"] = str(error)
            row["semantic_pass"] = False
            row["passed"] = False
            continue
        row.pop("semantic_judge_error", None)
        row["semantic_judgment"] = judgment
        row["semantic_pass"] = min(
            judgment[name] for name in SEMANTIC_DIMENSIONS
        ) >= 3
        row["passed"] = row["passed"] and row["semantic_pass"]
    return _evaluation_result(
        dataset=dataset,
        rows=rescored["cases"],
        total_cost=sum(row["generation_cost_usd"] for row in rescored["cases"]),
    )


def serialize_evaluation(evaluation: dict[str, Any]) -> str:
    return json.dumps(evaluation, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
