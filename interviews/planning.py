"""Deterministic source inventory, format detection, and pacing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from decks.pipeline import DeckSourceError, load_inventory
from decks.topics import ScopeInventory, Topic

from .contracts import (
    FormatChoice,
    InterviewCheckpoint,
    InterviewFormat,
    InterviewPreflight,
    TargetLevel,
    TopicState,
)


SOURCE_LED = re.compile(
    r"\b(interview|case\s+study|walkthrough|mock\s+interview|design\s+exercise)\b",
    re.IGNORECASE,
)
SYSTEM_DESIGN = re.compile(
    r"\b(system\s+design|architecture|scalab(?:le|ility)|distributed|"
    r"requirements?|capacity|throughput|latency|failure\s+modes?|trade[ -]?offs?)\b",
    re.IGNORECASE,
)


class InterviewSourceError(RuntimeError):
    """The selected source cannot support an interview."""


@dataclass(frozen=True)
class LoadedInterviewSource:
    inventory: ScopeInventory
    ingestion_version_id: UUID | None


def load_source(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    book_id: int | None = None,
    node_id: int | None = None,
    video_id: str | UUID | None = None,
) -> LoadedInterviewSource:
    """Reuse the measured deck inventory as the interview coverage contract."""

    try:
        inventory, version_id = load_inventory(
            connection,
            owner_id=owner_id,
            source_kind=source_kind,
            book_id=book_id,
            node_id=node_id,
            video_id=video_id,
        )
    except DeckSourceError as error:
        raise InterviewSourceError(str(error)) from error
    if not inventory.required_topics:
        raise InterviewSourceError(
            "this source has no substantive, citable topics for an interview"
        )
    return LoadedInterviewSource(inventory=inventory, ingestion_version_id=version_id)


def detect_format(inventory: ScopeInventory) -> InterviewFormat:
    """Classify from source structure and vocabulary without spending a model call."""

    sample = "\n".join(
        [inventory.title, inventory.source_title, inventory.outline]
        + [topic.evidence_text[:1_200] for topic in inventory.topics[:8]]
    )
    if SOURCE_LED.search(sample):
        # A system-design chapter that explicitly presents an interview should
        # keep the source's sequence rather than be rearranged into our generic
        # design template.
        return "source_led"
    system_hits = len(SYSTEM_DESIGN.findall(sample))
    if system_hits >= 3:
        return "system_design"
    return "concept"


def estimate_duration(
    inventory: ScopeInventory, *, target_level: TargetLevel
) -> tuple[int, int]:
    """Return an honest range; the chosen duration remains only a ceiling."""

    required = len(inventory.required_topics)
    per_topic = {
        "entry": (2.0, 3.5),
        "mid": (2.5, 4.5),
        "senior": (3.0, 5.5),
    }[target_level]
    minimum = round(4 + required * per_topic[0])
    maximum = round(7 + required * per_topic[1])
    return max(10, min(minimum, 120)), max(15, min(maximum, 120))


def preflight(
    source: LoadedInterviewSource,
    *,
    target_level: TargetLevel,
    format_choice: FormatChoice = "auto",
) -> InterviewPreflight:
    inventory = source.inventory
    detected = detect_format(inventory)
    selected = detected if format_choice == "auto" else format_choice
    minimum, maximum = estimate_duration(inventory, target_level=target_level)
    warnings: list[str] = []
    if len(inventory.required_topics) == 1:
        warnings.append(
            "This source has one substantive topic, so the interview may finish quickly."
        )
    return InterviewPreflight(
        source_kind=inventory.source_kind,
        scope_key=inventory.scope_key,
        title=inventory.title,
        source_title=inventory.source_title,
        detected_format=detected,
        selected_format=selected,
        format_source="detected" if format_choice == "auto" else "override",
        topic_count=len(inventory.topics),
        required_topic_count=len(inventory.required_topics),
        estimated_min_minutes=minimum,
        estimated_max_minutes=maximum,
        warnings=warnings,
    )


def initial_checkpoint(inventory: ScopeInventory) -> InterviewCheckpoint:
    return InterviewCheckpoint(
        topics=[
            TopicState(key=topic.key, label=topic.label, required=topic.required)
            for topic in inventory.topics
        ]
    )


def topic_by_key(inventory: ScopeInventory, key: str) -> Topic:
    try:
        return next(topic for topic in inventory.topics if topic.key == key)
    except StopIteration as error:
        raise InterviewSourceError("the session topic no longer exists") from error


def next_topic(
    inventory: ScopeInventory, checkpoint: InterviewCheckpoint
) -> Topic | None:
    by_key = {topic.key: topic for topic in checkpoint.topics}
    for topic in inventory.topics:
        state = by_key.get(topic.key)
        if state and state.required and not state.completed:
            return topic
    return None
