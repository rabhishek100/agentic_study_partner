"""Typed boundaries for grounded questions spanning course lectures."""

from pydantic import Field

from video.contracts import (
    ContractModel,
    HistoryDependency,
    VideoEvidenceRef,
    VideoMessage,
    VideoModality,
    VideoOutcome,
    VideoRoute,
)


class CourseEvidenceRef(VideoEvidenceRef):
    video_id: str
    video_title: str
    lecture_index: int = Field(ge=0)
    ingestion_version_id: str


class CourseCitationRef(ContractModel):
    marker: str
    evidence_rank: int = Field(gt=0)
    video_id: str
    video_title: str
    lecture_index: int = Field(ge=0)
    modality: VideoModality
    start_ms: int | None = None
    page_number: int | None = None
    frame_id: int | None = None
    resource_id: str | None = None


class CourseTurnResult(ContractModel):
    question: str
    answer: str
    route: VideoRoute = "evidence_qa"
    history_dependency: HistoryDependency = "independent"
    standalone_query: str | None = None
    evidence: list[CourseEvidenceRef] = Field(default_factory=list)
    citations: list[CourseCitationRef] = Field(default_factory=list)
    outcome: VideoOutcome
    retrieval_attempts: int = 1
    sufficiency_reason: str | None = None
    routing_reason: str | None = None
    cost_usd: float = 0.0
    trace_id: str | None = None
    excluded_video_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CourseConversationState(ContractModel):
    conversation_id: str
    course_id: str
    messages: list[VideoMessage] = Field(default_factory=list)
    previous_answer: str | None = None
    previous_evidence: list[CourseEvidenceRef] = Field(default_factory=list)
    previous_citations: list[CourseCitationRef] = Field(default_factory=list)
    previous_route: VideoRoute | None = None
    pending_clarification: str | None = None

    def recent_messages(self, *, turns: int = 3) -> list[VideoMessage]:
        return self.messages[-(turns * 2) :]
