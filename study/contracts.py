"""Small typed boundaries for conversation, evidence, and results."""

from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Route = Literal[
    "hierarchy_summary",
    "hierarchy_list",
    "retrieval_qa",
    "prior_answer_transform",
    "clarify",
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
