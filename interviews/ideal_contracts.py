"""Contracts for a complete, listen-only model interview over one chapter."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .contracts import ContractModel, InterviewCitation, InterviewFormat, TargetLevel


IdealPhase = Literal[
    "opening",
    "requirements",
    "estimation",
    "architecture",
    "deep_dive",
    "tradeoffs",
    "reliability",
    "evaluation",
    "closing",
]


class IdealInterviewExchangeDraft(ContractModel):
    """Private model output. Topic identity is assigned by deterministic code."""

    interviewer_text: str = Field(min_length=1, max_length=2_000)
    candidate_text: str = Field(min_length=1, max_length=8_000)
    citation_markers: list[str] = Field(min_length=1, max_length=100)


class IdealInterviewExchange(ContractModel):
    exchange_index: int = Field(ge=0)
    phase: IdealPhase
    topic_key: str = Field(min_length=1)
    topic_label: str = Field(min_length=1)
    interviewer_text: str = Field(min_length=1, max_length=2_000)
    candidate_text: str = Field(min_length=1, max_length=8_000)
    citations: list[InterviewCitation] = Field(min_length=1, max_length=100)
    pause_after_question_ms: int = Field(default=650, ge=250, le=2_000)
    pause_after_answer_ms: int = Field(default=900, ge=250, le=3_000)


class IdealInterviewFlow(ContractModel):
    flow_id: str
    book_id: int
    node_id: int
    scope_key: str
    title: str
    source_title: str
    interview_format: InterviewFormat
    target_level: TargetLevel
    status: Literal["ready", "complete", "failed"] = "complete"
    topic_count: int = Field(ge=1)
    covered_topic_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    estimated_duration_seconds: int = Field(ge=1)
    exchanges: list[IdealInterviewExchange] = Field(min_length=1)
    generation_model: str
    prompt_version: str
    total_cost_usd: float = Field(default=0, ge=0)
    voice_cost_usd: float = Field(default=0, ge=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def complete_means_full_coverage(self) -> "IdealInterviewFlow":
        if self.status == "complete":
            keys = [exchange.topic_key for exchange in self.exchanges]
            if len(keys) != len(set(keys)):
                raise ValueError("a complete ideal interview covers each topic once")
            if self.covered_topic_count != self.topic_count or self.coverage_ratio != 1:
                raise ValueError("a complete ideal interview must cover the full inventory")
        return self


class IdealInterviewList(ContractModel):
    flows: list[IdealInterviewFlow]
