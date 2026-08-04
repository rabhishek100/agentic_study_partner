"""Shared setup for tests that need a published, answerable video."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from tests.test_video_embeddings import FakeRegionEmbedder, FakeTextEmbedder
from video.evidence_store import rebuild_evidence
from video.jobs import claim_next_job
from video.repository import create_youtube_video
from video.transcript_store import persist_transcript
from video.transcripts import parse_webvtt


__all__ = [
    "FakeRegionEmbedder",
    "FakeTextEmbedder",
    "PublishedVideo",
    "publish_video_with_evidence",
]

DURATION_MS = 600_000
# The transcript cue and the frames sit more than a minute apart on purpose:
# the default timeline window must not reach the frames, so a visual question
# that matches only speech has to broaden its retrieval to find them.
TRANSCRIPT_TEXT = "today I will use the board to explain this"
FRAME_TIMESTAMPS = (120_000, 130_000)


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
