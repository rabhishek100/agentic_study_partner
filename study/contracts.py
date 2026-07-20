"""Typed boundaries shared by evaluation, orchestration, and interfaces."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Route = Literal[
    "hierarchy_summary",
    "hierarchy_list",
    "retrieval_qa",
    "prior_answer_transform",
    "clarify",
    "abstain",
    "error",
]
HistoryDependency = Literal["independent", "dependent", "ambiguous"]
ScopeBehavior = Literal[
    "hard_filter",
    "prefer_scope",
    "global",
    "reuse_prior_answer",
    "clarify",
]
Outcome = Literal["answer", "clarify", "abstain", "error", "blocked"]
SufficiencyStatus = Literal[
    "not_checked",
    "sufficient",
    "insufficient",
]
ActiveScopeUpdate = Literal["set_active_scope", "retain", "clear", "none"]
ClarificationUpdate = Literal["set", "clear", "none"]
DecisionSource = Literal["deterministic", "llm"]


class ContractModel(BaseModel):
    """Reject accidental schema drift at component boundaries."""

    model_config = ConfigDict(extra="forbid")


class ConversationMessage(ContractModel):
    role: Literal["user", "assistant"]
    content: str
    turn_id: str | None = None


class ScopeRef(ContractModel):
    kind: Literal["book", "chapter", "section"]
    book_id: int
    node_id: int | None = None
    display_path: str
    start_page: int
    end_page: int


class ScopeCandidate(ContractModel):
    """One canonical hierarchy option supplied to turn analysis."""

    book_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    kind: Literal["chapter", "section"]
    title: str = Field(min_length=1)
    display_path: str = Field(min_length=1)
    start_page: int = Field(gt=0)
    end_page: int = Field(gt=0)
    match_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_page_range(self) -> "ScopeCandidate":
        if self.end_page < self.start_page:
            raise ValueError("end_page must be greater than or equal to start_page")
        return self


class EvidenceRef(ContractModel):
    node_id: int
    pages: list[int]
    path: str
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
    evidence_rank: int | None = None


class StateUpdate(ContractModel):
    active_scope: ActiveScopeUpdate = "none"
    pending_clarification: ClarificationUpdate = "none"


class SufficiencyDecision(ContractModel):
    status: SufficiencyStatus = "not_checked"
    reason: str | None = None


class ConversationState(ContractModel):
    """Explicit state carried between turns; version one remains in memory."""

    conversation_id: str
    book_id: int | None = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    active_scope: ScopeRef | None = None
    pending_clarification: str | None = None
    previous_answer: str | None = None
    previous_evidence: list[EvidenceRef] = Field(default_factory=list)
    previous_citations: list[CitationRef] = Field(default_factory=list)
    previous_route: Route | None = None

    def recent_messages(self, *, turns: int = 3) -> list[ConversationMessage]:
        """Return at most the last N user/assistant pairs."""

        return self.messages[-(turns * 2) :]


class TurnAnalysis(ContractModel):
    """Validated routing decision produced before evidence selection."""

    route: Route
    history_dependency: HistoryDependency
    standalone_query: str | None = None
    scope_behavior: ScopeBehavior
    resolved_scope: ScopeRef | None = None
    state_update: StateUpdate
    clarification_question: str | None = None
    decision_reason: str = Field(min_length=1)
    decision_source: DecisionSource

    @model_validator(mode="after")
    def validate_route_requirements(self) -> "TurnAnalysis":
        if (
            self.route in {"hierarchy_summary", "hierarchy_list"}
            and self.resolved_scope is None
        ):
            raise ValueError("hierarchy routes require a resolved scope")
        if (
            self.route == "retrieval_qa"
            and not (self.standalone_query or "").strip()
        ):
            raise ValueError("retrieval_qa requires a standalone query")
        if self.route == "clarify":
            if not (self.clarification_question or "").strip():
                raise ValueError("clarify requires a clarification question")
            if self.scope_behavior != "clarify":
                raise ValueError("clarify requires clarify scope behavior")
        if (
            self.scope_behavior == "hard_filter"
            and self.resolved_scope is None
        ):
            raise ValueError("hard_filter requires a resolved scope")
        return self


class TurnResult(ContractModel):
    """Structured result rendered differently by each reader interface."""

    question: str
    answer: str
    route: Route
    history_dependency: HistoryDependency
    standalone_query: str | None
    scope_behavior: ScopeBehavior
    resolved_scope: ScopeRef | None = None
    state_update: StateUpdate = Field(default_factory=StateUpdate)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    citations: list[CitationRef] = Field(default_factory=list)
    outline_node_ids: list[int] = Field(default_factory=list)
    sufficiency: SufficiencyDecision = Field(
        default_factory=SufficiencyDecision
    )
    outcome: Outcome
    retrieval_mode: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    trace_urls: list[str] = Field(default_factory=list)
