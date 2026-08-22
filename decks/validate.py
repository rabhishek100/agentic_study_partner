"""Deterministic validation and metrics for generated cards.

Nothing here asks a model whether a card is good. Every check is a comparison
the application can make on its own — against markers it built, a topic list it
built, and cards it has already kept — which is what makes the numbers on a
deck row worth reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .contracts import (
    MCQ_OPTION_COUNT,
    CardBack,
    DeckCard,
    DeckCitation,
    DeckMetrics,
    GeneratedCard,
)
from .topics import Topic

BOOK_MARKER = re.compile(r"\[N(\d+):P(\d+)\]")
LECTURE_MARKER = re.compile(r"\[S(\d+)\]")
ANY_MARKER = re.compile(r"\[(?:N\d+:P\d+|S\d+)\]")
WHITESPACE = re.compile(r"\s+")

DropReason = str
DROP_MALFORMED: DropReason = "malformed"
DROP_UNCITED: DropReason = "uncited"
DROP_OUT_OF_SCOPE: DropReason = "out_of_scope"
DROP_DUPLICATE: DropReason = "duplicate"


@dataclass
class ValidationTally:
    """Running counts across one deck's generation."""

    generated: int = 0
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    with_angle: int = 0
    card_types: dict[str, int] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)
    curated_out: int = 0

    def drop(self, reason: DropReason) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def keep(self, card: DeckCard) -> None:
        self.kept += 1
        self.card_types[card.card_type] = self.card_types.get(card.card_type, 0) + 1
        key = str(card.interview_priority)
        self.priorities[key] = self.priorities.get(key, 0) + 1
        if card.interview_angle:
            self.with_angle += 1

    def remove_for_curation(self, card: DeckCard) -> None:
        """Remove one valid but lower-signal card from the published set."""

        self.kept -= 1
        self.curated_out += 1
        card_type_count = self.card_types.get(card.card_type, 0) - 1
        if card_type_count > 0:
            self.card_types[card.card_type] = card_type_count
        else:
            self.card_types.pop(card.card_type, None)
        priority = str(card.interview_priority)
        priority_count = self.priorities.get(priority, 0) - 1
        if priority_count > 0:
            self.priorities[priority] = priority_count
        else:
            self.priorities.pop(priority, None)
        if card.interview_angle:
            self.with_angle -= 1


def parse_marker(marker: str) -> DeckCitation | None:
    """Turn one evidence marker into a locator, or nothing if it is not one."""

    book = BOOK_MARKER.fullmatch(marker.strip())
    if book:
        return DeckCitation(
            marker=marker.strip(),
            node_id=int(book.group(1)),
            page=int(book.group(2)),
        )
    lecture = LECTURE_MARKER.fullmatch(marker.strip())
    if lecture:
        return DeckCitation(marker=marker.strip(), evidence_rank=int(lecture.group(1)))
    return None


def normalized_front(front: str) -> str:
    """A comparison key for near-identical fronts.

    Two cards that differ only in punctuation and capitalisation are one card
    that got asked for twice, and a deck that shows you the same question in
    two costumes wastes the review budget the whole feature exists to spend
    well.
    """

    # Markers come out before casefolding: the marker pattern is upper case by
    # construction and would stop matching afterwards.
    stripped = ANY_MARKER.sub(" ", front).casefold()
    return WHITESPACE.sub(" ", re.sub(r"[^\w\s]", " ", stripped)).strip()


def back_is_well_formed(card_type: str, back: CardBack) -> bool:
    """Whether the back carries what its own card type needs to be usable."""

    if card_type == "mcq":
        if len(back.options) != MCQ_OPTION_COUNT:
            return False
        if len({option.label for option in back.options}) != MCQ_OPTION_COUNT:
            return False
        return sum(option.correct for option in back.options) == 1
    if card_type == "system_design":
        # A design card earns its type by being glanceable structure. Prose
        # alone is a `qa` card that mislabelled itself.
        return bool(back.components or back.data_flow) and bool(
            back.answer.strip() or back.say_it_aloud.strip()
        )
    return bool(back.answer.strip())


