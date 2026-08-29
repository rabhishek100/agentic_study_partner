"""Deterministic interview-track and session-progression policy.

The source remains the grounding boundary. This module decides only which
kind of interview signal to seek next so a chapter does not turn into a list
of unrelated recall questions.
"""

from __future__ import annotations

import re
from math import ceil
from dataclasses import dataclass
from typing import Literal

from decks.topics import ScopeInventory

from .contracts import InterviewFormat, TargetLevel


InterviewTrack = Literal[
    "machine_learning_ai",
    "software_engineering",
    "general_technical",
]
InterviewMove = Literal[
    "fundamentals",
    "mechanism",
    "application",
    "requirements",
    "architecture",
    "tradeoff",
    "diagnosis",
    "evaluation",
]


ML_AI_SIGNAL = re.compile(
    r"\b(?:artificial intelligence|ai engineer(?:ing)?|embedding|feature|"
    r"inference|large language model|llm|machine learning|ml system|model|"
    r"neural|prompt|rag|retriev\w*|training)\b",
    re.IGNORECASE,
)
SOFTWARE_SIGNAL = re.compile(
    r"\b(?:algorithm|api|cache|database|distributed|endpoint|queue|service|"
    r"software|storage|throughput|web)\b",
    re.IGNORECASE,
)

DESIGN_MOVE_SIGNALS: dict[InterviewMove, re.Pattern[str]] = {
    "requirements": re.compile(
        r"\b(?:availability|constraint|goal|latency|privacy|requirement|scale|"
        r"scope|throughput|traffic)\w*\b",
        re.IGNORECASE,
    ),
    "architecture": re.compile(
        r"\b(?:api|architecture|component|database|data flow|index|pipeline|"
        r"queue|service|storage)\w*\b",
        re.IGNORECASE,
    ),
    "tradeoff": re.compile(
        r"\b(?:accuracy|cache|consistency|cost|freshness|latency|precision|"
        r"read|recall|trade[ -]?off|versus|write)\w*\b",
        re.IGNORECASE,
    ),
    "diagnosis": re.compile(
        r"\b(?:cache|delete|error|fail|index|missing|outage|replica|retry|"
        r"stale|timeout)\w*\b",
        re.IGNORECASE,
    ),
    "evaluation": re.compile(
        r"\b(?:accuracy|availability|cost|evaluate|experiment|latency|metric|"
        r"monitor|precision|recall|test|throughput|validate)\w*\b",
        re.IGNORECASE,
    ),
}

SOURCE_DEPENDENT_QUESTION = re.compile(
    r"\bchapter\s+\d+(?:\.\d+)*\b|"
    r"\b(?:according to|as (?:stated|described|explained) in)\s+"
    r"(?:(?:the|this|your)\s+)?(?:author|book|chapter|lecture|section|source)\b|"
    r"\b(?:from|in)\s+(?:(?:the|this|your)\s+)?"
    r"(?:book|chapter|lecture|section|source)\b|"
    r"\b(?:the|this)\s+(?:author|book|chapter|lecture|section|source)\s+"
    r"(?:calls?|describes?|lists?|mentions?|says?|uses?)\b|"
    r"\bsource[ -]?grounded\b",
    re.IGNORECASE,
)


CONCEPT_ARCS: dict[TargetLevel, tuple[InterviewMove, ...]] = {
    "entry": ("fundamentals", "mechanism", "application", "diagnosis"),
    "mid": ("fundamentals", "application", "diagnosis", "tradeoff"),
    "senior": ("application", "architecture", "tradeoff", "diagnosis"),
}
DESIGN_ARC: tuple[InterviewMove, ...] = (
    "requirements",
    "architecture",
    "tradeoff",
    "diagnosis",
    "evaluation",
)

