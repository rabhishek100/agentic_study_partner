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
from .realism import question_is_source_dependent


logger = logging.getLogger("study_partner.interviews.questions")


MAX_QUESTIONS_PER_TOPIC = 2
QUESTION_RETRY_ATTEMPTS = 2
MAX_QUESTION_WORDS = 32
MAX_WORK_SAMPLE_WORDS = 20
MAX_SPOKEN_TURN_WORDS = 50
MAX_EXPECTED_POINTS = 3

SECOND_OBJECTIVE = re.compile(
    r"(?:[,;]\s*|\b(?:and|then)\s+)"
    r"(?:(?:also|briefly)\s+)?"
    r"(?:what|how|which|describe|explain|discuss|identify|compare|"
    r"derive|design|implement|estimate|evaluate|justify|show|write|handle|"
    r"address|state|list|analyze|assess|test|validate|calculate|outline)\b",
    re.IGNORECASE,
)
OBJECTIVE_CUE = re.compile(
    r"\b(?:what|how|why|which|describe|explain|discuss|identify|compare|"
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
CODE_OBSERVABLE_BEHAVIOR = re.compile(
    r"\b(?:return|output|produce|compute|calculate|convert|modify|update|mutate|"
    r"raise|print|yield|find|determine|sort|filter|count)\w*\b",
    re.IGNORECASE,
)
CODE_META_TASK = re.compile(
    r"\b(?:explain|justify|rationale|prose|comment|comments|describe why)\b",
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

GENERIC_TOPIC_LABELS = frozenset(
    {
        "background",
        "conclusion",
        "introduction",
        "motivation",
        "overview",
        "problem statement",
        "summary",
    }
)

EVIDENCE_SEGMENT = re.compile(
    r"(?P<marker>\[(?:N\d+:P\d+|S\d+)\])\s*"
    r"(?P<text>.*?)"
    r"(?=(?:\n\s*)?\[(?:N\d+:P\d+|S\d+)\]|\Z)",
    re.DOTALL,
)


def _fallback_topic_label(label: str) -> str:
    """Choose a meaningful candidate label from a hierarchical source path."""

    parts = [
        candidate_topic_label(part)
        for part in label.split(" :: ")
        if part.strip()
    ]
    if not parts:
        return "this technical topic"
    if parts[-1].casefold() not in GENERIC_TOPIC_LABELS:
        return parts[-1]
    return next(
        (
            part
            for part in reversed(parts[:-1])
            if part.casefold() not in GENERIC_TOPIC_LABELS
        ),
        parts[-1],
    )


def _fallback_evidence(topic: Topic) -> tuple[str, str]:
    """Return one real marker and a short source excerpt for a safe fallback."""

    if not topic.allowed_markers:
        raise InterviewModelError("the active topic has no citable evidence")
    segments: list[tuple[str, str]] = []
    for match in EVIDENCE_SEGMENT.finditer(topic.evidence_text):
        marker = match.group("marker")
        excerpt = " ".join(match.group("text").split()).strip(" -:;,.")
        if marker in topic.allowed_markers and excerpt:
            segments.append((marker, excerpt))
    if not segments:
        raise InterviewModelError("the active topic has no readable evidence")

    path_labels = {
        candidate_topic_label(part).casefold()
        for part in topic.label.split(" :: ")
        if part.strip()
    }
    substantive = [
        (index, item)
        for index, item in enumerate(segments)
        if len(item[1]) >= 40
        and candidate_topic_label(item[1]).casefold() not in path_labels
    ]
    if not substantive:
        marker, excerpt = segments[0]
        return marker, " ".join(excerpt.split()[:80]).strip(" -:;,.")

    label_tokens = {
        value
        for value in re.findall(r"[a-z0-9]+", _fallback_topic_label(topic.label).lower())
        if len(value) >= 4
    }
    anchor_index, (marker, excerpt) = next(
        (
            candidate
            for candidate in substantive
            if label_tokens
            & set(re.findall(r"[a-z0-9]+", candidate[1][1].lower()))
        ),
        substantive[0],
    )
    # Consecutive blocks on the same cited page often split one explanation
    # around a displayed equation. Join them into a useful private answer while
    # staying inside the marker that will be cited.
    answer_parts = [excerpt]
    for next_marker, next_excerpt in segments[anchor_index + 1 :]:
        if next_marker != marker:
            break
        if candidate_topic_label(next_excerpt).casefold() in path_labels:
            continue
        answer_parts.append(next_excerpt)
        if len(" ".join(answer_parts).split()) >= 80:
            break
    return marker, " ".join(" ".join(answer_parts).split()[:80]).strip(" -:;,.")


def _practical_fallback_scope(
    label: str,
    *,
    kind: QuestionKind,
    alternate: bool = False,
    planned_move: str | None = None,
    scope_title: str | None = None,
    interview_format: InterviewFormat | None = None,
) -> tuple[str, list[str]]:
    """Build a reasoning prompt that never asks the candidate to recall text."""

    # Section titles are source data and may themselves contain questions,
    # sentence punctuation, or compound prompts. Never splice those verbatim
    # into a candidate-facing fallback.
    first_clause = re.split(
        r"[.!?,;:]|\b(?:and\s+then|and\s+what|and\s+how|and\s+why|"
        r"and\s+which)\b",
        _fallback_topic_label(label),
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
    if planned_move and kind == "primary":
        # The selected scope is a source heading and can carry printed labels
        # such as "Chapter 1".  A continuity fallback must introduce the
        # engineering scenario, not expose the table-of-contents label.
        clean_scope = " ".join(
            candidate_topic_label(scope_title or "").split()[:12]
        ).strip(" -:;,.?")
        # Keep product names and initialisms intact ("Proximity Service",
        # "RAG"). The surrounding template already places the phrase in a
        # grammatically neutral position.
        natural_scope = clean_scope or "this production system"
        scope_subject = re.sub(
            r"^(?:designing|creating|implementing|"
            r"building(?:\s+and\s+[a-z]+ing)?)\s+",
            "",
            natural_scope,
            flags=re.IGNORECASE,
        ) or natural_scope
        natural_topic = clean_label[:1].lower() + clean_label[1:]
        architecture_topic = re.sub(
            r"\s+architecture$", "", natural_topic, flags=re.IGNORECASE
        )
        mechanism_topic = re.sub(
            r"\s+behavio(?:u)?r$", "", natural_topic, flags=re.IGNORECASE
        )
        diagnostic_topic = re.sub(
            r"^(?:diagnosing|debugging|investigating)\s+",
            "",
            natural_topic,
            flags=re.IGNORECASE,
        )
        templates = {
            "fundamentals": (
                f"What engineering decision does {natural_topic} help you make, and why?",
                ["Explain the core idea in decision-relevant terms."],
            ),
            "mechanism": (
                f"How would you explain the behavior of {mechanism_topic} "
                "that matters most in practice?",
                ["Reason through the mechanism and its practical consequence."],
            ),
            "application": (
                f"What production decision about {natural_topic} would you make first?",
                ["Name a suitable situation and the reason it fits."],
            ),
            "requirements": (
                f"You are designing {scope_subject}. What would you clarify "
                "before proposing architecture?",
                ["Establish one material requirement or constraint before design."],
            ),
            "architecture": (
                f"Within {scope_subject}, where would you place "
                f"{architecture_topic} in the request flow?",
                ["Place the component at a coherent point in the request flow."],
            ),
            "tradeoff": (
                f"Given that approach, which trade-off involving {natural_topic} "
                "would drive your choice?",
                ["Identify one consequential engineering trade-off."],
            ),
            "diagnosis": (
                f"With that approach, you suspect {diagnostic_topic}. What would "
                "you inspect first?",
                ["Prioritize one plausible failure and a diagnostic direction."],
            ),
            "evaluation": (
                f"Before launching {scope_subject}, which "
                "operational signal would you validate first?",
                ["Choose one meaningful validation signal or test."],
            ),
        }
        if interview_format == "system_design":
            templates.update(
                {
                    "architecture": (
                        f"Within {scope_subject}, how would one request move "
                        "through the system?",
                        ["Describe a coherent request path and component boundaries."],
                    ),
                    "tradeoff": (
                        "Given that approach, which production trade-off would "
                        "you resolve next, and why?",
                        ["Choose one consequential trade-off and justify its priority."],
                    ),
                    "diagnosis": (
                        "Now assume one component degrades in production. "
                        "What would you inspect first?",
                        ["Prioritize one diagnostic signal or system boundary."],
                    ),
                    "evaluation": (
                        f"Before launching {scope_subject}, which operational "
                        "signal would you validate first?",
                        ["Choose one launch-critical operational signal."],
                    ),
                }
            )
        if planned_move in templates:
            return templates[planned_move]
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
    label = _fallback_topic_label(question.topic_label)
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
    planned_move: str | None = None,
    scope_title: str | None = None,
    candidate_probe: str | None = None,
    interview_format: InterviewFormat | None = None,
) -> InterviewQuestion:
    """Build one atomic cited question without another provider call.

    This is a continuity fallback, not a second generation strategy. It keeps
    an already-evaluated answer from being lost when both authored next-question
    drafts fail deterministic validation.
    """

    marker, excerpt = _fallback_evidence(topic)
    label = _fallback_topic_label(topic.label)
    text, expected_points = _practical_fallback_scope(
        label,
        kind=kind,
        planned_move=planned_move,
        scope_title=scope_title,
        interview_format=interview_format,
    )
    if candidate_probe:
        proposed = " ".join(candidate_probe.split()).strip()
        if proposed:
            text = proposed.rstrip(".?") + "?"
            expected_points = ["Resolve the specific ambiguity in the prior answer."]
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


def _asks_multiple_objectives(text: str) -> bool:
    """Distinguish a scenario preamble from a genuinely appended request."""

    return any(
        OBJECTIVE_CUE.search(text[: match.start()])
        for match in SECOND_OBJECTIVE.finditer(text)
    )


def _has_declarative_scenario_preamble(text: str) -> bool:
    """Allow one short setup sentence before the sole audible question."""

    match = re.fullmatch(r"\s*(?P<preamble>[^?]+\.)\s+(?P<question>[^?]+\?)\s*", text)
    if match is None:
        return False
    preamble = match.group("preamble")
    return not OBJECTIVE_CUE.search(preamble)


def validate_question_focus(question: InterviewQuestion) -> InterviewQuestion:
    """Keep one candidate turn to one atomic interview objective."""

    text = " ".join(question.text.split())
    prompt = " ".join((question.work_sample_prompt or "").split())
    maximum_words = 45 if question.coding_exercise is not None else MAX_QUESTION_WORDS
    if _word_count(text) > maximum_words:
        raise InterviewValidationError("the generated question is too broad for one turn")
    if question.coding_exercise is not None:
        if CODE_META_TASK.search(text):
            raise InterviewValidationError(
                "a coding question must not add a prose explanation or comment task"
            )
        if not CODE_OBSERVABLE_BEHAVIOR.search(text):
            raise InterviewValidationError(
                "a coding question must state its observable functional behavior"
            )
    terminal_count = len(re.findall(r"[.!?](?:\s|$)", text))
    has_code_example = question.coding_exercise is not None and bool(
        re.search(r"(?:^|\.\s+)(?:Example|For example):?\s", text, re.IGNORECASE)
    )
    allowed_terminals = (
        2
        if has_code_example or _has_declarative_scenario_preamble(text)
        else 1
    )
    if text.count("?") > 1 or terminal_count > allowed_terminals:
        raise InterviewValidationError("the generated question contains multiple prompts")
    if _asks_multiple_objectives(text):
        raise InterviewValidationError("the generated question asks for multiple objectives")
    if GENERIC_RECALL_QUESTION.search(text):
        raise InterviewValidationError(
            "the generated question tests source recall instead of understanding"
        )
    if question_is_source_dependent(text):
        raise InterviewValidationError(
            "the generated question depends on awareness of the study source"
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
    require_coding_exercise: bool = False,
    planned_move: str | None = None,
    fallback_probe: str | None = None,
    model: Any | None = None,
) -> tuple[InterviewQuestion, float]:
    """Generate with grounding and non-repetition validation, retrying once."""

    client = model or structured_model(InterviewQuestion)
    last_error: Exception | None = None
    total_cost = 0.0
    for attempt in range(QUESTION_RETRY_ATTEMPTS):
        repair = (
            f"Repair the prior draft, which was rejected because: {last_error}. "
            "Use only active-topic evidence markers, ask a "
            "materially different question, and keep exactly one atomic objective. "
            "Choose one interview move; do not combine a design choice, trade-off, "
            "failure response, metric, test, or explanation with another move. "
            "Test reasoning rather than recall of a source heading, and include only "
            "private expected points that the audible question explicitly requests. "
            "The screen instruction may change the response format but must not add "
            "another task."
            if attempt
            else None
        )
        required_format = (
            "This turn must be a source-grounded Python coding exercise. The audible "
            "question must explicitly ask the candidate to implement, write, or debug "
            "code, and the response must include work_sample `code` plus a complete "
            "coding_exercise scaffold. Do not substitute a verbal question."
            if require_coding_exercise
            else None
        )
        adaptive_purpose = " ".join(
            value for value in [purpose, required_format, repair] if value
        )
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
                    planned_move=planned_move,
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
            focused = validate_question_focus(focused)
            if require_coding_exercise and (
                focused.work_sample != "code" or focused.coding_exercise is None
            ):
                raise InterviewValidationError(
                    "the requested coding exercise was replaced by a verbal question"
                )
            return focused, total_cost
        except InterviewValidationError as error:
            last_error = error
    reason = str(last_error or "question validation failed")
    if require_coding_exercise:
        raise InterviewModelError(
            f"could not generate the requested coding exercise: {reason}"
        ) from last_error
    logger.warning(
        "Using grounded fallback after question generation failed validation: %s",
        reason,
        extra={
            "topic_key": topic.key,
            "question_kind": kind,
            "reason": reason,
        },
    )
    return (
        grounded_fallback_question(
            topic=topic,
            target_level=target_level,
            kind=kind,
            recent_questions=recent_questions,
            planned_move=planned_move,
            scope_title=inventory.title,
            candidate_probe=fallback_probe,
            interview_format=interview_format,
        ),
        total_cost,
    )