def _back_text(back: CardBack) -> str:
    return "\n".join(
        [
            back.answer,
            back.why_it_matters,
            back.say_it_aloud,
            *back.key_points,
            *back.components,
            *back.data_flow,
            *back.trade_offs,
            *back.failure_modes,
            *(f"{option.text} {option.rationale}" for option in back.options),
        ]
    )


def validate_card(
    generated: GeneratedCard,
    topic: Topic,
    *,
    card_index: int,
    seen_fronts: set[str],
) -> tuple[DeckCard | None, DropReason | None]:
    """Keep one card, or say why it was dropped.

    Markers are checked in both places they can appear: the declared
    `citation_markers`, and inline in the back's own text. A card that cites
    correctly in its metadata while quoting a marker from another chapter in
    its answer would send a reader to the wrong page, which is the failure this
    whole citation contract exists to prevent.
    """

    if not back_is_well_formed(generated.card_type, generated.back):
        return None, DROP_MALFORMED

    declared = [marker.strip() for marker in generated.citation_markers if marker.strip()]
    inline = ANY_MARKER.findall(_back_text(generated.back))
    if not declared and not inline:
        return None, DROP_UNCITED

    markers = list(dict.fromkeys([*declared, *inline]))
    if any(marker not in topic.allowed_markers for marker in markers):
        return None, DROP_OUT_OF_SCOPE

    citations = [parsed for marker in markers if (parsed := parse_marker(marker))]
    if not citations:
        return None, DROP_UNCITED

    key = normalized_front(generated.front)
    if not key or key in seen_fronts:
        return None, DROP_DUPLICATE

    seen_fronts.add(key)
    return (
        DeckCard(
            topic_key=topic.key,
            card_index=card_index,
            card_type=generated.card_type,
            front=generated.front.strip(),
            back=generated.back,
            citations=citations,
            figures=list(_figures_for(topic, citations)),
            interview_priority=generated.interview_priority,
            priority_reason=generated.priority_reason.strip(),
            difficulty=generated.difficulty,
            interview_angle=(generated.interview_angle or "").strip() or None,
        ),
        None,
    )


def _figures_for(topic: Topic, citations: list[DeckCitation]) -> list:
    """Attach a topic's figures to a card that cites the page they sit on.

    A design card about a diagram should show the diagram. A definition card
    from the same section should not drag it along.
    """

    pages = {citation.page for citation in citations if citation.page is not None}
    ranks = {
        citation.evidence_rank
        for citation in citations
        if citation.evidence_rank is not None
    }
    if not topic.figures:
        return []
    if pages:
        return [figure for figure in topic.figures if figure.page in pages]
    # A lecture frame is cited by its own rank, which is in `ranks` when the
    # card discussed what was on screen.
    return [figure for figure in topic.figures if ranks]


def build_metrics(
    tally: ValidationTally,
    *,
    topics: tuple[Topic, ...],
    covered_keys: set[str],
    repair_attempted: bool,
) -> DeckMetrics:
    required = [topic for topic in topics if topic.required]
    uncovered = [topic for topic in required if topic.key not in covered_keys]
    return DeckMetrics(
        topics_total=len(topics),
        topics_required=len(required),
        topics_covered=len(required) - len(uncovered),
        uncovered_topic_labels=[topic.label for topic in uncovered],
        cards_generated=tally.generated,
        cards_kept=tally.kept,
        cards_dropped_uncited=tally.dropped.get(DROP_UNCITED, 0),
        cards_dropped_out_of_scope=tally.dropped.get(DROP_OUT_OF_SCOPE, 0),
        cards_dropped_duplicate=tally.dropped.get(DROP_DUPLICATE, 0),
        cards_dropped_malformed=tally.dropped.get(DROP_MALFORMED, 0),
        cards_curated_out=tally.curated_out,
        cards_with_interview_angle=tally.with_angle,
        card_type_counts=dict(sorted(tally.card_types.items())),
        priority_counts=dict(sorted(tally.priorities.items())),
        repair_attempted=repair_attempted,
    )