MOVE_GUIDANCE: dict[InterviewMove, str] = {
    "fundamentals": (
        "establish one core mental model or prerequisite in the candidate's own "
        "words; do not ask for a definition copied from the source"
    ),
    "mechanism": (
        "ask the candidate to reason through how or why the mechanism works"
    ),
    "application": (
        "place the concept in a plausible engineering situation and ask for one "
        "decision or approach"
    ),
    "requirements": (
        "open the design discussion with one material goal, constraint, or "
        "requirement the candidate must establish"
    ),
    "architecture": (
        "ask for one coherent design boundary, component relationship, or data flow"
    ),
    "tradeoff": (
        "ask for one consequential engineering choice and the trade-off that drives it"
    ),
    "diagnosis": (
        "present one realistic failure, edge condition, or changed assumption and ask "
        "how the candidate would diagnose or handle it"
    ),
    "evaluation": (
        "ask how the candidate would validate the solution with a meaningful metric, "
        "test, or operational signal"
    ),
}


@dataclass(frozen=True)
class InterviewMovePlan:
    track: InterviewTrack
    move: InterviewMove
    guidance: str


def infer_interview_track(inventory: ScopeInventory) -> InterviewTrack:
    """Infer a broad role family without requiring company- or title-specific rules."""

    sample = "\n".join(
        [inventory.source_title, inventory.title, inventory.outline]
        + [topic.evidence_text[:800] for topic in inventory.topics[:8]]
    )
    ml_hits = len(ML_AI_SIGNAL.findall(sample))
    software_hits = len(SOFTWARE_SIGNAL.findall(sample))
    if ml_hits >= 2 and ml_hits >= software_hits:
        return "machine_learning_ai"
    if software_hits >= 2:
        return "software_engineering"
    return "general_technical"


def planned_interview_move(
    inventory: ScopeInventory,
    *,
    interview_format: InterviewFormat,
    target_level: TargetLevel,
    areas_visited: int,
    planned_area_count: int,
) -> InterviewMovePlan:
    """Spread realistic interview moves across the available first-pass plan."""

    arc = (
        DESIGN_ARC
        if interview_format in {"system_design", "source_led"}
        else CONCEPT_ARCS[target_level]
    )
    denominator = max(1, planned_area_count - 1)
    # When a source has more topics than phases, repeat middle/late moves rather
    # than asking two near-identical requirements questions at the opening.
    index = min(
        len(arc) - 1,
        ceil(areas_visited * (len(arc) - 1) / denominator),
    )
    move = arc[index]
    return InterviewMovePlan(
        track=infer_interview_track(inventory),
        move=move,
        guidance=MOVE_GUIDANCE[move],
    )


def minimum_design_moves(maximum_duration_minutes: int) -> int:
    """Return the phase floor that fits the selected system-design duration."""

    if maximum_duration_minutes <= 15:
        return 3
    if maximum_duration_minutes < 30:
        return 4
    return len(DESIGN_ARC)


def design_question_target(
    *, planned_area_count: int, maximum_duration_minutes: int
) -> int:
    """Keep compact sources from ending before a credible design arc forms."""

    return max(planned_area_count, minimum_design_moves(maximum_duration_minutes))


def topic_for_design_move(
    inventory: ScopeInventory,
    *,
    move: InterviewMove,
    attempts_by_key: dict[str, int] | None = None,
):
    """Choose the best source-backed topic for an otherwise missing design phase.

    No keyword support means no forced question. That preserves the source as
    the grounding boundary instead of padding a short interview with invention.
    """

    pattern = DESIGN_MOVE_SIGNALS.get(move)
    if pattern is None:
        return None
    attempts = attempts_by_key or {}
    ranked = []
    for topic in inventory.required_topics:
        sample = f"{topic.label}\n{topic.evidence_text}"
        hits = len(pattern.findall(sample))
        if hits:
            ranked.append((-hits, attempts.get(topic.key, 0), topic.ordinal, topic))
    return min(ranked, default=(0, 0, 0, None))[-1]


def interview_move_purpose(plan: InterviewMovePlan) -> str:
    role = {
        "machine_learning_ai": "machine-learning or AI engineering",
        "software_engineering": "software engineering",
        "general_technical": "technical engineering",
    }[plan.track]
    return (
        f"Shape this as a {role} interview move. The planned move is "
        f"`{plan.move}`: {plan.guidance}. Use a different move only when the "
        "active evidence cannot ground this one."
    )


def question_is_source_dependent(text: str) -> bool:
    """Whether a candidate needs awareness of the study source to parse the task."""

    return bool(SOURCE_DEPENDENT_QUESTION.search(text))
