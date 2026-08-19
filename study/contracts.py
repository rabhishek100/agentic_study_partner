"""Small typed boundaries for conversation, evidence, and results."""

from string import Formatter
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

Route = Literal[
    "library_list",
    "hierarchy_summary",
    "hierarchy_list",
    "retrieval_qa",
    "prior_answer_transform",
    "clarify",
    "external_qa",
]
HistoryDependency = Literal["independent", "dependent", "ambiguous"]
Outcome = Literal["answer", "clarify", "abstain", "error"]
AnswerArchetype = Literal[
    "concept_explanation",
    "system_design",
    "chapter_review",
    "answer_transform",
]
ResponseDepth = Literal["quick", "interview", "deep"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationMessage(ContractModel):
    role: Literal["user", "assistant"]
    content: str
    turn_id: str | None = None


class PromptProfile(ContractModel):
    """Editable interview behavior layered under locked grounding rules."""

    interview_instructions: str = Field(min_length=20, max_length=12_000)
    concept_template: str = Field(min_length=20, max_length=8_000)
    system_design_template: str = Field(min_length=20, max_length=8_000)
    chapter_review_template: str = Field(min_length=20, max_length=8_000)
    user_prompt_template: str = Field(min_length=20, max_length=8_000)

    @field_validator(
        "interview_instructions",
        "concept_template",
        "system_design_template",
        "chapter_review_template",
        "user_prompt_template",
    )
    @classmethod
    def strip_prompt_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("user_prompt_template")
    @classmethod
    def validate_user_template(cls, value: str) -> str:
        allowed = {
            "question",
            "answer_archetype",
            "response_depth",
            "evidence",
            "request_context",
        }
        fields = {
            field_name for _, field_name, _, _ in Formatter().parse(value) if field_name
        }
        unsupported = fields.difference(allowed)
        if unsupported:
            raise ValueError(
                "unsupported prompt placeholders: " + ", ".join(sorted(unsupported))
            )
        required = {"question", "evidence"}
        missing = required.difference(fields)
        if missing:
            raise ValueError(
                "user prompt template must include: " + ", ".join(sorted(missing))
            )
        return value


class ScopeRef(ContractModel):
    kind: Literal["book", "chapter", "section"]
    book_id: int
    node_id: int | None = None
    display_path: str
    start_page: int
    end_page: int


class ScopeCandidate(ContractModel):
    book_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    kind: Literal["chapter", "section"]
    title: str = Field(min_length=1)
    display_path: str = Field(min_length=1)
    start_page: int = Field(gt=0)
    end_page: int = Field(gt=0)
    match_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pages(self) -> "ScopeCandidate":
        if self.end_page < self.start_page:
            raise ValueError("end_page must be at least start_page")
        return self


class EvidenceRef(ContractModel):
    node_id: int
    pages: list[int]
    path: str
    # Book identity is what makes a reference readable once evidence can span
    # several books: a page number alone says nothing about which book it is
    # in. Producers always populate these; they are optional only so that a
    # conversation state serialized before this field existed still loads.
    # Stage 3 moves conversation state server-side and can then require them.
    book_id: int | None = None
    book_title: str | None = None
    rank: int | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    retrieval_method: str | None = None
    score: float | None = None
    excerpt: str | None = None


class CitationRef(ContractModel):
    marker: str
    node_id: int
    page: int
    book_id: int | None = None
    evidence_rank: int | None = None


class FigureRef(ContractModel):
    """One figure that sits inside the evidence an answer rests on.

    The payload is never inlined: a result carrying base64 images would
    balloon every response and every persisted turn. The interface fetches
    bytes from the image endpoint using `book_id` and `block_id`.
    """

    book_id: int
    node_id: int
    block_id: int
    page: int
    mime_type: str
    path: str
    caption: str | None = None
    evidence_rank: int | None = None


# The longest passage a side chat will store as an anchor. Set from measured
# answer lengths rather than guessed: whole-answer anchors are the common case.
MAXIMUM_QUOTE_CHARS = 16_000

# More than a handful of references in one small window stops being a focused
# question, and every anchor pins evidence that competes with retrieval for the
# same context budget.
MAXIMUM_ANCHORS = 5


# The longest reader selection a source anchor will store. A dense page of a
# technical book runs to about four thousand characters, and a reader who
# selects two of them has selected the section, not a passage. Longer
# selections are refused at the boundary rather than silently cut, because a
# cut selection resolves against the book differently from the one the reader
# made.
MAXIMUM_SELECTION_CHARS = 8_000


class QuoteAnchor(ContractModel):
    """One passage a reader carried from a conversation into a side chat.

    Only the selection is recorded: which turn of the parent it came from and
    the text that was highlighted. The citation markers, nodes, and chunks it
    implies are derived from that turn's stored result when the side chat runs
    a turn, so an anchor can never disagree with the answer it points at.

    This is generated answer text, and it is never evidence. What its markers
    *name* is evidence, and that is what pinning resolves.
    """

    # Anchors recorded before source anchors existed carry no `kind` at all.
    # `parse_anchor` supplies this default for them rather than a migration
    # rewriting stored selections: an answer quote is what every stored anchor
    # was, by construction.
    kind: Literal["answer_quote"] = "answer_quote"
    anchor_id: str = Field(min_length=1, max_length=64)
    parent_turn_index: int = Field(ge=0)
    # Wide enough for a whole answer, because anchoring a whole answer is one of
    # the two ways a side chat is opened. The first limit here was 4,000
    # characters, which rejected roughly half of real answers — an interview or
    # deep-dive answer averages over 7,000 — and the interface reported nothing.
    # Length is not what protects the model's context: `build_side_context`
    # truncates a long quote to its token budget and records that it did.
    quoted_text: str = Field(min_length=1, max_length=MAXIMUM_QUOTE_CHARS)


class SourceAnchorBase(ContractModel):
    """What every anchor pointing at a source — rather than at an answer — has.

    The inversion that matters is here rather than in any one subclass. A quote
    anchor points at generated text and can never be cited; a source anchor
    points at the reader's own material, so what it resolves to *is* citable —
    but only after the server has matched it back to canonical content. The
    client's string is never evidence; the chunk it resolves to is.
    """

    anchor_id: str = Field(min_length=1, max_length=64)


class DocumentPageAnchor(SourceAnchorBase):
    """The page the reader is looking at, with no selection made.

    The ambient case, and the common one: a question typed with no gesture at
    all still has to land somewhere, and where the reader is looking is the
    only honest answer.
    """

    kind: Literal["document_page"] = "document_page"
    book_id: int = Field(gt=0)
    page: int = Field(gt=0)


class DocumentPassageAnchor(SourceAnchorBase):
    """Text the reader selected on a page.

    `page` is not redundant with the text: resolution is page-scoped, so a
    sentence that recurs in three chapters resolves to the one in front of the
    reader instead of to whichever the matcher happened to reach first.
    """

    kind: Literal["document_passage"] = "document_passage"
    book_id: int = Field(gt=0)
    page: int = Field(gt=0)
    selected_text: str = Field(min_length=1, max_length=MAXIMUM_SELECTION_CHARS)


class DocumentSectionAnchor(SourceAnchorBase):
    """A named part of the hierarchy — "this section", "this chapter"."""

    kind: Literal["document_section"] = "document_section"
    book_id: int = Field(gt=0)
    node_id: int = Field(gt=0)


class LectureMomentAnchor(SourceAnchorBase):
    """Where the lecture is, as a single instant."""

    kind: Literal["lecture_moment"] = "lecture_moment"
    video_id: UUID
    timestamp_ms: int = Field(ge=0)


class LectureStretchAnchor(SourceAnchorBase):
    """A marked span of lecture.

    A stretch rather than an instant because that is already the unit the
    lecture evaluation set judges against, so the gesture and the measurement
    agree.
    """

    kind: Literal["lecture_stretch"] = "lecture_stretch"
    video_id: UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_span(self) -> "LectureStretchAnchor":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be after start_ms")
        return self


DocumentAnchor = DocumentPageAnchor | DocumentPassageAnchor | DocumentSectionAnchor
LectureAnchor = LectureMomentAnchor | LectureStretchAnchor
SourceAnchor = DocumentAnchor | LectureAnchor
Anchor = Annotated[
    QuoteAnchor | DocumentAnchor | LectureAnchor,
    Field(discriminator="kind"),
]

_ANCHOR_ADAPTER: TypeAdapter[Anchor] = TypeAdapter(Anchor)


def parse_anchor(value: object) -> Anchor:
    """Validate one stored or submitted anchor, defaulting the discriminator.

    Every anchor recorded before this union existed is an answer quote and has
    no `kind` key. Supplying it here keeps those rows loading unchanged, and
    keeps the alternative — a migration that rewrites stored reader selections
    — off the table for a field that can be defaulted correctly.
    """

    if isinstance(value, dict) and "kind" not in value:
        value = {**value, "kind": "answer_quote"}
    return _ANCHOR_ADAPTER.validate_python(value)


def parse_anchors(values: object) -> list[Anchor]:
    return [parse_anchor(value) for value in values or ()]


class SideContextReport(ContractModel):
    """What a side turn was given, and what did not fit its budget.

    Recorded on the turn so the answer inspector and the trace both show the
    inclusion decision rather than leaving it to be inferred from the answer.
    """

    anchor_ids: list[str] = Field(default_factory=list)
    pinned_chunk_ids: list[str] = Field(default_factory=list)
    # Source anchors that could not be matched to canonical content. Their
    # words still reach the model as context, but nothing they say can be
    # cited, and an answer resting on less than the reader selected should say
    # so rather than leave it to be inferred. Empty for answer quotes, which
    # are never resolved against a source in the first place.
    unresolved_anchor_ids: list[str] = Field(default_factory=list)
    token_count: int = Field(ge=0)
    token_budget: int = Field(ge=0)
    dropped: list[str] = Field(default_factory=list)


class ConversationState(ContractModel):
    conversation_id: str
    # The books this conversation may search. Empty means every book the owner
    # has. The last resolved scope's book lives on `active_scope` instead:
    # resolving one turn to one book must not silently narrow the selection
    # the reader made for the whole conversation.
    book_ids: list[int] = Field(default_factory=list)
    messages: list[ConversationMessage] = Field(default_factory=list)
    active_scope: ScopeRef | None = None
    pending_clarification: str | None = None
    previous_answer: str | None = None
    previous_evidence: list[EvidenceRef] = Field(default_factory=list)
    previous_citations: list[CitationRef] = Field(default_factory=list)
    previous_route: Route | None = None

    def recent_messages(self, *, turns: int = 3) -> list[ConversationMessage]:
        return self.messages[-(turns * 2) :]


class TurnDecision(ContractModel):
    route: Route
    history_dependency: HistoryDependency
    standalone_query: str | None = None
    resolved_scope: ScopeRef | None = None
    clarification_question: str | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_route(self) -> "TurnDecision":
        if (
            self.route in {"hierarchy_summary", "hierarchy_list"}
            and self.resolved_scope is None
        ):
            raise ValueError("hierarchy routes require a scope")
        if self.route == "retrieval_qa" and not (self.standalone_query or "").strip():
            raise ValueError("retrieval QA requires a standalone query")
        if self.route == "clarify" and not (self.clarification_question or "").strip():
            raise ValueError("clarify requires a question")
        return self


class WebSourceRef(ContractModel):
    url: str
    title: str
    snippet: str
    domain: str | None = None
    rank: int | None = None


class TurnResult(ContractModel):
    question: str
    answer: str
    route: Route
    history_dependency: HistoryDependency
    standalone_query: str | None = None
    resolved_scope: ScopeRef | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    citations: list[CitationRef] = Field(default_factory=list)
    figures: list[FigureRef] = Field(default_factory=list)
    outline_node_ids: list[int] = Field(default_factory=list)
    outcome: Outcome
    retrieval_mode: str | None = None
    warnings: list[str] = Field(default_factory=list)
    answer_archetype: AnswerArchetype | None = None
    response_depth: ResponseDepth | None = None
    routing_reason: str | None = None
    prompt_profile_version: str | None = None
    # Present only on a side-chat turn. Optional so that turns recorded before
    # side chats existed still load.
    side_context: SideContextReport | None = None
    web_sources: list[WebSourceRef] = Field(default_factory=list)
    source_type: Literal["book_library", "model_knowledge", "web_search"] = "book_library"
