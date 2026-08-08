"""Typed boundaries for decks, cards, and review state.

Two card shapes travel through this module and they are deliberately not the
same type. `GeneratedCard` is what a model is allowed to emit: flat, permissive
about which fields a given card type uses, because a structured-output schema
with a discriminated union is fragile in practice. `DeckCard` is what survives
validation and gets stored: its citations resolved, its type-specific fields
checked, its identity assigned by the database.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CardType = Literal["qa", "concept", "mcq", "system_design"]
Difficulty = Literal["foundational", "intermediate", "advanced"]
SourceKind = Literal["book", "video"]
DeckStatus = Literal["generating", "ready", "partial", "failed"]
ReviewStateName = Literal["new", "learning", "review", "relearning"]

# Anki's four, and for the same reason: three buttons cannot separate "I had to
# think" from "that was instant", and that difference is most of the signal a
# scheduler has.
Rating = Literal[1, 2, 3, 4]
RATING_AGAIN: Rating = 1
RATING_HARD: Rating = 2
RATING_GOOD: Rating = 3
RATING_EASY: Rating = 4

# An MCQ with three options is a coin flip with extra steps, and one with six
# needs distractors the evidence cannot support. Four is what every study app
# that works settled on.
MCQ_OPTION_COUNT = 4


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeckCitation(ContractModel):
    """Where one claim on a card back came from.

    A book citation names a node and a page; a lecture citation names an
    evidence rank and the moment it sits at. One citation never carries both,
    for the same reason `VideoCitationRef` does not: a page of a slide deck and
    a moment of the recording are not known to be the same place.
    """

    marker: str = Field(min_length=1)
    node_id: int | None = None
    page: int | None = None
    evidence_rank: int | None = Field(default=None, gt=0)
    start_ms: int | None = Field(default=None, ge=0)
    frame_id: int | None = None

    @model_validator(mode="after")
    def one_locator_kind(self) -> "DeckCitation":
        book = self.node_id is not None and self.page is not None
        lecture = self.evidence_rank is not None
        if book == lecture:
            raise ValueError(
                "a citation locates either a book page or a lecture moment"
            )
        return self


class DeckFigure(ContractModel):
    """A diagram shown with the back of a card.

    Payloads are never inlined here for the same reason `FigureRef` does not
    inline them: a deck carrying base64 images would balloon every response and
    every stored row. The interface fetches bytes from the existing image
    endpoints using these identifiers.
    """

    kind: Literal["book_image", "lecture_frame"]
    caption: str | None = None
    # Book locator.
    book_id: int | None = None
    node_id: int | None = None
    block_id: int | None = None
    page: int | None = None
    mime_type: str | None = None
    # Lecture locator.
    frame_id: int | None = None
    start_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def locator_matches_kind(self) -> "DeckFigure":
        if self.kind == "book_image":
            if self.book_id is None or self.block_id is None:
                raise ValueError("a book figure needs a book and a block")
        elif self.frame_id is None:
            raise ValueError("a lecture frame needs a frame id")
        return self


class McqOption(ContractModel):
    label: Literal["A", "B", "C", "D"]
    text: str = Field(min_length=1)
    correct: bool
    # Why this option is wrong — or, for the correct one, why it is right.
    # A distractor you cannot explain is a distractor that teaches nothing.
    rationale: str = Field(default="", max_length=600)


class CardBack(ContractModel):
    """Everything that can appear on the reverse of a card.

    One model rather than four, because the fields a type does not use are
    simply absent and the interface switches on `card_type` regardless. What
    keeps this honest is `validate_back_for_type` in `decks.validate`, which
    rejects a back that is empty for the type it claims to be.
    """

    # qa, concept, system_design
    answer: str = Field(default="", max_length=4_000)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    # The compressed version you could say out loud in an interview. This is
    # the line the review interface shows largest.
    say_it_aloud: str = Field(default="", max_length=400)
    # concept
    why_it_matters: str = Field(default="", max_length=1_000)
    # mcq
    options: list[McqOption] = Field(default_factory=list, max_length=4)
    # system_design
    components: list[str] = Field(default_factory=list, max_length=10)
    data_flow: list[str] = Field(default_factory=list, max_length=10)
    trade_offs: list[str] = Field(default_factory=list, max_length=8)
    failure_modes: list[str] = Field(default_factory=list, max_length=8)

    @property
    def correct_option(self) -> McqOption | None:
        return next((option for option in self.options if option.correct), None)


class GeneratedCard(ContractModel):
    """One card exactly as a model emitted it, before validation.

    Citations arrive as raw marker strings. Resolving them against the topic's
    allowed markers is the application's job, not the model's: a model asked to
    emit structured locators will happily emit plausible ones.
    """

    # Which numbered topic of the batch this card is about. Declared by the
    # model, then verified against the card's markers rather than trusted: a
    # batch containing three topics is exactly where attribution slips.
    topic_ordinal: int = Field(ge=1)
    card_type: CardType
    front: str = Field(min_length=1, max_length=1_000)
    back: CardBack
    citation_markers: list[str] = Field(default_factory=list, max_length=12)
    interview_priority: int = Field(default=3, ge=1, le=5)
    priority_reason: str = Field(default="", max_length=300)
    difficulty: Difficulty = "intermediate"
    # The labelled model-knowledge layer. Optional by construction: most cards
    # do not need one, and an angle invented for every card is noise.
    interview_angle: str | None = Field(default=None, max_length=600)


class TopicCards(ContractModel):
    """The structured-output schema one generation call returns."""

    cards: list[GeneratedCard] = Field(default_factory=list, max_length=12)


class DeckCard(ContractModel):
    """A validated card, ready to store or to render."""

    card_id: str | None = None
    topic_key: str = Field(min_length=1)
    card_index: int = Field(ge=0)
    card_type: CardType
    front: str = Field(min_length=1)
    back: CardBack
    citations: list[DeckCitation] = Field(min_length=1)
    figures: list[DeckFigure] = Field(default_factory=list)
    interview_priority: int = Field(ge=1, le=5)
    priority_reason: str = ""
    difficulty: Difficulty = "intermediate"
    interview_angle: str | None = None


class DeckMetrics(ContractModel):
    """What generation actually achieved, computed without a judge.

    Every number here is derived from a comparison the application can make on
    its own: a topic list it built before any model call, and markers it can
    resolve. Nothing in this model is a model's opinion of its own work.
    """

    topics_total: int = Field(default=0, ge=0)
    topics_required: int = Field(default=0, ge=0)
    topics_covered: int = Field(default=0, ge=0)
    uncovered_topic_labels: list[str] = Field(default_factory=list)
    cards_generated: int = Field(default=0, ge=0)
    cards_kept: int = Field(default=0, ge=0)
    cards_dropped_uncited: int = Field(default=0, ge=0)
    cards_dropped_out_of_scope: int = Field(default=0, ge=0)
    cards_dropped_duplicate: int = Field(default=0, ge=0)
    cards_dropped_malformed: int = Field(default=0, ge=0)
    cards_with_interview_angle: int = Field(default=0, ge=0)
    card_type_counts: dict[str, int] = Field(default_factory=dict)
    priority_counts: dict[str, int] = Field(default_factory=dict)
    repair_attempted: bool = False

    @property
    def coverage_ratio(self) -> float:
        if not self.topics_required:
            return 1.0
        return self.topics_covered / self.topics_required

    @property
    def complete(self) -> bool:
        return self.topics_covered >= self.topics_required


class DeckSummary(ContractModel):
    """One row of the deck library."""

    deck_id: str
    source_kind: SourceKind
    scope_key: str
    version: int
    title: str
    source_title: str
    status: DeckStatus
    card_count: int
    topic_count: int
    book_id: int | None = None
    node_id: int | None = None
    video_id: str | None = None
    metrics: DeckMetrics = Field(default_factory=DeckMetrics)
    due_count: int = 0
    new_count: int = 0
    updated_at: str | None = None


class ReviewState(ContractModel):
    """Scheduling state for one card, derived from its review log."""

    state: ReviewStateName = "new"
    due_at: str | None = None
    interval_days: float = Field(default=0.0, ge=0)
    ease: float = Field(default=2.5, ge=1.3)
    reps: int = Field(default=0, ge=0)
    lapses: int = Field(default=0, ge=0)
    last_reviewed_at: str | None = None
    last_rating: int | None = Field(default=None, ge=1, le=4)


class QueueCard(ContractModel):
    """One card as the review interface receives it."""

    card: DeckCard
    deck_id: str
    deck_title: str
    source_kind: SourceKind
    source_title: str
    book_id: int | None = None
    video_id: str | None = None
    review: ReviewState = Field(default_factory=ReviewState)


class ReviewQueue(ContractModel):
    cards: list[QueueCard] = Field(default_factory=list)
    due_total: int = 0
    new_total: int = 0
    reviewed_today: int = 0
    new_cards_per_day: int = 10
    max_reviews_per_day: int = 120


class DeckPreferences(ContractModel):
    new_cards_per_day: int = Field(default=10, ge=0, le=200)
    max_reviews_per_day: int = Field(default=120, ge=1, le=1_000)
