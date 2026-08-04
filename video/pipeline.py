"""Direct, inspectable video stage dispatcher for the local MVP worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable

import cv2
from psycopg import Connection
from psycopg.types.json import Jsonb

from video.acquisition import Chapter, MediaMetadata, probe_media
from video.acquisition_stage import (
    AcquisitionDependencies,
    YouTubeAcquirer,
    run_acquire_source,
)
from video.audio import (
    DEFAULT_TRANSCRIPTION_MODEL,
    AudioBudgetExceeded,
    AudioTranscription,
    OpenRouterAudioClient,
)
from video.jobs import (
    VideoIngestionJob,
    advance_stage,
    begin_stage_checkpoint,
    complete_stage_checkpoint,
    publish_job,
    release_claim,
)
from video.errors import VideoBudgetExceeded
from video.evidence_store import (
    evaluate_quality_gates,
    persist_quality_gates,
    rebuild_evidence,
)
from video.frames import OcrResult, select_frames, run_tesseract
from video.media_store import FilesystemMediaStore
from video.source_store import (
    load_source_stage_target,
    publish_media_metadata,
)
from video.states import Stage
from video.transcript_store import persist_transcript
from video.transcripts import parse_webvtt, transcript_coverage
from video.vision import (
    PROMPT_VERSION,
    FrameVisualAnalysis,
    OpenRouterVisualClient,
    VisualAnalysis,
    VisualFrame,
)
from video.visual_store import (
    OCRResultInput,
    SelectedFrameInput,
    VisualObservationInput,
    load_stage_frames,
    persist_ocr_results,
    persist_selected_frames,
    persist_visual_observation,
)


METADATA_STAGE_VERSION = "video-media-metadata-v1"
TRANSCRIPT_STAGE_VERSION = "video-transcript-prefer-caption-v2"
RESOURCES_STAGE_VERSION = "video-resources-deferred-v1"
FRAME_STAGE_VERSION = "video-frame-selection-v1"
OCR_STAGE_VERSION = "video-frame-ocr-v1"
VISUAL_STAGE_VERSION = "video-visual-analysis-v1"
SPATIAL_STAGE_VERSION = "video-spatial-regions-v1"
INDEX_STAGE_VERSION = "video-evidence-index-v1"
QUALITY_STAGE_VERSION = "video-quality-gates-v1"
PUBLISH_STAGE_VERSION = "video-publish-v1"
MediaProbe = Callable[[Path], MediaMetadata]
FrameSelector = Callable[..., tuple[Any, ...]]
FrameOcr = Callable[[Path], OcrResult]
VisualAnalyzer = Callable[[tuple[VisualFrame, ...]], VisualAnalysis]
AudioTranscriber = Callable[..., AudioTranscription]
MINIMUM_CAPTION_COVERAGE = 0.90
MAXIMUM_TRANSCRIPT_ARTIFACT_BYTES = 50 * 1024 * 1024


def _configured_model(name: str, fallback: str) -> str:
    return os.getenv(name, "").strip() or fallback


class MissingVideoTranscript(RuntimeError):
    """No usable caption exists; the audio fallback should handle the source."""


@dataclass(frozen=True)
class VideoPipelineDependencies:
    media_store: FilesystemMediaStore
    youtube_acquirer: YouTubeAcquirer
    media_probe: MediaProbe = probe_media
    maximum_bytes: int = 2 * 1024 * 1024 * 1024
    youtube_acquisition_version: str = "yt-dlp"
    frame_selector: FrameSelector = select_frames
    frame_ocr: FrameOcr = run_tesseract
    visual_analyzer: VisualAnalyzer | None = None
    visual_model: str = field(
        default_factory=lambda: _configured_model(
            "OPENROUTER_VIDEO_VISION_MODEL", "openai/gpt-5.6-luna"
        )
    )
    audio_transcriber: AudioTranscriber | None = None
    audio_model: str = field(
        default_factory=lambda: _configured_model(
            "OPENROUTER_AUDIO_MODEL", DEFAULT_TRANSCRIPTION_MODEL
        )
    )


def run_video_stage(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    work_dir: Path,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    """Execute exactly one claimed stage, preserving observable boundaries."""

    match job.stage:
        case Stage.ACQUIRE_SOURCE:
            return run_acquire_source(
                connection,
                job=job,
                worker_id=worker_id,
                work_dir=work_dir,
                dependencies=AcquisitionDependencies(
                    media_store=dependencies.media_store,
                    youtube_acquirer=dependencies.youtube_acquirer,
                    media_probe=dependencies.media_probe,
                    maximum_bytes=dependencies.maximum_bytes,
                    youtube_acquisition_version=(
                        dependencies.youtube_acquisition_version
                    ),
                ),
            )
        case Stage.MEDIA_METADATA:
            return _run_media_metadata(
                connection,
                job=job,
                worker_id=worker_id,
                dependencies=dependencies,
            )
        case Stage.TRANSCRIPT:
            return _run_transcript(
                connection,
                job=job,
                worker_id=worker_id,
                work_dir=work_dir,
                dependencies=dependencies,
            )
        case Stage.RESOURCES:
            return _run_deferred_stage(
                connection,
                job=job,
                worker_id=worker_id,
                stage=Stage.RESOURCES,
                next_stage=Stage.FRAME_SELECTION,
                stage_version=RESOURCES_STAGE_VERSION,
            )
        case Stage.FRAME_SELECTION:
            return _run_frame_selection(
                connection,
                job=job,
                worker_id=worker_id,
                work_dir=work_dir,
                dependencies=dependencies,
            )
        case Stage.OCR:
            return _run_ocr(
                connection,
                job=job,
                worker_id=worker_id,
                dependencies=dependencies,
            )
        case Stage.VISUAL_ANALYSIS:
            return _run_visual_analysis(
                connection,
                job=job,
                worker_id=worker_id,
                dependencies=dependencies,
            )
        case Stage.SPATIAL_REGIONS:
            return _run_spatial_regions(
                connection,
                job=job,
                worker_id=worker_id,
                work_dir=work_dir,
                dependencies=dependencies,
            )
        case Stage.INDEXING:
            return _run_indexing(
                connection,
                job=job,
                worker_id=worker_id,
            )
        case Stage.QUALITY_GATES:
            return _run_quality_gates(
                connection,
                job=job,
                worker_id=worker_id,
            )
        case Stage.PUBLISH:
            return _run_publish(
                connection,
                job=job,
                worker_id=worker_id,
            )
        case _:
            raise ValueError(f"video stage is not implemented: {job.stage}")


def _run_media_metadata(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    target = load_source_stage_target(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.MEDIA_METADATA,
    )
    acquisition = _completed_manifest(
        connection, job=job, stage=Stage.ACQUIRE_SOURCE
    )
    dependency_hash = _stable_hash(
        {
            "stage_version": METADATA_STAGE_VERSION,
            "source_content_hash": target.source_content_hash,
            "acquisition": acquisition,
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.MEDIA_METADATA,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        if (
            target.source_storage_key is None
            or target.source_content_hash is None
            or target.source_size_bytes is None
        ):
            raise RuntimeError("canonical video media is unavailable")
        dependencies.media_store.verify_object(
            owner_id=job.owner_id,
            storage_key=target.source_storage_key,
            expected_size=target.source_size_bytes,
            expected_hash=target.source_content_hash,
        )
        source_path = dependencies.media_store.open_path(
            owner_id=job.owner_id,
            storage_key=target.source_storage_key,
        )
        media = dependencies.media_probe(source_path)
        chapters = tuple(
            Chapter(**value) for value in acquisition.get("chapters") or []
        )
        with connection.transaction():
            published = publish_media_metadata(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                media=media,
                chapters=chapters,
                discovered_title=acquisition.get("title"),
                discovered_description=acquisition.get("description"),
            )
            complete_stage_checkpoint(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                stage=Stage.MEDIA_METADATA,
                dependency_hash=dependency_hash,
                output_manifest={
                    "stage_version": METADATA_STAGE_VERSION,
                    "source_id": str(published.source_id),
                    "media": _media_manifest(media),
                    "chapter_count": len(chapters),
                },
            )
    with connection.transaction():
        advance_stage(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            next_stage=Stage.TRANSCRIPT,
        )
        return release_claim(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def _run_transcript(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    work_dir: Path,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    target = load_source_stage_target(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.TRANSCRIPT,
    )
    acquisition = _completed_manifest(
        connection, job=job, stage=Stage.ACQUIRE_SOURCE
    )
    metadata = _completed_manifest(
        connection, job=job, stage=Stage.MEDIA_METADATA
    )
    captions = list(acquisition.get("captions") or [])
    dependency_hash = _stable_hash(
        {
            "stage_version": TRANSCRIPT_STAGE_VERSION,
            "source_content_hash": target.source_content_hash,
            "captions": captions,
            "duration_ms": metadata.get("media", {}).get("duration_ms"),
            "fallback_model": dependencies.audio_model,
            "minimum_caption_coverage": MINIMUM_CAPTION_COVERAGE,
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.TRANSCRIPT,
        dependency_hash=dependency_hash,
    )
    work_dir = Path(work_dir)
    try:
        if not checkpoint.reused:
            duration_ms = int(metadata.get("media", {}).get("duration_ms") or 0)
            candidates: list[
                tuple[float, int, str, dict[str, Any], list[Any]]
            ] = []
            for caption in captions:
                if caption.get("storage_backend") != dependencies.media_store.backend:
                    continue
                dependencies.media_store.verify_object(
                    owner_id=job.owner_id,
                    storage_key=caption["storage_key"],
                    expected_size=int(caption["size_bytes"]),
                    expected_hash=caption["content_hash"],
                )
                path = dependencies.media_store.open_path(
                    owner_id=job.owner_id,
                    storage_key=caption["storage_key"],
                )
                try:
                    cues = parse_webvtt(path.read_bytes())
                except ValueError:
                    continue
                coverage = transcript_coverage(cues, duration_ms=duration_ms)
                candidates.append(
                    (coverage, len(cues), caption["content_hash"], caption, cues)
                )

            best = max(candidates, key=lambda value: value[:3]) if candidates else None
            if best is not None and best[0] >= MINIMUM_CAPTION_COVERAGE:
                coverage, _, _, selected, cues = best
                source_kind = "youtube_caption"
                language = "en"
                provider = "youtube"
                model_name = model_revision = None
                storage_backend = selected["storage_backend"]
                storage_key = selected["storage_key"]
                content_hash = selected["content_hash"]
                cost = Decimal("0")
                provenance = {
                    "stage_version": TRANSCRIPT_STAGE_VERSION,
                    "candidate_count": len(captions),
                    "selected_coverage_ratio": coverage,
                }
            else:
                if target.source_storage_key is None:
                    raise MissingVideoTranscript(
                        "canonical media is unavailable for audio transcription"
                    )
                remaining = _remaining_job_budget(connection, job=job)
                transcriber = dependencies.audio_transcriber
                owned_client: OpenRouterAudioClient | None = None
                if transcriber is None:
                    owned_client = OpenRouterAudioClient(
                        model=dependencies.audio_model
                    )
                    transcriber = owned_client.transcribe
                try:
                    result = transcriber(
                        dependencies.media_store.open_path(
                            owner_id=job.owner_id,
                            storage_key=target.source_storage_key,
                        ),
                        duration_ms=duration_ms,
                        work_dir=work_dir / "audio",
                        remaining_budget_usd=remaining,
                        language="en",
                    )
                except AudioBudgetExceeded as error:
                    raise VideoBudgetExceeded() from error
                finally:
                    if owned_client is not None:
                        owned_client.close()
                cues = list(result.cues)
                source_kind = "openrouter_transcription"
                language = result.language or "en"
                provider = "openrouter"
                model_name = result.provenance.requested_model
                revisions = tuple(
                    dict.fromkeys(chunk.model for chunk in result.provenance.chunks)
                )
                model_revision = ",".join(revisions) or model_name
                cost = Decimal(str(result.provenance.total_cost_usd))
                provenance = {
                    "stage_version": TRANSCRIPT_STAGE_VERSION,
                    "candidate_count": len(captions),
                    "caption_fallback_reason": (
                        "insufficient_coverage" if best is not None else "unavailable"
                    ),
                    "processed_duration_ms": duration_ms,
                    **result.provenance.as_json(),
                }
                artifact_path = _write_transcript_artifact(
                    work_dir / "transcript.json",
                    duration_ms=duration_ms,
                    language=language,
                    cues=cues,
                    provenance=provenance,
                )
                stored = dependencies.media_store.import_file(
                    owner_id=job.owner_id,
                    source=artifact_path,
                    namespace="transcripts",
                    extension=".json",
                    maximum_bytes=MAXIMUM_TRANSCRIPT_ARTIFACT_BYTES,
                )
                storage_backend = dependencies.media_store.backend
                storage_key = stored.storage_key
                content_hash = stored.content_hash

            with connection.transaction():
                persisted = persist_transcript(
                    connection,
                    owner_id=job.owner_id,
                    video_id=job.video_id,
                    video_source_id=target.source_id,
                    source_kind=source_kind,
                    language=language,
                    provider=provider,
                    storage_backend=storage_backend,
                    storage_key=storage_key,
                    content_hash=content_hash,
                    cues=cues,
                    model_name=model_name,
                    model_revision=model_revision,
                    provenance=provenance,
                    cost_usd=cost,
                )
                complete_stage_checkpoint(
                    connection,
                    job_id=job.id,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                    stage=Stage.TRANSCRIPT,
                    dependency_hash=dependency_hash,
                    output_manifest={
                        "stage_version": TRANSCRIPT_STAGE_VERSION,
                        "transcript_source_id": str(persisted.id),
                        "source_kind": persisted.source_kind,
                        "coverage_ratio": persisted.coverage_ratio,
                        "segment_count": persisted.segment_count,
                        "content_hash": content_hash,
                        "model": model_name,
                    },
                    cost_usd=cost,
                )
        with connection.transaction():
            advance_stage(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                next_stage=Stage.RESOURCES,
            )
            return release_claim(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
            )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_deferred_stage(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    stage: Stage,
    next_stage: Stage,
    stage_version: str,
) -> VideoIngestionJob:
    dependency_hash = _stable_hash(
        {
            "stage_version": stage_version,
            "ingestion_version_id": str(job.target_version_id),
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=stage,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        complete_stage_checkpoint(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            stage=stage,
            dependency_hash=dependency_hash,
            output_manifest={"stage_version": stage_version, "deferred": True},
        )
    with connection.transaction():
        advance_stage(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            next_stage=next_stage,
        )
        return release_claim(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def _run_frame_selection(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    work_dir: Path,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    target = load_source_stage_target(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.FRAME_SELECTION,
    )
    chapters = tuple(
        Chapter(
            index=int(row["chapter_index"]),
            title=row["title"],
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]),
        )
        for row in connection.execute(
            """
            select chapter_index, title, start_ms, end_ms
            from video.chapters where owner_id = %s and video_id = %s
            order by chapter_index
            """,
            (job.owner_id, job.video_id),
        ).fetchall()
    )
    dependency_hash = _stable_hash(
        {
            "stage_version": FRAME_STAGE_VERSION,
            "source_content_hash": target.source_content_hash,
            "chapters": [
                {
                    "index": value.index,
                    "title": value.title,
                    "start_ms": value.start_ms,
                    "end_ms": value.end_ms,
                }
                for value in chapters
            ],
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.FRAME_SELECTION,
        dependency_hash=dependency_hash,
    )
    work_dir = Path(work_dir)
    try:
        if not checkpoint.reused:
            if target.source_storage_key is None:
                raise RuntimeError("canonical video media is unavailable")
            source_path = dependencies.media_store.open_path(
                owner_id=job.owner_id,
                storage_key=target.source_storage_key,
            )
            candidates = dependencies.frame_selector(
                source_path, work_dir / "selected", chapters=chapters
            )
            selected: list[SelectedFrameInput] = []
            for candidate in candidates:
                full = dependencies.media_store.import_file(
                    owner_id=job.owner_id,
                    source=candidate.full_path,
                    namespace="frames",
                    extension=".jpg",
                    maximum_bytes=25 * 1024 * 1024,
                )
                preview = dependencies.media_store.import_file(
                    owner_id=job.owner_id,
                    source=candidate.preview_path,
                    namespace="previews",
                    extension=".jpg",
                    maximum_bytes=10 * 1024 * 1024,
                )
                selected.append(
                    SelectedFrameInput(
                        frame_index=candidate.frame_index,
                        timestamp_ms=candidate.timestamp_ms,
                        selection_reasons=candidate.selection_reasons,
                        full_storage_backend=dependencies.media_store.backend,
                        full_storage_key=full.storage_key,
                        full_content_hash=full.content_hash,
                        preview_storage_backend=dependencies.media_store.backend,
                        preview_storage_key=preview.storage_key,
                        preview_content_hash=preview.content_hash,
                        perceptual_hash=candidate.perceptual_hash,
                        width=candidate.width,
                        height=candidate.height,
                    )
                )
            with connection.transaction():
                persisted = persist_selected_frames(
                    connection,
                    job_id=job.id,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                    frames=selected,
                )
                complete_stage_checkpoint(
                    connection,
                    job_id=job.id,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                    stage=Stage.FRAME_SELECTION,
                    dependency_hash=dependency_hash,
                    output_manifest={
                        "stage_version": FRAME_STAGE_VERSION,
                        "frame_count": len(persisted.frames),
                        "first_timestamp_ms": persisted.frames[0].timestamp_ms,
                        "last_timestamp_ms": persisted.frames[-1].timestamp_ms,
                    },
                )
        with connection.transaction():
            advance_stage(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                next_stage=Stage.OCR,
            )
            return release_claim(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
            )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_ocr(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    frames = load_stage_frames(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.OCR,
    )
    dependency_hash = _stable_hash(
        {
            "stage_version": OCR_STAGE_VERSION,
            "previews": [frame.preview_content_hash for frame in frames],
            "mode": "tesseract-psm-11-tsv",
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.OCR,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        results = []
        for frame in frames:
            path = dependencies.media_store.open_path(
                owner_id=job.owner_id,
                storage_key=frame.preview_storage_key,
            )
            value = dependencies.frame_ocr(path)
            results.append(
                OCRResultInput(
                    frame_id=frame.id,
                    text=value.text,
                    confidence=value.confidence,
                    engine="tesseract",
                    version="psm-11-tsv-v1",
                )
            )
        with connection.transaction():
            persisted = persist_ocr_results(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                results=results,
            )
            complete_stage_checkpoint(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                stage=Stage.OCR,
                dependency_hash=dependency_hash,
                output_manifest={
                    "stage_version": OCR_STAGE_VERSION,
                    "frame_count": len(persisted.frames),
                    "frames_with_text": sum(
                        bool(frame.ocr_text.strip()) for frame in persisted.frames
                    ),
                },
            )
    with connection.transaction():
        advance_stage(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            next_stage=Stage.VISUAL_ANALYSIS,
        )
        return release_claim(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def _run_visual_analysis(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    frames = load_stage_frames(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.VISUAL_ANALYSIS,
    )
    dependency_hash = _stable_hash(
        {
            "stage_version": VISUAL_STAGE_VERSION,
            "model": dependencies.visual_model,
            "prompt_version": PROMPT_VERSION,
            "frames": [
                [frame.preview_content_hash, frame.ocr_text] for frame in frames
            ],
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.VISUAL_ANALYSIS,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        analyzer = dependencies.visual_analyzer
        owned_client: OpenRouterVisualClient | None = None
        if analyzer is None:
            owned_client = OpenRouterVisualClient(model=dependencies.visual_model)
            analyzer = owned_client.analyze
        try:
            for start in range(0, len(frames), 2):
                group = frames[start : start + 2]
                _ensure_visual_budget(connection, job=job, estimated_cost="0.002")
                inputs = tuple(
                    VisualFrame(
                        frame_index=frame.frame_index,
                        timestamp_ms=frame.timestamp_ms,
                        image=dependencies.media_store.open_path(
                            owner_id=job.owner_id,
                            storage_key=frame.preview_storage_key,
                        ).read_bytes(),
                        mime_type="image/jpeg",
                        ocr_text=frame.ocr_text,
                    )
                    for frame in group
                )
                analysis = analyzer(inputs)
                by_index = {item.frame_index: item for item in analysis.frames}
                with connection.transaction():
                    for offset, frame in enumerate(group):
                        item = by_index[frame.frame_index]
                        details = _visual_details(analysis, item)
                        persist_visual_observation(
                            connection,
                            job_id=job.id,
                            worker_id=worker_id,
                            attempt_count=job.attempt_count,
                            observation=VisualObservationInput(
                                frame_id=frame.id,
                                group_frame_ids=tuple(value.id for value in group),
                                status="success",
                                visual_types=item.visual_types,
                                summary=item.summary,
                                visible_text=item.visible_text,
                                technical_details=details,
                                importance=item.importance,
                                confidence=item.confidence,
                                model_name=analysis.provenance.requested_model,
                                model_revision=analysis.provenance.model,
                                prompt_version=analysis.provenance.prompt_version,
                                input_hash=analysis.provenance.input_hash,
                                cost_usd=(
                                    round(analysis.provenance.cost_usd, 6)
                                    if offset == 0
                                    else Decimal("0")
                                ),
                            ),
                        )
        finally:
            if owned_client is not None:
                owned_client.close()
        cost = connection.execute(
            """
            select coalesce(sum(cost_usd), 0) as cost
            from video.visual_observations
            where owner_id = %s and video_id = %s and ingestion_version_id = %s
            """,
            (job.owner_id, job.video_id, job.target_version_id),
        ).fetchone()["cost"]
        complete_stage_checkpoint(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            stage=Stage.VISUAL_ANALYSIS,
            dependency_hash=dependency_hash,
            output_manifest={
                "stage_version": VISUAL_STAGE_VERSION,
                "frame_count": len(frames),
                "model": dependencies.visual_model,
            },
            cost_usd=cost,
        )
    with connection.transaction():
        advance_stage(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            next_stage=Stage.SPATIAL_REGIONS,
        )
        return release_claim(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def _run_spatial_regions(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    work_dir: Path,
    dependencies: VideoPipelineDependencies,
) -> VideoIngestionJob:
    frames = load_stage_frames(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.SPATIAL_REGIONS,
    )
    observations = connection.execute(
        """
        select id, frame_id, technical_details_json, model_name,
               model_revision, prompt_version, input_hash
        from video.visual_observations
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
          and status = 'success'
        order by frame_id
        """,
        (job.owner_id, job.video_id, job.target_version_id),
    ).fetchall()
    dependency_hash = _stable_hash(
        {
            "stage_version": SPATIAL_STAGE_VERSION,
            "observations": [
                [row["id"], row["input_hash"], row["technical_details_json"]]
                for row in observations
            ],
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.SPATIAL_REGIONS,
        dependency_hash=dependency_hash,
    )
    work_dir = Path(work_dir)
    try:
        if not checkpoint.reused:
            by_id = {frame.id: frame for frame in frames}
            regions: list[tuple[dict[str, Any], int, dict[str, Any], Any]] = []
            events: list[tuple[dict[str, Any], dict[str, Any], Any, Any]] = []
            for observation in observations:
                details = observation["technical_details_json"] or {}
                frame = by_id[observation["frame_id"]]
                for index, proposal in enumerate(details.get("regions") or []):
                    crop_path = _crop_region(
                        dependencies.media_store.open_path(
                            owner_id=job.owner_id,
                            storage_key=frame.full_storage_key,
                        ),
                        proposal,
                        work_dir / "regions" / f"{frame.id}-{index}.jpg",
                    )
                    stored = dependencies.media_store.import_file(
                        owner_id=job.owner_id,
                        source=crop_path,
                        namespace="regions",
                        extension=".jpg",
                        maximum_bytes=10 * 1024 * 1024,
                    )
                    regions.append((observation, index, proposal, stored))
                transition = details.get("transition_after")
                group_ids = details.get("analysis_group_frame_ids") or []
                if transition and len(group_ids) >= 2:
                    end_frame = by_id.get(int(group_ids[1]))
                    if end_frame is not None and end_frame.timestamp_ms > frame.timestamp_ms:
                        events.append((observation, transition, frame, end_frame))
            with connection.transaction():
                connection.execute(
                    """
                    delete from video.visual_events
                    where owner_id = %s and video_id = %s
                      and ingestion_version_id = %s
                    """,
                    (job.owner_id, job.video_id, job.target_version_id),
                )
                connection.execute(
                    """
                    delete from video.visual_regions
                    where owner_id = %s and video_id = %s
                      and ingestion_version_id = %s
                    """,
                    (job.owner_id, job.video_id, job.target_version_id),
                )
                for observation, region_index, proposal, stored in regions:
                    connection.execute(
                        """
                        insert into video.visual_regions (
                            owner_id, video_id, ingestion_version_id, frame_id,
                            visual_observation_id, region_index, region_type,
                            x, y, width, height, summary,
                            crop_storage_backend, crop_storage_key,
                            crop_content_hash, model_name, model_revision,
                            prompt_version, input_hash, confidence
                        ) values (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            job.owner_id,
                            job.video_id,
                            job.target_version_id,
                            observation["frame_id"],
                            observation["id"],
                            region_index,
                            proposal["region_type"],
                            proposal["x"],
                            proposal["y"],
                            proposal["width"],
                            proposal["height"],
                            proposal["summary"],
                            dependencies.media_store.backend,
                            stored.storage_key,
                            stored.content_hash,
                            observation["model_name"],
                            observation["model_revision"],
                            observation["prompt_version"],
                            observation["input_hash"],
                            proposal["confidence"],
                        ),
                    )
                for observation, transition, start_frame, end_frame in events:
                    connection.execute(
                        """
                        insert into video.visual_events (
                            owner_id, video_id, ingestion_version_id,
                            start_frame_id, end_frame_id, event_type,
                            start_ms, end_ms, summary, details_json,
                            model_name, model_revision, prompt_version, input_hash
                        ) values (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s
                        )
                        """,
                        (
                            job.owner_id,
                            job.video_id,
                            job.target_version_id,
                            start_frame.id,
                            end_frame.id,
                            transition["event_type"],
                            start_frame.timestamp_ms,
                            end_frame.timestamp_ms,
                            transition["summary"],
                            Jsonb(
                                {
                                    "technical_changes": transition.get(
                                        "technical_changes", []
                                    )
                                }
                            ),
                            observation["model_name"],
                            observation["model_revision"],
                            observation["prompt_version"],
                            observation["input_hash"],
                        ),
                    )
                complete_stage_checkpoint(
                    connection,
                    job_id=job.id,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                    stage=Stage.SPATIAL_REGIONS,
                    dependency_hash=dependency_hash,
                    output_manifest={
                        "stage_version": SPATIAL_STAGE_VERSION,
                        "region_count": len(regions),
                        "event_count": len(events),
                        "image_embeddings_deferred": True,
                    },
                )
        with connection.transaction():
            advance_stage(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                next_stage=Stage.INDEXING,
            )
            return release_claim(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
            )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_indexing(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
) -> VideoIngestionJob:
    transcript = _completed_manifest(connection, job=job, stage=Stage.TRANSCRIPT)
    visual = _completed_manifest(
        connection, job=job, stage=Stage.VISUAL_ANALYSIS
    )
    spatial = _completed_manifest(
        connection, job=job, stage=Stage.SPATIAL_REGIONS
    )
    dependency_hash = _stable_hash(
        {
            "stage_version": INDEX_STAGE_VERSION,
            "transcript": transcript,
            "visual": visual,
            "spatial": spatial,
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.INDEXING,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        with connection.transaction():
            built = rebuild_evidence(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                transcript_source_id=transcript["transcript_source_id"],
            )
            complete_stage_checkpoint(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                stage=Stage.INDEXING,
                dependency_hash=dependency_hash,
                output_manifest={
                    "stage_version": INDEX_STAGE_VERSION,
                    "transcript_count": built.transcript_count,
                    "visual_frame_count": built.visual_frame_count,
                    "visual_event_count": built.visual_event_count,
                    "total_count": built.total_count,
                    "retrieval": "postgres-simple-fts",
                },
            )
    with connection.transaction():
        advance_stage(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            next_stage=Stage.QUALITY_GATES,
        )
        return release_claim(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def _run_quality_gates(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
) -> VideoIngestionJob:
    transcript = _completed_manifest(connection, job=job, stage=Stage.TRANSCRIPT)
    indexing = _completed_manifest(connection, job=job, stage=Stage.INDEXING)
    dependency_hash = _stable_hash(
        {
            "stage_version": QUALITY_STAGE_VERSION,
            "transcript": transcript,
            "indexing": indexing,
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.QUALITY_GATES,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        with connection.transaction():
            quality = evaluate_quality_gates(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                transcript_source_id=transcript["transcript_source_id"],
            )
            persist_quality_gates(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                result=quality,
            )
            complete_stage_checkpoint(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                stage=Stage.QUALITY_GATES,
                dependency_hash=dependency_hash,
                output_manifest={
                    "stage_version": QUALITY_STAGE_VERSION,
                    "readiness": quality.readiness,
                    "metrics": quality.metrics,
                },
            )
    with connection.transaction():
        advance_stage(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            next_stage=Stage.PUBLISH,
        )
        return release_claim(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def _run_publish(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
) -> VideoIngestionJob:
    quality = _completed_manifest(
        connection, job=job, stage=Stage.QUALITY_GATES
    )
    dependency_hash = _stable_hash(
        {
            "stage_version": PUBLISH_STAGE_VERSION,
            "quality": quality,
        }
    )
    checkpoint = begin_stage_checkpoint(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        stage=Stage.PUBLISH,
        dependency_hash=dependency_hash,
    )
    if not checkpoint.reused:
        complete_stage_checkpoint(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            stage=Stage.PUBLISH,
            dependency_hash=dependency_hash,
            output_manifest={
                "stage_version": PUBLISH_STAGE_VERSION,
                "readiness": quality["readiness"],
            },
        )
    return publish_job(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        readiness=quality["readiness"],
    )


def _visual_details(
    analysis: VisualAnalysis, item: FrameVisualAnalysis
) -> dict[str, Any]:
    transition = item.transition_after
    return {
        **item.technical_details_json,
        "sequence_summary": analysis.sequence_summary,
        "regions": [
            {
                "region_type": region.region_type,
                "x": region.x,
                "y": region.y,
                "width": region.width,
                "height": region.height,
                "summary": region.summary,
                "confidence": region.confidence,
            }
            for region in item.regions
        ],
        "transition_after": (
            {
                "event_type": transition.event_type,
                "summary": transition.summary,
                "technical_changes": list(transition.technical_changes),
            }
            if transition is not None
            else None
        ),
    }


def _ensure_visual_budget(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    estimated_cost: str,
) -> None:
    row = connection.execute(
        """
        select j.cost_cap_usd, j.actual_cost_usd,
               coalesce(sum(observation.cost_usd), 0) as pending_visual_cost
        from video.ingestion_jobs j
        left join video.visual_observations observation
          on observation.owner_id = j.owner_id
         and observation.video_id = j.video_id
         and observation.ingestion_version_id = j.target_version_id
        where j.id = %s and j.owner_id = %s
        group by j.id
        """,
        (job.id, job.owner_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("video budget is unavailable")
    remaining = (
        row["cost_cap_usd"]
        - row["actual_cost_usd"]
        - row["pending_visual_cost"]
    )
    if remaining < Decimal(estimated_cost):
        raise VideoBudgetExceeded()


def _remaining_job_budget(
    connection: Connection, *, job: VideoIngestionJob
) -> Decimal:
    row = connection.execute(
        """
        select cost_cap_usd, actual_cost_usd
        from video.ingestion_jobs
        where id = %s and owner_id = %s
        """,
        (job.id, job.owner_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("video budget is unavailable")
    remaining = row["cost_cap_usd"] - row["actual_cost_usd"]
    if remaining <= 0:
        raise VideoBudgetExceeded()
    return remaining


def _write_transcript_artifact(
    destination: Path,
    *,
    duration_ms: int,
    language: str,
    cues: list[Any],
    provenance: dict[str, Any],
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "format_version": "openrouter-timestamped-transcript-v1",
        "duration_ms": duration_ms,
        "language": language,
        "cues": [
            {
                "cue_index": cue.cue_index,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "text": cue.text,
                "raw_text": cue.raw_text,
            }
            for cue in cues
        ],
        "provenance": provenance,
    }
    destination.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def _crop_region(source: Path, proposal: dict[str, Any], destination: Path) -> Path:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise RuntimeError("could not read canonical frame for region crop")
    height, width = image.shape[:2]
    left = max(0, min(width - 1, round(float(proposal["x"]) * width)))
    top = max(0, min(height - 1, round(float(proposal["y"]) * height)))
    right = max(
        left + 1,
        min(width, round((float(proposal["x"]) + float(proposal["width"])) * width)),
    )
    bottom = max(
        top + 1,
        min(
            height,
            round(
                (float(proposal["y"]) + float(proposal["height"])) * height
            ),
        ),
    )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not cv2.imwrite(str(destination), image[top:bottom, left:right]):
        raise RuntimeError("could not write visual region crop")
    return destination


def _completed_manifest(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    stage: Stage,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select output_manifest_json
        from video.ingestion_stage_checkpoints
        where owner_id = %s and ingestion_version_id = %s
          and stage = %s and status = 'complete'
        """,
        (job.owner_id, job.target_version_id, str(stage)),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"completed video stage is unavailable: {stage}")
    return dict(row["output_manifest_json"])


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _media_manifest(media: MediaMetadata) -> dict[str, Any]:
    return {
        "duration_ms": media.duration_ms,
        "width": media.width,
        "height": media.height,
        "video_codec": media.video_codec,
        "audio_codec": media.audio_codec,
        "format_name": media.format_name,
        "size_bytes": media.size_bytes,
    }
