"""Typed boundaries for interview planning, turns, and reports."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SourceKind = Literal["book", "video"]
InterviewMode = Literal["realistic", "guided"]
InterviewFormat = Literal["concept", "system_design", "source_led"]
FormatChoice = Literal["auto", "concept", "system_design", "source_led"]
TargetLevel = Literal["entry", "mid", "senior"]
InterviewStatus = Literal["ready", "active", "paused", "completed", "abandoned"]
QuestionKind = Literal["primary", "follow_up", "clarifying", "hint", "synthesis"]
WorkSampleKind = Literal[
    "none",
    "architecture_diagram",
    "equation_derivation",
    "code",
    "assumptions",
]
AnswerClassification = Literal[
    "source_aligned",
    "correct_extension",
    "partially_correct",
    "incorrect",
    "insufficient",
]
PythonRunStatus = Literal["passed", "failed", "error", "timed_out", "not_run"]

DURATION_OPTIONS = (15, 30, 45, 60, 90, 120)
SCORE_WEIGHTS = {
    "technical_correctness": 0.30,
    "depth_completeness": 0.20,
    "reasoning_structure": 0.15,
    "tradeoff_awareness": 0.15,
    "communication_clarity": 0.10,
    "independence": 0.10,
}


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterviewCitation(ContractModel):
    marker: str = Field(min_length=1)
    node_id: int | None = None
    page: int | None = None
    evidence_rank: int | None = Field(default=None, gt=0)
    start_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def one_locator(self) -> "InterviewCitation":
        book = self.node_id is not None and self.page is not None
        lecture = self.evidence_rank is not None
        if book == lecture:
            raise ValueError("a citation locates either a book page or lecture evidence")
        return self


class WebSource(ContractModel):
    title: str
    url: str
    snippet: str = ""
    rank: int = Field(ge=1)


class ScoreCard(ContractModel):
    technical_correctness: int = Field(ge=1, le=5)
    depth_completeness: int = Field(ge=1, le=5)
    reasoning_structure: int = Field(ge=1, le=5)
    tradeoff_awareness: int = Field(ge=1, le=5)
    communication_clarity: int = Field(ge=1, le=5)
    independence: int = Field(ge=1, le=5)

    @property
    def weighted_score(self) -> float:
        total = sum(
            getattr(self, field) * weight for field, weight in SCORE_WEIGHTS.items()
        )
        return round(total, 2)


class InterviewClarification(ContractModel):
    """One candidate request for clarification before answering a turn."""

    candidate_question: str = Field(min_length=1, max_length=1_000)
    interviewer_response: str = Field(min_length=1, max_length=2_000)


class InterviewClarificationDraft(ContractModel):
    """Private structured output used to author a clarification response."""

    interviewer_response: str = Field(min_length=1, max_length=2_000)


class PythonCodingExercise(ContractModel):
    """Candidate-visible Python scaffold plus private progressive hints."""

    language: Literal["python"] = "python"
    starter_code: str = Field(min_length=1, max_length=20_000)
    visible_tests: str = Field(min_length=1, max_length=12_000)
    hints: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def scaffold_is_fill_in_work(self) -> "PythonCodingExercise":
        if "TODO" not in self.starter_code:
            raise ValueError("starter code must contain at least one TODO marker")
        if "assert" not in self.visible_tests:
            raise ValueError("visible tests must contain at least one assertion")
        if any(not hint.strip() for hint in self.hints):
            raise ValueError("coding hints cannot be blank")
        return self


class PythonExecutionResult(ContractModel):
    status: PythonRunStatus = "not_run"
    stdout: str = Field(default="", max_length=12_000)
    error: str = Field(default="", max_length=12_000)
    duration_ms: int = Field(default=0, ge=0, le=30_000)
    official_tests_passed: bool | None = None
    scratch_tests_passed: bool | None = None


class PythonCodingAnswer(ContractModel):
    """Persisted code artifact submitted alongside the spoken/text answer."""

    language: Literal["python"] = "python"
    code: str = Field(min_length=1, max_length=30_000)
    scratch_tests: str = Field(default="", max_length=12_000)
    execution: PythonExecutionResult = Field(default_factory=PythonExecutionResult)


class InterviewQuestion(ContractModel):
    topic_key: str = Field(min_length=1)
    topic_label: str = Field(min_length=1)
    kind: QuestionKind = "primary"
    text: str = Field(min_length=1, max_length=2_000)
    expected_points: list[str] = Field(default_factory=list, max_length=10)
    suggested_answer: str = Field(default="", max_length=6_000)
    citation_markers: list[str] = Field(default_factory=list, max_length=20)
    difficulty: TargetLevel = "mid"
    interviewer_note: str = Field(default="", max_length=500)
    work_sample: WorkSampleKind = "none"
    work_sample_prompt: str | None = Field(default=None, max_length=1_000)
    coding_exercise: PythonCodingExercise | None = None
    clarifications: list[InterviewClarification] = Field(
        default_factory=list,
        max_length=4,
    )

    @model_validator(mode="after")
    def work_sample_has_an_instruction(self) -> "InterviewQuestion":
        prompt = (self.work_sample_prompt or "").strip()
        if self.work_sample == "none" and prompt:
            raise ValueError("a verbal question cannot include a work-sample prompt")
        if self.work_sample != "none" and not prompt:
            raise ValueError("a work-sample question requires an instruction")
        if self.coding_exercise is not None and self.work_sample != "code":
            raise ValueError("a coding exercise requires the code work-sample kind")
        return self


class ScreenObservation(ContractModel):
    summary: str = Field(min_length=1, max_length=2_000)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    issues: list[str] = Field(default_factory=list, max_length=8)
    follow_up: str | None = Field(default=None, max_length=1_000)
    # Raw images are deliberately absent from this contract.


class AnswerEvaluation(ContractModel):
    classification: AnswerClassification
    scores: ScoreCard
    strengths: list[str] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    concise_feedback: str = Field(default="", max_length=2_000)
    recommended_answer: str = Field(default="", max_length=6_000)
    citation_markers: list[str] = Field(default_factory=list, max_length=20)
    # A candidate is evaluated against the question they heard, not every fact
    # in the source topic. Topic coverage is tracked separately below.
    question_complete: bool = False
    needs_clarifying_probe: bool = False
    clarifying_probe: str | None = Field(default=None, max_length=1_000)
    needs_depth_follow_up: bool = False
    depth_follow_up_focus: str | None = Field(default=None, max_length=1_000)
    needs_external_verification: bool = False
    external_query: str | None = Field(default=None, max_length=500)
    extension_summary: str | None = Field(default=None, max_length=1_000)
    topic_complete: bool = False

    @model_validator(mode="after")
    def required_conditional_fields(self) -> "AnswerEvaluation":
        if self.needs_clarifying_probe and not (self.clarifying_probe or "").strip():
            raise ValueError("a clarifying probe must be supplied when requested")
        if self.needs_depth_follow_up and not (
            self.depth_follow_up_focus or ""
        ).strip():
            raise ValueError("a depth follow-up focus must be supplied when requested")
        if self.needs_depth_follow_up and not self.question_complete:
            raise ValueError("a depth follow-up follows a completed scoped answer")
        if self.needs_depth_follow_up and self.topic_complete:
            raise ValueError("a completed topic cannot request a depth follow-up")
        if self.needs_clarifying_probe and self.needs_depth_follow_up:
            raise ValueError("an evaluation cannot request two follow-up paths")
        if self.needs_external_verification and not (self.external_query or "").strip():
            raise ValueError("an external query must be supplied when verification is requested")
        return self


class TopicState(ContractModel):
    key: str
    label: str
    required: bool = True
    attempts: int = Field(default=0, ge=0)
    hints_used: int = Field(default=0, ge=0, le=2)
    completed: bool = False
    best_score: float = Field(default=0, ge=0, le=5)


class InterviewCheckpoint(ContractModel):
    topics: list[TopicState] = Field(default_factory=list)
    active_topic_key: str | None = None
    strong_streak: int = Field(default=0, ge=0)
    questions_asked: int = Field(default=0, ge=0)
    screen_observation: ScreenObservation | None = None
    closing_reason: str | None = None

    @property
    def required_topics(self) -> list[TopicState]:
        return [topic for topic in self.topics if topic.required]

    @property
    def coverage_ratio(self) -> float:
        required = self.required_topics
        if not required:
            return 1.0
        return sum(topic.completed for topic in required) / len(required)


class InterviewTurn(ContractModel):
    turn_index: int = Field(ge=0)
    question: InterviewQuestion
    answer_text: str | None = None
    coding_answer: PythonCodingAnswer | None = None
    transcript_corrected: bool = False
    evaluation: AnswerEvaluation | None = None
    # Candidate-facing, speakable transition derived from the persisted
    # evaluation. It is populated at the API boundary, so it needs no second
    # source of truth in Postgres.
    interviewer_reaction: str = Field(default="", max_length=2_000)
    citations: list[InterviewCitation] = Field(default_factory=list)
    web_sources: list[WebSource] = Field(default_factory=list)
    screen_observation: ScreenObservation | None = None
    hints_used: int = Field(default=0, ge=0, le=2)
    available_coding_hints: int = Field(default=0, ge=0, le=2)
    cost_usd: float = Field(default=0, ge=0)
    created_at: datetime | None = None
    answered_at: datetime | None = None


class InterviewMetrics(ContractModel):
    questions_answered: int = Field(default=0, ge=0)
    topics_covered: int = Field(default=0, ge=0)
    topics_required: int = Field(default=0, ge=0)
    overall_score: float | None = Field(default=None, ge=1, le=5)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    classification_counts: dict[str, int] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    revision_topics: list[str] = Field(default_factory=list)


class InterviewSession(ContractModel):
    session_id: str
    source_kind: SourceKind
    book_id: int | None = None
    node_id: int | None = None
    video_id: str | None = None
    scope_key: str
    title: str
    source_title: str
    interview_format: InterviewFormat
    format_source: Literal["detected", "override"]
    feedback_mode: InterviewMode
    target_level: TargetLevel
    coding_exercise_requested: bool = False
    maximum_duration_minutes: int
    estimated_min_minutes: int
    estimated_max_minutes: int
    status: InterviewStatus
    elapsed_seconds: int = Field(ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    checkpoint: InterviewCheckpoint
    metrics: InterviewMetrics = Field(default_factory=InterviewMetrics)
    total_cost_usd: float = Field(default=0, ge=0)
    turns: list[InterviewTurn] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InterviewPreflight(ContractModel):
    source_kind: SourceKind
    scope_key: str
    title: str
    source_title: str
    detected_format: InterviewFormat
    selected_format: InterviewFormat
    format_source: Literal["detected", "override"]
    topic_count: int = Field(ge=1)
    required_topic_count: int = Field(ge=1)
    coding_topic_count: int = Field(default=0, ge=0)
    estimated_min_minutes: int = Field(gt=0)
    estimated_max_minutes: int = Field(gt=0, le=120)
    warnings: list[str] = Field(default_factory=list)


class SessionReport(ContractModel):
    session: InterviewSession
    missed_topics: list[str] = Field(default_factory=list)
    evidence_confidence: str
    suggested_next_steps: list[str] = Field(default_factory=list)
