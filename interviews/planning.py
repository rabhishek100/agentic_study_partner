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
CODING_SIGNAL = re.compile(
    r"\b(?:algorithm|array|class|code|comput\w*|function|gradient|implement\w*|"
    r"input|loss|matrix|model|output|predict\w*|probabil\w*|pseudocode|return|"
    r"search|sort|tree|vector)\b",
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
        coding_topic_count=sum(
            topic_supports_coding(topic) for topic in inventory.required_topics
        ),
        estimated_min_minutes=minimum,
        estimated_max_minutes=maximum,
        warnings=warnings,
    )


def topic_supports_coding(topic: Topic) -> bool:
    """Whether this evidence can ground a small executable programming task."""

    sample = f"{topic.label}\n{topic.evidence_text}"
    signals = {match.group(0).casefold() for match in CODING_SIGNAL.finditer(sample)}
    return len(signals) >= 2


def next_coding_topic(
    inventory: ScopeInventory,
    checkpoint: InterviewCheckpoint,
) -> Topic | None:
    """Return the first unseen substantive topic that supports executable work."""

    by_key = {topic.key: topic for topic in checkpoint.topics}
    return next(
        (
            topic
            for topic in inventory.required_topics
            if (state := by_key.get(topic.key))
            and state.attempts == 0
            and topic_supports_coding(topic)
        ),
        None,
    )


MINUTES_PER_PLANNED_TOPIC = {
    "entry": 2.5,
    "mid": 3.0,
    "senior": 3.5,
}


def planned_topics(
    inventory: ScopeInventory,
    *,
    maximum_duration_minutes: int,
    target_level: TargetLevel,
) -> tuple[Topic, ...]:
    """Select a chapter-wide primary-question plan that fits the time ceiling."""

    required = list(inventory.required_topics)
    if not required:
        return ()
    usable_minutes = max(8.0, maximum_duration_minutes - 3.0)
    capacity = max(
        3,
        int(usable_minutes / MINUTES_PER_PLANNED_TOPIC[target_level]),
    )
    if len(required) <= capacity:
        return tuple(required)

    selected: list[Topic] = []
    for index in range(capacity):
        start = index * len(required) // capacity
        end = (index + 1) * len(required) // capacity
        window = required[start:max(start + 1, end)]
        representative = min(
            window,
            key=lambda item: (
                len([part for part in item.label.split(" :: ") if part.strip()]),
                -len(item.evidence_text),
                item.ordinal,
            ),
        )
        selected.append(representative)
    # Windows do not overlap, so their representatives are already unique.
    # Do not hash Topic objects: a book topic may contain Pydantic-backed
    # figure references, which are intentionally unhashable.
    return tuple(sorted(selected, key=lambda item: item.ordinal))


def initial_checkpoint(
    inventory: ScopeInventory,
    *,
    maximum_duration_minutes: int = 120,
    target_level: TargetLevel = "mid",
) -> InterviewCheckpoint:
    planned_keys = {
        topic.key
        for topic in planned_topics(
            inventory,
            maximum_duration_minutes=maximum_duration_minutes,
            target_level=target_level,
        )
    }
    return InterviewCheckpoint(
        topics=[
            TopicState(
                key=topic.key,
                label=topic.label,
                required=topic.required and topic.key in planned_keys,
            )
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
    # Breadth first: visit every planned area before revisiting a local gap.
    for topic in inventory.topics:
        state = by_key.get(topic.key)
        if state and state.required and state.attempts == 0:
            return topic
    incomplete = [
        topic
        for topic in inventory.topics
        if (
            (state := by_key.get(topic.key))
            and state.required
            and not state.completed
        )
    ]
    if not incomplete:
        return None
    return min(
        incomplete,
        key=lambda topic: (by_key[topic.key].best_score, topic.ordinal),
    )
