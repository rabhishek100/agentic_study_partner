"""Validated question generation, progression guards, and work-sample policy."""

from __future__ import annotations

import logging
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
from .models import (
    InterviewModelError,
    InterviewProviderError,
    invoke_structured,
    structured_model,
)
from .prompts import build_question_messages, candidate_topic_label


logger = logging.getLogger("study_partner.interviews.questions")


MAX_QUESTIONS_PER_TOPIC = 2
QUESTION_RETRY_ATTEMPTS = 2
MAX_QUESTION_WORDS = 32
MAX_WORK_SAMPLE_WORDS = 20
MAX_SPOKEN_TURN_WORDS = 50
MAX_EXPECTED_POINTS = 3

SECOND_OBJECTIVE = re.compile(
    r"(?:[,;]\s*|\b(?:and|then)\s+)"
    r"(?:what|how|why|which|describe|explain|discuss|identify|compare|"
    r"derive|design|implement|estimate|evaluate|justify|show|write|handle|"
    r"address|state|list|analyze|assess|test|validate|calculate|outline)\b",
    re.IGNORECASE,
)

ARCHITECTURE_REQUEST = re.compile(
    r"\b(?:draw|sketch|diagram|map|lay out|visualize)\b.{0,100}"
    r"\b(?:architect(?:ure|ural)|system|component|service|data[ -]?flow|pipeline|"
    r"layer|storage|database|cache|queue|api)\b",
    re.IGNORECASE,
)
EQUATION_REQUEST = re.compile(
    r"(?:\b(?:derive|prove|formulate|write|show|calculate)\b.{0,100}"
    r"\b(?:equation|formula|function|gradient|loss|objective function|likelihood|"
    r"log[ -]?odds|probability|matrix)\b)|"
    r"(?:\b(?:equation|formula|gradient|loss|objective function|likelihood|"
    r"log[ -]?odds)\b.{0,60}\b(?:derive|derivation|proof)\b)",
    re.IGNORECASE,
)
CODE_REQUEST = re.compile(
    r"\b(?:code|implement|write|debug)\b.{0,100}"
    r"\b(?:code|implementation|pseudocode|algorithm|function|class|test)\b|"
    r"\b(?:pseudocode|code)\b",
    re.IGNORECASE,
)
VAGUE_MATH_REQUEST = re.compile(
    r"\b(?:the|an?|one|this|single|key|single key)\s+"
    r"(?:key\s+)?(?:equation|formula)\b",
    re.IGNORECASE,
)
GENERIC_RECALL_QUESTION = re.compile(
    r"\b(?:central|main|key)\s+(?:idea|concept)\s+behind\b|"
    r"\baccording to (?:the|this) (?:book|chapter|source)\b|"
    r"\b(?:name|list|recall)\b.{0,40}\b(?:from|in) (?:the|this) "
    r"(?:book|chapter|source)\b",
    re.IGNORECASE,
)

DEFAULT_WORK_SAMPLE_PROMPTS: dict[WorkSampleKind, str] = {
    "none": "",
    "architecture_diagram": (
        "Use the shared screen to draw the architecture requested in the question."
    ),
    "equation_derivation": (
        "Use the shared screen to show each step of the requested derivation."
    ),
    "code": (
        "Complete the Python scaffold in the coding workspace."
    ),
    # Retained only so old persisted questions remain parseable. New
    # assumption and requirements questions are deliberately verbal.
    "assumptions": "",
}


def _fallback_evidence(topic: Topic) -> tuple[str, str]:
    """Return one real marker and a short source excerpt for a safe fallback."""

    if not topic.allowed_markers:
        raise InterviewModelError("the active topic has no citable evidence")
    marker = min(
        topic.allowed_markers,
        key=lambda value: (
            topic.evidence_text.find(value)
            if value in topic.evidence_text
            else len(topic.evidence_text)
        ),
    )
    after_marker = topic.evidence_text.split(marker, 1)[-1]
    next_marker = re.search(r"\[(?:N\d+:P\d+|S\d+)\]", after_marker)
    excerpt = after_marker[: next_marker.start() if next_marker else None]
    words = " ".join(excerpt.split()).split()
    excerpt = " ".join(words[:80]).strip(" -:;,.")
    if not excerpt:
        raise InterviewModelError("the active topic has no readable evidence")
    return marker, excerpt


