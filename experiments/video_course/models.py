"""Serializable contracts for the isolated video-course experiment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TranscriptSegment:
    id: str
    start_ms: int
    end_ms: int
    text: str
    chapter_index: int | None = None


@dataclass(frozen=True)
class FrameCandidate:
    id: str
    timestamp_ms: int
    path: str
    preview_path: str
    width: int
    height: int
    content_hash: str
    difference_score: float
    selection_reasons: tuple[str, ...]
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    chapter_index: int | None = None


@dataclass(frozen=True)
class VisualObservation:
    id: str
    frame_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    content_types: tuple[str, ...]
    importance: float
    summary: str
    visible_text: str
    technical_details: tuple[str, ...]
    transition_type: str | None
    transition_summary: str | None
    image_embedding_recommended: bool
    evidence_frame_ids: tuple[str, ...]
    model_id: str
    prompt_hash: str
    cost_usd: float
    latency_seconds: float
    chapter_index: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class SlidePage:
    page: int
    text: str
    image_path: str


@dataclass(frozen=True)
class EvidenceUnit:
    id: str
    modality: str
    source_title: str
    chapter_index: int | None
    start_ms: int | None
    end_ms: int | None
    page: int | None
    text: str
    frame_ids: tuple[str, ...] = ()
    image_path: str | None = None
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class RetrievedEvidence:
    evidence_id: str
    rank: int
    score: float
    retrieval_methods: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationQuestion:
    id: str
    category: str
    question: str
    expected_modalities: tuple[str, ...]
    expected_chapter_indexes: tuple[int, ...]
    generated_from_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationResult:
    question: EvaluationQuestion
    reference_answer: str
    reference_evidence_ids: tuple[str, ...]
    system_answer: str
    retrieved: tuple[RetrievedEvidence, ...]
    semantic_agreement: float
    citation_correctness: float
    unsupported_claims: tuple[str, ...]
    missing_points: tuple[str, ...]
    judge_summary: str
    cost_usd: float
    latency_seconds: float
    error: str | None = None


@dataclass
class PilotManifest:
    version: str
    source_url: str
    slides_url: str
    title: str = ""
    video_id: str = ""
    duration_ms: int = 0
    source_video: str = ""
    transcript_source: str = ""
    slides_source: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    stage_hashes: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
