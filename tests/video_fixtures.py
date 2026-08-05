"""Shared setup for tests that need a published, answerable video.

The pipeline fakes live here rather than in one suite because more than one
suite drives the real stage machinery: importing a TestCase to borrow its
helpers makes pytest collect that suite twice.
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from video.acquisition import Chapter, DownloadedSource, MediaMetadata
from video.frames import FrameCandidate
from video.vision import (
    FrameVisualAnalysis,
    TechnicalDetails,
    VisualAnalysis,
    VisualProvenance,
    VisualRegion,
    VisualTransition,
)

from tests.test_video_embeddings import FakeRegionEmbedder, FakeTextEmbedder
from video.evidence_store import rebuild_evidence
from video.jobs import claim_next_job
from video.repository import create_youtube_video
from video.transcript_store import persist_transcript
from video.transcripts import parse_webvtt


__all__ = [
    "FakeRegionEmbedder",
    "encoded_video_bytes",
    "FakeTextEmbedder",
    "FakeYouTubeAcquirer",
    "PublishedVideo",
    "analyze_two_frames",
    "publish_video_with_evidence",
    "select_two_frames",
]

DURATION_MS = 600_000
# The transcript cue and the frames sit more than a minute apart on purpose:
# the default timeline window must not reach the frames, so a visual question
# that matches only speech has to broaden its retrieval to find them.
TRANSCRIPT_TEXT = "today I will use the board to explain this"
FRAME_TIMESTAMPS = (120_000, 130_000)


_ENCODED_VIDEO: bytes | None = None


def encoded_video_bytes() -> bytes:
    """A real, tiny, decodable mp4 for fixtures that stand in for a source.

    Fixtures used to write a few ASCII bytes and call it a video. Acquisition
    now proves a frame decodes before promoting a source — the check that a
    production upload needed and did not have — so a fixture source has to be
    something a decoder can actually open.
    """

    global _ENCODED_VIDEO
    if _ENCODED_VIDEO is None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mp4"
            writer = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*"avc1"), 5, (320, 180)
            )
            if not writer.isOpened():  # pragma: no cover - local codec support
                writer = cv2.VideoWriter(
                    str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (320, 180)
                )
            for index in range(10):
                frame = np.full((180, 320, 3), 255, dtype=np.uint8)
                cv2.putText(
                    frame,
                    "ATTENTION" if index < 5 else "OUTPUT",
                    (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (0, 0, 0),
                    3,
                )
                writer.write(frame)
            writer.release()
            _ENCODED_VIDEO = path.read_bytes()
    return _ENCODED_VIDEO


@dataclass(frozen=True)
class PublishedVideo:
    video_id: UUID
    version_id: UUID
    job_id: UUID
    source_id: UUID
    title: str
    frame_ids: tuple[int, ...]


def publish_video_with_evidence(
    database,
    *,
    owner_id: UUID,
    title: str = "Attention lecture",
    readiness: str = "ready",
) -> PublishedVideo:
    """Create one video whose published version has real, mixed evidence."""

    created = create_youtube_video(
        database,
        owner_id=owner_id,
        idempotency_key=uuid4(),
        url="https://youtu.be/abcdefghijk",
        title=title,
    )
    database.execute(
        "update video.videos set duration_ms = %s where id = %s",
        (DURATION_MS, created.video_id),
    )
    database.execute(
        """
        update video.video_sources
        set status = 'ready', storage_backend = 'filesystem', storage_key = %s,
            content_hash = %s, size_bytes = 100, media_type = 'video/mp4',
            acquired_at = now()
        where id = %s
        """,
        (f"{owner_id}/canonical/videos/v.mp4", "a" * 64, created.source_id),
    )
    transcript = persist_transcript(
        database,
        owner_id=owner_id,
        video_id=created.video_id,
        video_source_id=created.source_id,
        source_kind="youtube_caption",
        language="en",
        provider="youtube",
        storage_backend="filesystem",
        storage_key=f"{owner_id}/canonical/transcripts/c.vtt",
        content_hash="b" * 64,
        cues=parse_webvtt(
            f"WEBVTT\n\n00:00.000 --> 00:10.000\n{TRANSCRIPT_TEXT}\n"
        ),
    )
    frame_ids = []
    for index, timestamp in enumerate(FRAME_TIMESTAMPS):
        frame_id = database.execute(
            """
            insert into video.frames (
                owner_id, video_id, ingestion_version_id, frame_index,
                timestamp_ms, selection_reasons, full_storage_backend,
                full_storage_key, full_content_hash, preview_storage_backend,
                preview_storage_key, preview_content_hash, perceptual_hash,
                width, height, ocr_text, ocr_confidence, ocr_engine, ocr_version
            ) values (
                %s, %s, %s, %s, %s, array['change_detected'], 'filesystem',
                %s, %s, 'filesystem', %s, %s, %s, 1280, 720, %s, 0.95,
                'tesseract', '5.5'
            ) returning id
            """,
            (
                owner_id,
                created.video_id,
                created.version_id,
                index,
                timestamp,
                f"{owner_id}/frames/{index}.jpg",
                f"{index + 1:064x}",
                f"{owner_id}/previews/{index}.jpg",
                f"{index + 3:064x}",
                f"{index + 5:016x}",
                "Softmax over query key products"
                if index == 0
                else "Weighted sum of values",
            ),
        ).fetchone()["id"]
        frame_ids.append(frame_id)
        database.execute(
            """
            insert into video.visual_observations (
                owner_id, video_id, ingestion_version_id, frame_id, status,
                visual_types, summary, visible_text, technical_details_json,
                importance, confidence, model_name, model_revision,
                prompt_version, input_hash
            ) values (
                %s, %s, %s, %s, 'success', array['diagram'], %s, %s, %s,
                0.9, 0.95, 'openai/gpt-5.6-luna', 'openai/gpt-5.6-luna',
                'video-visual-v1', %s
            )
            """,
            (
                owner_id,
                created.video_id,
                created.version_id,
                frame_id,
                "A scaled dot-product attention diagram is drawn"
                if index == 0
                else "The diagram gains an output vector",
                "softmax(QK^T/sqrt(d))V",
                Jsonb({"concepts": ["attention"]}),
                f"{index + 7:064x}",
            ),
        )

    claimed = claim_next_job(database, worker_id="fixture-worker")
    database.execute(
        "update video.ingestion_jobs set stage = 'indexing' where id = %s",
        (created.job_id,),
    )
    rebuild_evidence(
        database,
        job_id=created.job_id,
        worker_id="fixture-worker",
        attempt_count=claimed.attempt_count,
        transcript_source_id=transcript.id,
    )
    database.execute(
        """
        update video.ingestion_versions
        set status = %s, completed_at = now(), published_at = now(),
            quality_gates_json = %s
        where id = %s
        """,
        (
            "ready" if readiness == "ready" else "degraded",
            Jsonb({"readiness": readiness}),
            created.version_id,
        ),
    )
    database.execute(
        """
        update video.videos
        set readiness_status = %s, current_ingestion_version_id = %s,
            ready_at = now()
        where id = %s
        """,
        (readiness, created.version_id, created.video_id),
    )
    return PublishedVideo(
        video_id=created.video_id,
        version_id=created.version_id,
        job_id=created.job_id,
        source_id=created.source_id,
        title=title,
        frame_ids=tuple(frame_ids),
    )


class FakeYouTubeAcquirer:
    def __init__(self, *, on_call=None) -> None:
        self.calls = 0
        self.on_call = on_call

    def __call__(
        self,
        source_url,
        destination,
        *,
        expected_video_id,
        maximum_bytes,
    ) -> DownloadedSource:
        del source_url, maximum_bytes
        self.calls += 1
        root = Path(destination).resolve()
        video = root / "source.mp4"
        info = root / "source.info.json"
        caption = root / "source.en.vtt"
        video.write_bytes(encoded_video_bytes())
        info.write_text('{"id":"abcdefghijk"}', encoding="utf-8")
        caption.write_text(
            "WEBVTT\n\n00:00.000 --> 00:10.000\nhello\n",
            encoding="utf-8",
        )
        if self.on_call is not None:
            self.on_call()
        return DownloadedSource(
            video_id=expected_video_id,
            title="Lecture 1",
            description="Stanford course lecture",
            video_path=video,
            info_path=info,
            caption_paths=(caption,),
            media=MediaMetadata(
                duration_ms=10_000,
                width=1920,
                height=1080,
                video_codec="h264",
                audio_codec="aac",
                format_name="mov,mp4",
                size_bytes=len(encoded_video_bytes()),
            ),
            chapters=(Chapter(0, "Complete lecture", 0, 10_000),),
        )


def select_two_frames(
    video_path: Path, staging_dir: Path, *, chapters
) -> tuple[FrameCandidate, ...]:
    del video_path, chapters
    root = Path(staging_dir)
    root.mkdir(parents=True, exist_ok=True)
    candidates = []
    for index, timestamp in enumerate((0, 9_000)):
        full = root / f"frame-{index}-full.jpg"
        preview = root / f"frame-{index}-preview.jpg"
        image = np.full((100, 160, 3), 40 + index * 100, dtype=np.uint8)
        cv2.imwrite(str(full), image)
        cv2.imwrite(str(preview), image)
        candidates.append(
            FrameCandidate(
                frame_index=index,
                timestamp_ms=timestamp,
                selection_reasons=(
                    "first_frame" if index == 0 else "periodic_safeguard",
                ),
                full_path=full,
                preview_path=preview,
                full_content_hash=f"{index + 1:064x}",
                preview_content_hash=f"{index + 3:064x}",
                perceptual_hash=f"{index + 5:016x}",
                width=160,
                height=100,
                difference_score=1.0,
                chapter_index=0,
            )
        )
    return tuple(candidates)


def analyze_two_frames(frames) -> VisualAnalysis:
    details = TechnicalDetails(
        concepts=("attention",),
        relationships=("query connects to key",),
        equations=(),
        code_or_commands=(),
        chart_or_ui_details=(),
    )
    values = []
    for index, frame in enumerate(frames):
        values.append(
            FrameVisualAnalysis(
                frame_index=frame.frame_index,
                visual_types=("diagram",) if index == 0 else ("slide",),
                importance=0.9,
                confidence=0.95,
                summary=(
                    "Attention connects queries, keys, and values"
                    if index == 0
                    else "The resulting representation feeds the output"
                ),
                visible_text="Scaled dot-product attention",
                technical_details=details,
                transition_after=(
                    VisualTransition(
                        event_type="transition",
                        summary="The diagram changes to the output representation",
                        technical_changes=("attention output appears",),
                    )
                    if index == 0 and len(frames) > 1
                    else None
                ),
                regions=(
                    VisualRegion(
                        region_type="diagram",
                        x=0.1,
                        y=0.1,
                        width=0.8,
                        height=0.8,
                        summary="Attention block diagram",
                        confidence=0.95,
                    ),
                )
                if index == 0
                else (),
            )
        )
    return VisualAnalysis(
        sequence_summary="Attention is transformed into an output representation",
        frames=tuple(values),
        provenance=VisualProvenance(
            provider="openrouter",
            requested_model="openai/gpt-5.6-luna",
            model="openai/gpt-5.6-luna",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            input_hash="f" * 64,
            prompt_version="technical-lecture-visual-v1",
            attempt=1,
        ),
    )
