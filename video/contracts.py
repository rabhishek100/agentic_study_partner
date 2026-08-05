"""Typed boundaries for one grounded video conversation turn.

These mirror the book conversation contracts deliberately: the same
server-authoritative state, the same follow-up rewriting, the same abstain
behavior. They are separate types because the locators are different. A book
cites a node and a page; a video cites a timestamp, a frame, a visual change,
or a page of a linked document, and those never collapse into one field.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


VideoRoute = Literal[
    "evidence_qa",
    "lecture_summary",
    "topic_inventory",
    "prior_answer_transform",
    "clarify",
]
VideoOutcome = Literal["answer", "clarify", "abstain", "error"]
HistoryDependency = Literal["independent", "dependent", "ambiguous"]
VideoModality = Literal[
    "transcript", "visual_frame", "visual_event", "resource_page"
]
RetrievalMethod = Literal[
    "fts",
    "text_vector",
    "image_vector",
    "hybrid",
    "timeline_expansion",
    # Not a search at all: the complete published transcript, in order. A
    # whole-lecture request is answered from all of it, and the inspector must
    # not imply that eight passages were ranked and chosen.
    "complete_transcript",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoMessage(ContractModel):
    role: Literal["user", "assistant"]
    content: str
    turn_id: str | None = None


class VideoEvidenceRef(ContractModel):
    """One retrieved item, keeping the identity it was retrieved with."""

    rank: int = Field(gt=0)
    evidence_id: str
    modality: VideoModality
    excerpt: str
    retrieval_method: RetrievalMethod
    score: float
    start_ms: int | None = None
    end_ms: int | None = None
    page_number: int | None = None
    frame_id: int | None = None
    visual_event_id: int | None = None
    transcript_segment_id: int | None = None
    resource_page_id: int | None = None
    resource_id: str | None = None
    resource_title: str | None = None

    @property
    def is_visual(self) -> bool:
        return self.modality in {"visual_frame", "visual_event"}


class VideoCitationRef(ContractModel):
    """Where the interface should send a reader who clicks a marker.

    A timestamp citation seeks the player; a page citation opens the document.
    One citation never carries both, because a slide page and a moment in the
    lecture are not known to be the same place.
    """

    marker: str
    evidence_rank: int = Field(gt=0)
    modality: VideoModality
    start_ms: int | None = None
    page_number: int | None = None
    frame_id: int | None = None
    resource_id: str | None = None


class VisualCard(ContractModel):
    """A frame or before/after pair shown beside the answer."""

    evidence_rank: int = Field(gt=0)
    frame_id: int | None = None
    visual_event_id: int | None = None
    start_ms: int
    end_ms: int | None = None
    summary: str
    kind: Literal["frame", "transition"]


class VideoTurnDecision(ContractModel):
    route: VideoRoute
    history_dependency: HistoryDependency
    standalone_query: str | None = None
    clarification_question: str | None = None
    reason: str = Field(min_length=1)


class VideoTurnResult(ContractModel):
    question: str
    answer: str
    route: VideoRoute
    history_dependency: HistoryDependency
    standalone_query: str | None = None
    evidence: list[VideoEvidenceRef] = Field(default_factory=list)
    citations: list[VideoCitationRef] = Field(default_factory=list)
    visual_cards: list[VisualCard] = Field(default_factory=list)
    outcome: VideoOutcome
    ingestion_version_id: str | None = None
    retrieval_attempts: int = 1
    sufficiency_reason: str | None = None
    routing_reason: str | None = None
    cost_usd: float = 0.0
    trace_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class VideoConversationState(ContractModel):
    """Everything a later turn may use to resolve a reference.

    Prior answers help rewrite "that diagram" into a standalone query. They
    never become evidence: only retrieved units can support a claim.
    """

    conversation_id: str
    video_id: str
    messages: list[VideoMessage] = Field(default_factory=list)
    previous_answer: str | None = None
    previous_evidence: list[VideoEvidenceRef] = Field(default_factory=list)
    previous_citations: list[VideoCitationRef] = Field(default_factory=list)
    previous_route: VideoRoute | None = None
    pending_clarification: str | None = None

    def recent_messages(self, *, turns: int = 3) -> list[VideoMessage]:
        return self.messages[-(turns * 2) :]