def _practical_fallback_scope(
    label: str,
    *,
    kind: QuestionKind,
    alternate: bool = False,
) -> tuple[str, list[str]]:
    """Build a reasoning prompt that never asks the candidate to recall text."""

    # Section titles are source data and may themselves contain questions,
    # sentence punctuation, or compound prompts. Never splice those verbatim
    # into a candidate-facing fallback.
    first_clause = re.split(
        r"[.!?,;:]|\b(?:and\s+then|and\s+what|and\s+how|and\s+why|"
        r"and\s+which)\b",
        candidate_topic_label(label),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    clean_label = " ".join(first_clause.split()[:12]) or "this technical decision"
    gerund_label = bool(re.match(r"^[A-Za-z]+ing\b", clean_label))
    natural_label = (
        clean_label[:1].lower() + clean_label[1:]
        if gerund_label
        else clean_label
    )
    if alternate and gerund_label:
        return (
            f"What other factor would influence {natural_label} in this scenario?",
            ["Identify another technically relevant decision factor."],
        )
    if alternate:
        return (
            f"What other practical consideration matters when applying {clean_label}?",
            ["Identify another technically relevant practical consideration."],
        )
    if kind == "primary" and gerund_label:
        return (
            f"How would you approach {natural_label} in a real system?",
            ["Describe a plausible practical decision or approach."],
        )
    if kind == "primary":
        return (
            f"How would you use {clean_label} in a practical system?",
            ["Describe a plausible practical application."],
        )
    if gerund_label:
        return (
            f"What factor would most influence {natural_label} in this scenario?",
            ["Identify one technically relevant decision factor."],
        )
    return (
        f"What practical consideration matters most when applying {clean_label}?",
        ["Identify one technically relevant practical consideration."],
    )


def _unconditional_fallback_question(
    *,
    topic: Topic,
    target_level: TargetLevel,
    kind: QuestionKind,
    recent_questions: list[InterviewQuestion],
) -> InterviewQuestion:
    """Return a fixed atomic question when even a title-derived fallback fails."""

    marker, excerpt = _fallback_evidence(topic)
    primary = "What practical factor would guide your decision in this scenario?"
    alternate = "Which trade-off would you examine next in this scenario?"
    recent = {_normalized_question(item.text) for item in recent_questions[-4:]}
    text = alternate if _normalized_question(primary) in recent else primary
    return InterviewQuestion(
        topic_key=topic.key,
        topic_label=topic.label,
        kind=kind,
        text=text,
        expected_points=["Identify one technically relevant decision factor."],
        suggested_answer=f"{excerpt}. {marker}",
        citation_markers=[marker],
        difficulty=target_level,
        interviewer_note="Unconditional continuity fallback after title sanitization.",
        work_sample="none",
        work_sample_prompt=None,
    )


def repair_legacy_recall_fallback(question: InterviewQuestion) -> InterviewQuestion:
    """Repair recall-oriented continuity questions saved by older prompts."""

    text = " ".join(question.text.split())
    source_match = re.fullmatch(
        r"State one source-grounded point about (.+)\.", text, re.IGNORECASE
    )
    central_match = re.fullmatch(
        r"What is the central idea behind (.+)\?", text, re.IGNORECASE
    )
    generic_repeat = text.casefold() == (
        "state one important source-grounded point we have not covered yet."
    )
    if not source_match and not central_match and not generic_repeat:
        return question

    label = (
        (source_match or central_match).group(1)
        if source_match or central_match
        else question.topic_label.split(" :: ")[-1]
    )
    replacement, expected_points = _practical_fallback_scope(
        label,
        kind=question.kind,
        alternate=generic_repeat,
    )
    return question.model_copy(
        update={
            "text": replacement,
            "expected_points": expected_points,
            "work_sample": "none",
            "work_sample_prompt": None,
            "interviewer_note": (
                "Repaired a legacy recall-oriented continuity fallback."
            ),
        }
    )


def repair_numbered_fallback(question: InterviewQuestion) -> InterviewQuestion:
    """Remove a printed section ordinal leaked by an older fallback question."""

    if "fallback" not in question.interviewer_note.casefold():
        return question
    label = candidate_topic_label(question.topic_label)
    if label == question.topic_label.split(" :: ")[-1].strip():
        return question
    replacement, expected_points = _practical_fallback_scope(
        label,
        kind=question.kind,
        alternate=question.text.casefold().startswith("what other"),
    )
    return question.model_copy(
        update={
            "text": replacement,
            "expected_points": expected_points,
            "interviewer_note": "Repaired a fallback that leaked a section ordinal.",
        }
    )


def repair_nonvisual_work_sample(question: InterviewQuestion) -> InterviewQuestion:
    """Remove legacy screen tasks without an explicit visual artifact request."""

    if question.work_sample == "none":
        return question
    supported = _matching_work_samples(question)
    if question.work_sample != "assumptions" and question.work_sample in supported:
        return question
    return question.model_copy(
        update={
            "work_sample": "none",
            "work_sample_prompt": None,
            "coding_exercise": None,
            "interviewer_note": (
                "Converted an irrelevant text-only screen exercise to a verbal answer."
            ),
        }
    )


def grounded_fallback_question(
    *,
    topic: Topic,
    target_level: TargetLevel,
    kind: QuestionKind,
    recent_questions: list[InterviewQuestion],
) -> InterviewQuestion:
    """Build one atomic cited question without another provider call.

    This is a continuity fallback, not a second generation strategy. It keeps
    an already-evaluated answer from being lost when both authored next-question
    drafts fail deterministic validation.
    """

    marker, excerpt = _fallback_evidence(topic)
    label = (topic.label.split(" :: ")[-1].strip() or "this topic")
    text, expected_points = _practical_fallback_scope(label, kind=kind)
    question = InterviewQuestion(
        topic_key=topic.key,
        topic_label=topic.label,
        kind=kind,
        text=text,
        expected_points=expected_points,
        suggested_answer=f"{excerpt}. {marker}",
        citation_markers=[marker],
        difficulty=target_level,
        interviewer_note="Deterministic continuity fallback after question validation.",
        work_sample="none",
        work_sample_prompt=None,
    )
    try:
        question = validate_question_focus(validate_question(question, topic))
    except InterviewValidationError:
        # Saving the candidate's answer must never depend on a section title
        # surviving presentation validation. This fixed question has no title
        # interpolation and is valid by construction.
        return _unconditional_fallback_question(
            topic=topic,
            target_level=target_level,
            kind=kind,
            recent_questions=recent_questions,
        )
    try:
        return validate_question_progression(question, recent_questions)
    except InterviewValidationError:
        # A repeated continuity fallback asks for a different practical angle;
        # it still must not turn into a request to remember the source.
        alternate, alternate_points = _practical_fallback_scope(
            label,
            kind=kind,
            alternate=True,
        )
        return question.model_copy(
            update={"text": alternate, "expected_points": alternate_points}
        )


def _normalized_question(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def validate_question_focus(question: InterviewQuestion) -> InterviewQuestion:
    """Keep one candidate turn to one atomic interview objective."""

    text = " ".join(question.text.split())
    prompt = " ".join((question.work_sample_prompt or "").split())
    if _word_count(text) > MAX_QUESTION_WORDS:
        raise InterviewValidationError("the generated question is too broad for one turn")
    if text.count("?") > 1 or len(re.findall(r"[.!?](?:\s|$)", text)) > 1:
        raise InterviewValidationError("the generated question contains multiple prompts")
    if SECOND_OBJECTIVE.search(text):
        raise InterviewValidationError("the generated question asks for multiple objectives")
    if GENERIC_RECALL_QUESTION.search(text):
        raise InterviewValidationError(
            "the generated question tests source recall instead of understanding"
        )
    if len(question.expected_points) > MAX_EXPECTED_POINTS:
        raise InterviewValidationError(
            "the private rubric exceeds the explicit scope of one question"
        )
    if VAGUE_MATH_REQUEST.search(text):
        raise InterviewValidationError(
            "an equation question must name the relationship being derived"
        )
    if prompt:
        # Starting screen capture is the response mode, not a second technical
        # objective. Validate only the instruction that follows that prefix.
        task_prompt = re.sub(
            r"^share your screen(?:\s+and)?\s+", "", prompt, flags=re.IGNORECASE
        )
        if _word_count(prompt) > MAX_WORK_SAMPLE_WORDS:
            raise InterviewValidationError("the work-sample instruction is too broad")
        if (
            SECOND_OBJECTIVE.search(task_prompt)
            or prompt.count("?")
            or len(re.findall(r"[.!?](?:\s|$)", prompt)) > 1
            or prompt.count(",") >= 2
        ):
            raise InterviewValidationError(
                "the work-sample instruction adds another objective"
            )
        supported = _matching_work_samples(question)
        if question.work_sample not in supported:
            raise InterviewValidationError(
                "the work-sample instruction does not match the interview question"
            )
    if question.work_sample == "code" and question.coding_exercise is None:
        raise InterviewValidationError(
            "a new coding question must include an executable Python scaffold"
        )
    if (
        question.coding_exercise is not None
        and not question.coding_exercise.hints
    ):
        raise InterviewValidationError(
            "a new coding question must include at least one progressive hint"
        )
    if _word_count(text) + _word_count(prompt) > MAX_SPOKEN_TURN_WORDS:
        raise InterviewValidationError("the complete spoken turn asks too much at once")
    return question


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
) -> list[WorkSampleKind]:
    """Return artifacts explicitly requested by the candidate-facing question."""

    candidates: list[WorkSampleKind] = []

    for pattern, kind in (
        (CODE_REQUEST, "code"),
        (EQUATION_REQUEST, "equation_derivation"),
        (ARCHITECTURE_REQUEST, "architecture_diagram"),
    ):
        if pattern.search(question.text) and kind not in candidates:
            candidates.append(kind)
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
            update={
                "work_sample": "none",
                "work_sample_prompt": None,
                "coding_exercise": None,
            }
        )
    # Never request screen work on consecutive questions.
    if recent_questions and recent_questions[-1].work_sample != "none":
        return question.model_copy(
            update={
                "work_sample": "none",
                "work_sample_prompt": None,
                "coding_exercise": None,
            }
        )

    # Topic evidence can suggest future questions, but it must never silently
    # add an artifact to a different candidate-facing objective. The screenshot
    # task is inferred only from the words the candidate actually hears.
    candidates = _matching_work_samples(question)
    if question.work_sample not in {"none", "assumptions"}:
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
            update={
                "work_sample": "none",
                "work_sample_prompt": None,
                "coding_exercise": None,
            }
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
            "Repair the prior draft: use only active-topic evidence markers, ask a "
            "materially different question, and keep exactly one atomic objective. "
            "Test reasoning rather than recall of a source heading, and include only "
            "private expected points that the audible question explicitly requests. "
            "The screen instruction may change the response format but must not add "
            "another task."
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
        except InterviewProviderError as error:
            last_error = error
            break
        except InterviewModelError as error:
            last_error = error
            continue
        total_cost += cost
        try:
            validated = validate_question_focus(
                validate_question(question, topic)
            ).model_copy(
                update={
                    "topic_key": topic.key,
                    "topic_label": topic.label,
                    "kind": kind,
                    "difficulty": target_level,
                    "clarifications": [],
                }
            )
            validated = validate_question_progression(validated, recent_questions)
            focused = apply_work_sample_policy(
                validated,
                topic=topic,
                interview_format=interview_format,
                recent_questions=recent_questions,
            )
            return validate_question_focus(focused), total_cost
        except InterviewValidationError as error:
            last_error = error
    logger.warning(
        "Using grounded fallback after question generation failed validation",
        extra={
            "topic_key": topic.key,
            "question_kind": kind,
            "reason": str(last_error or "question validation failed"),
        },
    )
    return (
        grounded_fallback_question(
            topic=topic,
            target_level=target_level,
            kind=kind,
            recent_questions=recent_questions,
        ),
        total_cost,
    )
