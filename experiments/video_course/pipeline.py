"""Resumable orchestration for the isolated CME295 video-first pilot."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .artifacts import CostLedger, as_records, read_json, stable_hash, write_json
from .evaluation import (
    REQUIRED_CATEGORY_COUNTS,
    evaluate_questions,
    generate_questions,
    question_category_counts,
    question_chapter_coverage,
)
from .frames import extract_candidates
from .models import (
    Chapter,
    EvaluationQuestion,
    EvaluationResult,
    EvidenceUnit,
    FrameCandidate,
    PilotManifest,
    RetrievedEvidence,
    SlidePage,
    TranscriptSegment,
    VisualObservation,
)
from .openrouter import OpenRouterClient
from .report import build_report
from .retrieval import add_embeddings
from .slides import download_slides, extract_slides
from .transcript import parse_vtt
from .visual import analyze_groups, calibrate_models, frame_groups


PILOT_VERSION = "2026-08-03.1"


class VideoCoursePilot:
    """Run and checkpoint the complete single-lecture experiment."""

    def __init__(
        self,
        root: Path,
        *,
        source_url: str,
        slides_url: str,
        ingestion_cap_usd: float = 0.75,
        evaluation_cap_usd: float = 0.75,
    ) -> None:
        self.root = root.resolve()
        self.source_url = source_url
        self.slides_url = slides_url
        self.source_dir = self.root / "source"
        self.artifacts_dir = self.root / "artifacts"
        self.cache_dir = self.root / "cache"
        self.cost_dir = self.root / "costs"
        self.ingestion_cap_usd = ingestion_cap_usd
        self.evaluation_cap_usd = evaluation_cap_usd
        for path in (self.source_dir, self.artifacts_dir, self.cache_dir, self.cost_dir):
            path.mkdir(parents=True, exist_ok=True)

    def run(self) -> PilotManifest:
        metadata_path = self.source_dir / "lecture.info.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"missing YouTube metadata: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        video_path = self._video_path()
        transcript_path = self._transcript_path()
        duration_ms = round(float(metadata["duration"]) * 1000)
        chapters = chapters_from_metadata(metadata, duration_ms)
        manifest = PilotManifest(
            version=PILOT_VERSION,
            source_url=self.source_url,
            slides_url=self.slides_url,
            title=str(metadata.get("title") or "CME295 Lecture 1"),
            video_id=str(metadata.get("id") or ""),
            duration_ms=duration_ms,
            source_video=str(video_path),
            transcript_source=str(transcript_path),
            chapters=chapters,
        )

        transcripts = self._transcripts(transcript_path, chapters)
        slides, slides_path = self._slides()
        manifest.slides_source = str(slides_path)
        frames = self._frames(video_path, chapters)

        ingestion_ledger = CostLedger(
            self.cost_dir / "ingestion.json", maximum_usd=self.ingestion_cap_usd
        )
        ingestion_client = OpenRouterClient(
            self.cache_dir / "openrouter", ingestion_ledger
        )
        try:
            groups = frame_groups(frames)
            calibration = self._calibration(ingestion_client, groups)
            observations = self._observations(
                ingestion_client,
                groups,
                selected_model=str(calibration["selected_model"]),
                fallback_model=_fallback_model(calibration),
            )
            evidence, embedding_costs = self._evidence(
                ingestion_client, transcripts, slides, frames, observations
            )
        finally:
            ingestion_client.close()

        evaluation_ledger = CostLedger(
            self.cost_dir / "evaluation.json", maximum_usd=self.evaluation_cap_usd
        )
        evaluation_client = OpenRouterClient(
            self.cache_dir / "openrouter", evaluation_ledger
        )
        try:
            questions = self._questions(evaluation_client, evidence, chapters)
            results = self._results(evaluation_client, questions, evidence)
        finally:
            evaluation_client.close()

        metrics = coverage_metrics(
            duration_ms=duration_ms,
            chapters=chapters,
            transcripts=transcripts,
            frames=frames,
            observations=observations,
            results=results,
        )
        metrics.update(
            {
                "slide_count": len(slides),
                "evidence_count": len(evidence),
                "image_embedding_count": sum(
                    item.modality == "visual_image" for item in evidence
                ),
                "selected_visual_model": calibration["selected_model"],
                "embedding_costs": {
                    "text": ledger_operation_cost(
                        ingestion_ledger, "evidence-text-embeddings"
                    ),
                    "image": ledger_operation_cost(
                        ingestion_ledger, "visual-image-embedding"
                    ),
                },
                "ingestion_cost_usd": round(ingestion_ledger.spent_usd, 6),
                "evaluation_cost_usd": round(evaluation_ledger.spent_usd, 6),
                "total_cost_usd": round(
                    ingestion_ledger.spent_usd + evaluation_ledger.spent_usd, 6
                ),
            }
        )
        manifest.metrics = metrics
        manifest.stage_hashes = {
            "transcript": stable_hash(as_records(transcripts)),
            "slides": stable_hash(as_records(slides)),
            "frames": stable_hash(as_records(frames)),
            "visual": stable_hash(as_records(observations)),
            "evidence": stable_hash(as_records(evidence)),
            "evaluation": stable_hash(as_records(results)),
        }
        write_json(self.root / "manifest.json", manifest.to_dict())
        build_report(
            self.root / "report.html",
            title=manifest.title,
            source_url=self.source_url,
            source_video=video_path,
            frames=frames,
            observations=observations,
            evidence=evidence,
            results=results,
            metrics=metrics,
            root=self.root,
        )
        return manifest

    def _video_path(self) -> Path:
        preferred = self.source_dir / "lecture.mp4"
        if preferred.exists():
            return preferred
        candidates = [
            path
            for path in self.source_dir.glob("lecture.*")
            if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
        ]
        if not candidates:
            raise FileNotFoundError(
                f"missing source video in {self.source_dir}; expected lecture.mp4"
            )
        return candidates[0]

    def _transcript_path(self) -> Path:
        for name in ("lecture.en-orig.vtt", "lecture.en.vtt", "lecture.en-US.vtt"):
            path = self.source_dir / name
            if path.exists():
                return path
        raise FileNotFoundError(f"missing English WebVTT transcript in {self.source_dir}")

    def _transcripts(
        self, path: Path, chapters: list[Chapter]
    ) -> list[TranscriptSegment]:
        destination = self.artifacts_dir / "transcript.json"
        cached = read_json(destination)
        if cached:
            return [TranscriptSegment(**item) for item in cached]
        values = parse_vtt(path, chapters=chapters)
        write_json(destination, as_records(values))
        return values

    def _slides(self) -> tuple[list[SlidePage], Path]:
        pdf = download_slides(self.slides_url, self.source_dir / "lecture-slides.pdf")
        destination = self.artifacts_dir / "slides.json"
        cached = read_json(destination)
        if cached:
            return [SlidePage(**item) for item in cached], pdf
        values = extract_slides(pdf, self.artifacts_dir / "slides")
        write_json(destination, as_records(values))
        return values, pdf

    def _frames(self, path: Path, chapters: list[Chapter]) -> list[FrameCandidate]:
        destination = self.artifacts_dir / "frames.json"
        cached = read_json(destination)
        if cached:
            return [_frame(item) for item in cached]
        values = extract_candidates(path, self.artifacts_dir / "visual", chapters=chapters)
        write_json(destination, as_records(values))
        return values

    def _calibration(
        self, client: OpenRouterClient, groups: list[list[FrameCandidate]]
    ) -> dict[str, Any]:
        destination = self.artifacts_dir / "visual-calibration.json"
        cached = read_json(destination)
        if cached:
            return dict(cached)
        value = calibrate_models(client, groups)
        write_json(destination, value)
        return value

    def _observations(
        self,
        client: OpenRouterClient,
        groups: list[list[FrameCandidate]],
        *,
        selected_model: str,
        fallback_model: str,
    ) -> list[VisualObservation]:
        destination = self.artifacts_dir / "visual-observations.json"
        cached = read_json(destination)
        if cached:
            return [_observation(item) for item in cached]
        values = analyze_groups(
            client, groups, model=selected_model, fallback_model=fallback_model
        )
        write_json(destination, as_records(values))
        return values

    def _evidence(
        self,
        client: OpenRouterClient,
        transcripts: list[TranscriptSegment],
        slides: list[SlidePage],
        frames: list[FrameCandidate],
        observations: list[VisualObservation],
    ) -> tuple[list[EvidenceUnit], dict[str, float]]:
        destination = self.artifacts_dir / "evidence.json"
        cached = read_json(destination)
        values = build_evidence(transcripts, slides, frames, observations)
        if cached:
            cached_values = [_evidence(item) for item in cached]
            expected_signature = [
                (item.id, item.modality, item.text, item.image_path) for item in values
            ]
            cached_signature = [
                (item.id, item.modality, item.text, item.image_path)
                for item in cached_values
            ]
            if cached_signature == expected_signature:
                return cached_values, {"text": 0.0, "image": 0.0}
        values, costs = add_embeddings(client, values)
        write_json(destination, as_records(values))
        return values, costs

    def _questions(
        self,
        client: OpenRouterClient,
        evidence: list[EvidenceUnit],
        chapters: list[Chapter],
    ) -> list[EvaluationQuestion]:
        destination = self.artifacts_dir / "evaluation-questions.json"
        cached = read_json(destination)
        if cached:
            values = [_question(item) for item in cached]
            if (
                question_category_counts(values) == REQUIRED_CATEGORY_COUNTS
                and question_chapter_coverage(
                    values, {chapter.index for chapter in chapters}
                )
            ):
                return values
        values = generate_questions(client, evidence, chapters)
        write_json(destination, as_records(values))
        return values

    def _results(
        self,
        client: OpenRouterClient,
        questions: list[EvaluationQuestion],
        evidence: list[EvidenceUnit],
    ) -> list[EvaluationResult]:
        destination = self.artifacts_dir / "evaluation-results.json"
        cached = read_json(destination)
        context_path = self.artifacts_dir / "evaluation-results.context.json"
        context_hash = stable_hash(
            {
                "questions": as_records(questions),
                "evidence": as_records(evidence),
            }
        )
        if cached:
            values = [_result(item) for item in cached]
            expected = [(question.id, question.question) for question in questions]
            actual = [
                (result.question.id, result.question.question) for result in values
            ]
            if actual == expected and read_json(context_path, {}).get("hash") == context_hash:
                if all(result.cost_usd > 0 for result in values):
                    return values
        values = evaluate_questions(client, questions, evidence)
        write_json(destination, as_records(values))
        write_json(context_path, {"hash": context_hash})
        return values


def chapters_from_metadata(metadata: dict[str, Any], duration_ms: int) -> list[Chapter]:
    raw = list(metadata.get("chapters") or [])
    if not raw:
        return [Chapter(index=0, title="Complete lecture", start_ms=0, end_ms=duration_ms)]
    return [
        Chapter(
            index=index,
            title=str(item.get("title") or f"Chapter {index + 1}"),
            start_ms=round(float(item.get("start_time") or 0) * 1000),
            end_ms=round(float(item.get("end_time") or duration_ms / 1000) * 1000),
        )
        for index, item in enumerate(raw)
    ]


def build_evidence(
    transcripts: list[TranscriptSegment],
    slides: list[SlidePage],
    frames: list[FrameCandidate],
    observations: list[VisualObservation],
) -> list[EvidenceUnit]:
    by_frame = {frame.id: frame for frame in frames}
    evidence: list[EvidenceUnit] = []
    for segment in transcripts:
        evidence.append(
            EvidenceUnit(
                id=segment.id,
                modality="transcript",
                source_title="YouTube transcript",
                chapter_index=segment.chapter_index,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                page=None,
                text=segment.text,
            )
        )
    for slide in slides:
        evidence.append(
            EvidenceUnit(
                id=f"slide-{slide.page:03d}",
                modality="slide",
                source_title="Official lecture slides",
                chapter_index=None,
                start_ms=None,
                end_ms=None,
                page=slide.page,
                text=slide.text,
                image_path=slide.image_path,
            )
        )
    for observation in observations:
        frame = by_frame.get(observation.evidence_frame_ids[0])
        if frame is None:
            continue
        details = "; ".join(observation.technical_details)
        transition = ""
        if observation.transition_summary:
            transition = (
                f" Transition ({observation.transition_type or 'change'}): "
                f"{observation.transition_summary}"
            )
        text = (
            f"Visual types: {', '.join(observation.content_types)}. "
            f"{observation.summary} Visible text: {observation.visible_text}. "
            f"Technical details: {details}. OCR: {frame.ocr_text}.{transition}"
        )
        spatial_types = {"diagram", "drawing", "chart"}
        use_image_embedding = observation.image_embedding_recommended and bool(
            spatial_types.intersection(observation.content_types)
        )
        evidence.append(
            EvidenceUnit(
                id=f"visual-{frame.id}",
                modality=(
                    "visual_image"
                    if use_image_embedding
                    else "visual"
                ),
                source_title="Lecture video",
                chapter_index=observation.chapter_index,
                start_ms=observation.start_ms,
                end_ms=observation.end_ms,
                page=None,
                text=" ".join(text.split()),
                frame_ids=observation.evidence_frame_ids,
                image_path=frame.preview_path,
            )
        )
    return evidence


def coverage_metrics(
    *,
    duration_ms: int,
    chapters: list[Chapter],
    transcripts: list[TranscriptSegment],
    frames: list[FrameCandidate],
    observations: list[VisualObservation],
    results: list[EvaluationResult],
) -> dict[str, Any]:
    successful = [value for value in observations if not value.error and value.summary]
    analyzed_chapters = {value.chapter_index for value in successful}
    timestamps = sorted(value.start_ms for value in successful)
    gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
    max_gap_ms = max(gaps, default=duration_ms)
    transcript_start = min((value.start_ms for value in transcripts), default=duration_ms)
    transcript_end = max((value.end_ms for value in transcripts), default=0)
    transcript_coverage = max(0.0, transcript_end - transcript_start) / max(1, duration_ms)
    frame_coverage = 0.0
    if frames:
        frame_coverage = min(
            1.0,
            (frames[-1].timestamp_ms + 30_000 - frames[0].timestamp_ms)
            / max(1, duration_ms),
        )
    visual_success = len(successful) / max(1, len(observations))
    chapter_coverage = len(analyzed_chapters) / max(1, len(chapters))
    gates = {
        "full_timeline_frames": frame_coverage >= 0.995,
        "transcript_coverage": transcript_coverage >= 0.95,
        "visual_analysis_success": visual_success >= 0.90,
        "every_chapter_visual": all(chapter.index in analyzed_chapters for chapter in chapters),
        "no_analysis_gap_over_five_minutes": max_gap_ms <= 300_000,
    }
    valid_results = [result for result in results if not result.error]
    return {
        "ready": all(gates.values()),
        "quality_gates": gates,
        "duration_ms": duration_ms,
        "frame_count": len(frames),
        "frame_timeline_coverage": round(frame_coverage, 4),
        "transcript_segment_count": len(transcripts),
        "transcript_coverage": round(transcript_coverage, 4),
        "visual_observation_count": len(observations),
        "visual_success_rate": round(visual_success, 4),
        "chapter_visual_coverage": round(chapter_coverage, 4),
        "max_visual_gap_ms": max_gap_ms,
        "question_count": len(results),
        "valid_question_count": len(valid_results),
        "mean_semantic_agreement": round(
            _mean(result.semantic_agreement for result in valid_results), 4
        ),
        "mean_citation_correctness": round(
            _mean(result.citation_correctness for result in valid_results), 4
        ),
    }


def _fallback_model(calibration: dict[str, Any]) -> str:
    selected = calibration["selected_model"]
    for model in calibration.get("candidate_models") or []:
        summary = (calibration.get("summaries") or {}).get(model) or {}
        if model != selected and not summary.get("failures"):
            return str(model)
    return "openai/gpt-5.6-luna"


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def ledger_operation_cost(ledger: CostLedger, operation: str) -> float:
    return round(
        sum(
            float(entry.get("cost_usd") or 0.0)
            for entry in ledger.entries
            if entry.get("operation") == operation
        ),
        8,
    )


def _frame(item: dict[str, Any]) -> FrameCandidate:
    item = dict(item)
    item["selection_reasons"] = tuple(item.get("selection_reasons") or [])
    return FrameCandidate(**item)


def _observation(item: dict[str, Any]) -> VisualObservation:
    item = dict(item)
    for key in ("frame_ids", "content_types", "technical_details", "evidence_frame_ids"):
        item[key] = tuple(item.get(key) or [])
    return VisualObservation(**item)


def _evidence(item: dict[str, Any]) -> EvidenceUnit:
    item = dict(item)
    item["frame_ids"] = tuple(item.get("frame_ids") or [])
    item["embedding"] = tuple(item.get("embedding") or [])
    return EvidenceUnit(**item)


def _question(item: dict[str, Any]) -> EvaluationQuestion:
    item = dict(item)
    for key in (
        "expected_modalities",
        "expected_chapter_indexes",
        "generated_from_evidence_ids",
    ):
        item[key] = tuple(item.get(key) or [])
    return EvaluationQuestion(**item)


def _result(item: dict[str, Any]) -> EvaluationResult:
    item = dict(item)
    item["question"] = _question(item["question"])
    item["reference_evidence_ids"] = tuple(item.get("reference_evidence_ids") or [])
    item["unsupported_claims"] = tuple(item.get("unsupported_claims") or [])
    item["missing_points"] = tuple(item.get("missing_points") or [])
    item["retrieved"] = tuple(
        RetrievedEvidence(
            evidence_id=value["evidence_id"],
            rank=int(value["rank"]),
            score=float(value["score"]),
            retrieval_methods=tuple(value.get("retrieval_methods") or []),
        )
        for value in item.get("retrieved") or []
    )
    return EvaluationResult(**item)
