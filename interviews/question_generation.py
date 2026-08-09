"""Validated question generation, progression guards, and work-sample policy."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from decks.topics import ScopeInventory, Topic

from .contracts import (
    InterviewFormat,
    InterviewQuestion,
    QuestionKind,
    TargetLevel,
    WorkSampleKind,
)
from .evaluation import InterviewValidationError, validate_question
from .models import InterviewModelError, invoke_structured, structured_model
from .prompts import build_question_messages


MAX_QUESTIONS_PER_TOPIC = 2
QUESTION_RETRY_ATTEMPTS = 2

ARCHITECTURE = re.compile(
    r"\b(architect(?:ure|ural)|diagram|component|service|data[ -]?flow|pipeline|"
    r"distributed|storage|database|cache|queue|api)\b",
    re.IGNORECASE,
)
EQUATION = re.compile(
    r"\b(equation|derive|derivation|proof|gradient|loss|objective|probability|"
    r"likelihood|log[ -]?odds|matrix|calculus|formula)\b",
    re.IGNORECASE,
)
CODE = re.compile(
    r"\b(code|coding|implement|implementation|pseudocode|algorithm|function|"
    r"class|complexity|debug)\b",
    re.IGNORECASE,
)
ASSUMPTIONS = re.compile(
    r"\b(assumption|requirement|estimate|capacity|scale|qps|throughput|latency|"
    r"traffic|constraint|slo|sla)\b",
    re.IGNORECASE,
)

DEFAULT_WORK_SAMPLE_PROMPTS: dict[WorkSampleKind, str] = {
    "none": "",
    "architecture_diagram": (
        "Share your screen and draw the architecture you would propose. Label the "
        "main components, interfaces, and important data flows, then talk through it."
    ),
    "equation_derivation": (
        "Share your screen and derive the key equation step by step. State each "
        "assumption and explain what every term means."
    ),
    "code": (
        "Share your screen and write code or pseudocode for the core approach. Walk "
        "through complexity, edge cases, and one test as you work."
    ),
    "assumptions": (
        "Share your screen and write down the assumptions, constraints, and estimates "
        "that drive your answer before proceeding."
    ),
}


def _normalized_question(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def validate_question_progression(
    question: InterviewQuestion,
    recent_questions: list[InterviewQuestion],
) -> InterviewQuestion:
    """Reject a restatement of a recent question before it reaches a candidate."""

    current = _normalized_question(question.text)
    for previous in recent_questions[-4:]:
        prior = _normalized_question(previous.text)
        if not current or not prior:
            continue
        if current == prior or SequenceMatcher(None, current, prior).ratio() >= 0.86:
            raise InterviewValidationError(
                "the generated question repeats a recent interview question"
            )
    return question


def _matching_work_samples(
    question: InterviewQuestion,
    topic: Topic,
    interview_format: InterviewFormat,
) -> list[WorkSampleKind]:
    """Return relevant artifacts, preferring the actual question over context."""

    candidates: list[WorkSampleKind] = []

    def add_matches(text: str) -> None:
        for pattern, kind in (
            (CODE, "code"),
            (EQUATION, "equation_derivation"),
            (ASSUMPTIONS, "assumptions"),
            (ARCHITECTURE, "architecture_diagram"),
        ):
            if pattern.search(text) and kind not in candidates:
                candidates.append(kind)

    add_matches(question.text)
    add_matches("\n".join([topic.label, topic.evidence_text[:2_500]]))
    if interview_format == "system_design" and "architecture_diagram" not in candidates:
        candidates.append("architecture_diagram")
    return candidates


def apply_work_sample_policy(
    question: InterviewQuestion,
    *,
    topic: Topic,
    interview_format: InterviewFormat,
    recent_questions: list[InterviewQuestion],
) -> InterviewQuestion:
    """Request useful work samples without turning every question into screen work."""

    # Follow-ups should resolve the current verbal gap and move on. Requiring a
    # second artifact is both repetitive and expensive.
    if question.kind != "primary":
        return question.model_copy(
            update={"work_sample": "none", "work_sample_prompt": None}
        )
    # Never request screen work on consecutive questions.
    if recent_questions and recent_questions[-1].work_sample != "none":
        return question.model_copy(
            update={"work_sample": "none", "work_sample_prompt": None}
        )

    candidates = _matching_work_samples(question, topic, interview_format)
    if question.work_sample != "none":
        candidates.insert(0, question.work_sample)
        candidates = list(dict.fromkeys(candidates))
    recently_used = {
        item.work_sample
        for item in recent_questions[-4:]
        if item.work_sample != "none"
    }
    work_sample = next(
        (candidate for candidate in candidates if candidate not in recently_used),
        candidates[0] if candidates else "none",
    )
    if work_sample == "none":
        return question.model_copy(
            update={"work_sample": "none", "work_sample_prompt": None}
        )
    authored_prompt = (
        (question.work_sample_prompt or "").strip()
        if work_sample == question.work_sample
        else ""
    )
    prompt = authored_prompt or DEFAULT_WORK_SAMPLE_PROMPTS[work_sample]
    return question.model_copy(
        update={"work_sample": work_sample, "work_sample_prompt": prompt}
    )


def generate_question(
    *,
    inventory: ScopeInventory,
    topic: Topic,
    interview_format: InterviewFormat,
    target_level: TargetLevel,
    kind: QuestionKind,
    recent_questions: list[InterviewQuestion],
    prior_question: InterviewQuestion | None = None,
    candidate_answer: str | None = None,
    purpose: str | None = None,
    model: Any | None = None,
) -> tuple[InterviewQuestion, float]:
    """Generate with grounding and non-repetition validation, retrying once."""

    client = model or structured_model(InterviewQuestion)
    last_error: Exception | None = None
    total_cost = 0.0
    for attempt in range(QUESTION_RETRY_ATTEMPTS):
        repair = (
            "Repair the prior draft: use only active-topic evidence markers and ask "
            "a materially different question from every recent question."
            if attempt
            else None
        )
        adaptive_purpose = " ".join(value for value in [purpose, repair] if value)
        try:
            question, cost = invoke_structured(
                client,
                build_question_messages(
                    inventory=inventory,
                    topic=topic,
                    interview_format=interview_format,
                    target_level=target_level,
                    kind=kind,
                    prior_question=prior_question,
                    candidate_answer=candidate_answer,
                    purpose=adaptive_purpose or None,
                    recent_questions=recent_questions,
                ),
                InterviewQuestion,
            )
        except InterviewModelError as error:
            last_error = error
            continue
        total_cost += cost
        try:
            validated = validate_question(question, topic).model_copy(
                update={
                    "topic_key": topic.key,
                    "topic_label": topic.label,
                    "kind": kind,
                    "difficulty": target_level,
                }
            )
            validated = validate_question_progression(validated, recent_questions)
            return (
                apply_work_sample_policy(
                    validated,
                    topic=topic,
                    interview_format=interview_format,
                    recent_questions=recent_questions,
                ),
                total_cost,
            )
        except InterviewValidationError as error:
            last_error = error
    raise InterviewModelError(str(last_error or "question validation failed"))
