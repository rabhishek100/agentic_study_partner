"""Transcript-derived evaluation of adaptive interviewer behavior.

The public videos inform behavioral expectations, not copied question text.
Each case freezes a synthetic candidate turn, routes it through the production
answer graph, and evaluates the next candidate-visible interviewer move.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from decks.topics import ScopeInventory, Topic
from interviews.contracts import (
    AnswerEvaluation,
    ContractModel,
    InterviewCheckpoint,
    InterviewFormat,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
    ScoreCard,
    TargetLevel,
    TopicState,
)
from interviews.evaluation import (
    InterviewValidationError,
    interviewer_reaction,
    validate_question,
)
from interviews.graph import AnswerGraphContext, answer_graph
from interviews.question_generation import (
    validate_question_focus,
    validate_question_progression,
)
from interviews.realism import question_is_source_dependent


DatasetSplit = Literal["development", "held_out"]
InteractionRoute = Literal["clarifying", "depth_follow_up", "advance"]


class TranscriptObservation(ContractModel):
    start_seconds: int = Field(ge=0)
    end_seconds: int = Field(gt=0)
    behavior: str

    @model_validator(mode="after")
    def valid_range(self) -> "TranscriptObservation":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("observation end must follow its start")
        return self


class TranscriptSource(ContractModel):
    id: str
    video_id: str
    title: str
    channel: str
    url: str
    upload_date: str
    duration_seconds: int = Field(gt=0)
    transcript_kind: Literal["youtube_auto_captions", "youtube_captions"]
    split: DatasetSplit
    observations: list[TranscriptObservation] = Field(min_length=1)


class InteractionTopic(ContractModel):
    key: str
    label: str
    evidence: str
    marker: str


class InterviewInteractionCase(ContractModel):
    id: str
    split: DatasetSplit
    source_ids: list[str] = Field(min_length=1)
    role: str
    source_title: str
    scope_title: str
    interview_format: InterviewFormat
    target_level: TargetLevel
    topics: list[InteractionTopic] = Field(min_length=2)
    primary_question: InterviewQuestion
    candidate_answer: str
    route: InteractionRoute
    follow_up_focus: str | None = None

    @model_validator(mode="after")
    def valid_case(self) -> "InterviewInteractionCase":
        keys = {topic.key for topic in self.topics}
        if len(keys) != len(self.topics):
            raise ValueError("interaction topic keys must be unique")
        if self.primary_question.topic_key not in keys:
            raise ValueError("primary question must use an interaction topic")
        if self.route != "advance" and not (self.follow_up_focus or "").strip():
            raise ValueError("adaptive routes require a follow-up focus")
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
            scope_key=f"interaction:{self.id}",
            title=self.scope_title,
            source_title=self.source_title,
            outline="\n".join(f"- {topic.label}" for topic in topics),
            topics=topics,
        )


class InterviewInteractionDataset(ContractModel):
    dataset_id: str
    version: int = Field(ge=1)
    review_status: str
    methodology: str
    sources: list[TranscriptSource] = Field(min_length=6)
    cases: list[InterviewInteractionCase] = Field(min_length=6)

    @model_validator(mode="after")
    def references_known_sources(self) -> "InterviewInteractionDataset":
        sources = {source.id: source for source in self.sources}
        for case in self.cases:
            missing = set(case.source_ids) - set(sources)
            if missing:
                raise ValueError(f"case {case.id} references unknown sources: {missing}")
            if any(sources[source_id].split != case.split for source_id in case.source_ids):
                raise ValueError(f"case {case.id} crosses the development/held-out split")
        return self


class InterviewInteractionJudgment(ContractModel):
    natural_continuation: int = Field(ge=0, le=4)
    probing_quality: int = Field(ge=0, le=4)
    neutrality: int = Field(ge=0, le=4)
    role_relevance: int = Field(ge=0, le=4)
    boundedness: int = Field(ge=0, le=4)
    violations: list[str]
    explanation: str


class InteractionJudge(Protocol):
    def evaluate(self, **values) -> InterviewInteractionJudgment: ...


class _StaticEvaluationModel:
    """Inject the frozen candidate condition while exercising the real graph."""

    def __init__(self, evaluation: AnswerEvaluation):
        self.evaluation = evaluation

    def invoke(self, _messages):
        return {"parsed": self.evaluation, "raw": None}


NON_NEUTRAL_REACTION_TERMS = (
    "correct",
    "incorrect",
    "wrong",
    "strong answer",
    "well-grounded",
    "right track",
    "important issue",
    "needs more precision",
    "score",
    "expected",
)


def load_interview_interaction_dataset(
    path: str | Path,
) -> InterviewInteractionDataset:
    return InterviewInteractionDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _scripted_evaluation(case: InterviewInteractionCase) -> AnswerEvaluation:
    topic = next(
        item for item in case.topics if item.key == case.primary_question.topic_key
    )
    adaptive = case.route != "advance"
    ambiguous = case.route == "clarifying"
    score = 2 if ambiguous else 4
    return AnswerEvaluation(
        classification="partially_correct" if ambiguous else "source_aligned",
        scores=ScoreCard(
            technical_correctness=score,
            depth_completeness=score,
            reasoning_structure=max(3, score),
            tradeoff_awareness=max(3, score),
            communication_clarity=4,
            independence=5,
        ),
        strengths=["The answer engaged with the requested technical decision."],
        gaps=(
            [case.follow_up_focus or "The answer is ambiguous."] if ambiguous else []
        ),
        concise_feedback=(
            "The response is ambiguous." if ambiguous else "The scoped answer is sound."
        ),
        recommended_answer=f"{topic.evidence} {topic.marker}",
        citation_markers=[topic.marker],
        question_complete=not ambiguous,
        needs_clarifying_probe=ambiguous,
        clarifying_probe=case.follow_up_focus if ambiguous else None,
        needs_depth_follow_up=case.route == "depth_follow_up",
        depth_follow_up_focus=(
            case.follow_up_focus if case.route == "depth_follow_up" else None
        ),
        topic_complete=not adaptive,
    )


def _session(case: InterviewInteractionCase) -> InterviewSession:
    checkpoint = InterviewCheckpoint(
        topics=[TopicState(key=topic.key, label=topic.label) for topic in case.topics],
        active_topic_key=case.primary_question.topic_key,
    )
    return InterviewSession(
        session_id=f"interaction-{case.id}",
        source_kind="book",
        scope_key=f"interaction:{case.id}",
        title=f"Interview · {case.scope_title}",
        source_title=case.source_title,
        interview_format=case.interview_format,
        format_source="override",
        feedback_mode="realistic",
        target_level=case.target_level,
        maximum_duration_minutes=45,
        estimated_min_minutes=15,
        estimated_max_minutes=45,
        status="active",
        elapsed_seconds=120,
        checkpoint=checkpoint,
        turns=[],
    )


def _safe_check(check) -> tuple[bool, str | None]:
    try:
        check()
    except InterviewValidationError as error:
        return False, str(error)
    return True, None


def evaluate_interaction_case(
    case: InterviewInteractionCase,
    *,
    question_model: Any,
    judge: InteractionJudge | None = None,
    dataset_id: str,
    transcript_behaviors: list[str] | None = None,
) -> dict[str, Any]:
    inventory = case.inventory()
    primary_topic = next(
        topic for topic in inventory.topics if topic.key == case.primary_question.topic_key
    )
    validate_question(case.primary_question, primary_topic)
    evaluation = _scripted_evaluation(case)
    current = InterviewTurn(turn_index=0, question=case.primary_question)
    output = answer_graph.invoke(
        {
            "session": _session(case),
            "inventory": inventory,
            "current_turn": current,
            "answer_text": case.candidate_answer,
        },
        context=AnswerGraphContext(
            evaluation_model=_StaticEvaluationModel(evaluation),
            question_model=question_model,
        ),
    )
    next_question = output["next_question"]
    try:
        reaction = interviewer_reaction(output["evaluation"], mode="realistic")
    except TypeError:
        # Keeps the evaluator runnable against the pre-change baseline.
        reaction = interviewer_reaction(output["evaluation"])
    expected_kind = {
        "clarifying": "clarifying",
        "depth_follow_up": "follow_up",
        "advance": "primary",
    }[case.route]
    expected_same_topic = case.route != "advance"
    actual_same_topic = bool(
        next_question is not None
        and next_question.topic_key == case.primary_question.topic_key
    )
    kind_correct = bool(next_question is not None and next_question.kind == expected_kind)
    route_correct = actual_same_topic == expected_same_topic
    reaction_neutral = not any(
        phrase in reaction.casefold() for phrase in NON_NEUTRAL_REACTION_TERMS
    )
    reaction_source_independent = not question_is_source_dependent(reaction)

    focused = grounded = progressive = source_independent = False
    errors: list[str] = []
    if next_question is None:
        errors.append("the graph did not produce the expected next interviewer move")
    else:
        next_topic = next(
            topic for topic in inventory.topics if topic.key == next_question.topic_key
        )
        focused, focus_error = _safe_check(lambda: validate_question_focus(next_question))
        grounded, grounding_error = _safe_check(
            lambda: validate_question(next_question, next_topic)
        )
        progressive, progression_error = _safe_check(
            lambda: validate_question_progression(next_question, [case.primary_question])
        )
        source_independent = not question_is_source_dependent(next_question.text)
        errors.extend(
            error
            for error in (focus_error, grounding_error, progression_error)
            if error
        )

    fallback_used = bool(
        next_question is not None
        and "fallback" in next_question.interviewer_note.casefold()
    )
    checks = {
        "route_correct": route_correct,
        "kind_correct": kind_correct,
        "focused": focused,
        "grounded": grounded,
        "source_independent": source_independent,
        "non_repeating": progressive,
        "reaction_neutral": reaction_neutral,
        "reaction_source_independent": reaction_source_independent,
    }
    hard_pass = all(checks.values())
    row: dict[str, Any] = {
        "case_id": case.id,
        "split": case.split,
        "role": case.role,
        "route": case.route,
        "source_ids": case.source_ids,
        "candidate_visible": {
            "question": case.primary_question.text,
            "candidate_answer": case.candidate_answer,
            "interviewer_reaction": reaction,
            "next_question": (
                {
                    "kind": next_question.kind,
                    "text": next_question.text,
                    "work_sample": next_question.work_sample,
                    "work_sample_prompt": next_question.work_sample_prompt,
                }
                if next_question is not None
                else None
            ),
        },
        "checks": checks,
        "errors": errors,
        "fallback_used": fallback_used,
        "generation_cost_usd": float(output.get("question_cost_usd", 0)),
        "hard_pass": hard_pass,
        "generation_robustness_pass": not fallback_used,
        "passed": hard_pass,
    }
    if judge is not None:
        try:
            judgment = judge.evaluate(
                dataset_id=dataset_id,
                case=case.model_dump(mode="json"),
                candidate_visible=row["candidate_visible"],
                transcript_behaviors=transcript_behaviors or [],
                deterministic_checks=checks,
            ).model_dump(mode="json")
        except Exception as error:
            row["semantic_judge_error"] = str(error)
            row["semantic_pass"] = False
            row["passed"] = False
            return row
        row["semantic_judgment"] = judgment
        row["semantic_pass"] = min(
            judgment[name]
            for name in (
                "natural_continuation",
                "probing_quality",
                "neutrality",
                "role_relevance",
                "boundedness",
            )
        ) >= 3
        row["passed"] = row["passed"] and row["semantic_pass"]
    return row


def evaluate_interview_interactions(
    dataset: InterviewInteractionDataset,
    cases: list[InterviewInteractionCase],
    *,
    question_model: Any,
    judge: InteractionJudge | None = None,
) -> dict[str, Any]:
    sources = {source.id: source for source in dataset.sources}
    rows = [
        evaluate_interaction_case(
            case,
            question_model=question_model,
            judge=judge,
            dataset_id=dataset.dataset_id,
            transcript_behaviors=[
                observation.behavior
                for source_id in case.source_ids
                for observation in sources[source_id].observations
            ],
        )
        for case in cases
    ]
    judged = [row["semantic_judgment"] for row in rows if "semantic_judgment" in row]
    dimensions = (
        "natural_continuation",
        "probing_quality",
        "neutrality",
        "role_relevance",
        "boundedness",
    )
    return {
        "dataset_id": dataset.dataset_id,
        "review_status": dataset.review_status,
        "cases": rows,
        "summary": {
            "case_count": len(rows),
            "passed_cases": sum(row["passed"] for row in rows),
            "pass_rate": sum(row["passed"] for row in rows) / len(rows) if rows else 0.0,
            "hard_pass_rate": sum(row["hard_pass"] for row in rows) / len(rows) if rows else 0.0,
            "generation_robustness_pass_rate": (
                sum(row["generation_robustness_pass"] for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            "semantic_means_0_to_4": (
                {
                    name: sum(item[name] for item in judged) / len(judged)
                    for name in dimensions
                }
                if judged
                else {}
            ),
            "generation_cost_usd": round(
                sum(row["generation_cost_usd"] for row in rows), 6
            ),
        },
    }


def serialize_interaction_evaluation(evaluation: dict[str, Any]) -> str:
    return json.dumps(evaluation, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
