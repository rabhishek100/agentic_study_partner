"""The acquire-source stage promotes URL and upload artifacts durably."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

import cv2
import numpy as np

from storage.database import connection, resolve_database_url
from tests.test_video_embeddings import FakeRegionEmbedder, FakeTextEmbedder
from tests.test_video_resources import write_deck
from tests.video_fixtures import (
    FakeYouTubeAcquirer,
    encoded_video_bytes,
    analyze_two_frames,
    select_two_frames,
)
from video.acquisition import Chapter, DownloadedSource, MediaMetadata
from video.acquisition_stage import (
    AcquisitionDependencies,
    run_acquire_source,
)
from video.audio import (
    AudioChunkProvenance,
    AudioTranscription,
    AudioTranscriptionProvenance,
)
from video.jobs import claim_next_job, get_job, request_cancellation
from video.media_store import FilesystemMediaStore
from video.pipeline import (
    VideoPipelineDependencies,
    _bounded_course_visual_frames,
    run_video_stage,
)
from video.course_repository import create_youtube_course
from video.repository import (
    complete_video_upload,
    create_url_resource,
    create_youtube_video,
    initialize_video_upload,
)
from video.resources import DownloadedResource
from video.states import Stage, Status
from video.transcripts import TranscriptCue
from video.frames import FrameCandidate, OcrResult
from video.vision import (
    FrameVisualAnalysis,
    TechnicalDetails,
    VisualAnalysis,
    VisualProvenance,
    VisualRegion,
    VisualTransition,
)


class S3NamedFilesystemMediaStore(FilesystemMediaStore):
    """Exercise backend provenance without requiring an object-store service."""

    backend = "s3"


class VideoAcquisitionStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FilesystemMediaStore(self.root / "media")
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-acquisition-stage.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))
        self.temporary.cleanup()

    def test_youtube_artifacts_are_promoted_and_next_stage_is_queued(self) -> None:
        acquirer = FakeYouTubeAcquirer()
        work_dir = self.root / "work" / "youtube"
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/abcdefghijk",
            )
            claimed = claim_next_job(database, worker_id="video-worker")
            outcome = run_acquire_source(
                database,
                job=claimed,
                worker_id="video-worker",
                work_dir=work_dir,
                dependencies=AcquisitionDependencies(
                    media_store=self.store,
                    youtube_acquirer=acquirer,
                    youtube_acquisition_version="2026.07.04",
                ),
            )
            source = database.execute(
                """
                select status, storage_key, content_hash, provenance_json
                from video.video_sources where id = %s
                """,
                (created.source_id,),
            ).fetchone()
            checkpoint = database.execute(
                """
                select status, output_manifest_json
                from video.ingestion_stage_checkpoints
                where ingestion_version_id = %s and stage = 'acquire_source'
                """,
                (created.version_id,),
            ).fetchone()

        self.assertEqual(acquirer.calls, 1)
        self.assertEqual(outcome.status, Status.QUEUED)
        self.assertEqual(outcome.stage, Stage.MEDIA_METADATA)
        self.assertEqual(source["status"], "acquiring")
        self.assertTrue(source["storage_key"].startswith(f"{self.owner}/canonical/"))
        self.assertEqual(checkpoint["status"], "complete")
        self.assertEqual(checkpoint["output_manifest_json"]["title"], "Lecture 1")
        self.assertEqual(len(checkpoint["output_manifest_json"]["captions"]), 1)
        self.assertFalse(work_dir.exists())
        self.store.verify_object(
            owner_id=self.owner,
            storage_key=source["storage_key"],
            expected_size=len(encoded_video_bytes()),
            expected_hash=source["content_hash"],
        )

    def test_manifest_records_the_selected_media_store_backend(self) -> None:
        store = S3NamedFilesystemMediaStore(self.root / "s3-named-media")
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/abcdefghijk",
            )
            claimed = claim_next_job(database, worker_id="video-worker")
            run_acquire_source(
                database,
                job=claimed,
                worker_id="video-worker",
                work_dir=self.root / "work" / "s3-manifest",
                dependencies=AcquisitionDependencies(
                    media_store=store,
                    youtube_acquirer=FakeYouTubeAcquirer(),
                ),
            )
            checkpoint = database.execute(
                """
                select output_manifest_json
                from video.ingestion_stage_checkpoints
                where ingestion_version_id = %s and stage = 'acquire_source'
                """,
                (created.version_id,),
            ).fetchone()["output_manifest_json"]

        self.assertEqual(checkpoint["source"]["storage_backend"], "s3")
        self.assertEqual(checkpoint["metadata"]["storage_backend"], "s3")
        self.assertTrue(checkpoint["captions"])
        self.assertTrue(
            all(item["storage_backend"] == "s3" for item in checkpoint["captions"])
        )

    def test_course_visual_budget_keeps_paid_prefix_and_samples_the_rest(self) -> None:
        database = MagicMock()
        course = MagicMock()
        course.fetchone.return_value = {"metadata_json": {}}
        duration = MagicMock()
        duration.fetchone.return_value = {"duration_ms": 3_600_000}
        observations = MagicMock()
        observations.fetchall.return_value = [
            {"frame_id": index} for index in range(1, 21)
        ]
        database.execute.side_effect = [course, duration, observations]
        frames = tuple(SimpleNamespace(id=index) for index in range(1, 101))
        job = SimpleNamespace(
            id=uuid4(),
            owner_id=self.owner,
            video_id=uuid4(),
            target_version_id=uuid4(),
        )

        bounded = _bounded_course_visual_frames(
            database, job=job, frames=frames
        )

        self.assertEqual(len(bounded), 60)
        self.assertEqual([frame.id for frame in bounded[:20]], list(range(1, 21)))
        self.assertEqual(bounded[-1].id, 100)

    def test_uploaded_object_is_verified_probed_and_promoted(self) -> None:
        payload = encoded_video_bytes()
        work_dir = self.root / "work" / "upload"
        with connection(self.database_url) as database:
            created = initialize_video_upload(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                original_filename="lecture.mp4",
                media_type="video/mp4",
                declared_size_bytes=len(payload),
            )
            writer = self.store.writer(
                owner_id=self.owner,
                storage_key=created.upload_storage_key,
                maximum_bytes=len(payload) + 1,
            )
            writer.write(payload)
            staged = writer.finish(expected_size=len(payload))
            complete_video_upload(
                database,
                created.job_id,
                owner_id=self.owner,
                storage_key=staged.storage_key,
                content_hash=staged.content_hash,
                size_bytes=staged.size_bytes,
                media_type="video/mp4",
            )
            claimed = claim_next_job(database, worker_id="video-worker")
            probes: list[Path] = []

            def probe(path: Path) -> MediaMetadata:
                probes.append(path)
                return MediaMetadata(
                    duration_ms=5_000,
                    width=1280,
                    height=720,
                    video_codec="h264",
                    audio_codec="aac",
                    format_name="mp4",
                    size_bytes=len(payload),
                )

            outcome = run_acquire_source(
                database,
                job=claimed,
                worker_id="video-worker",
                work_dir=work_dir,
                dependencies=AcquisitionDependencies(
                    media_store=self.store,
                    youtube_acquirer=lambda *args, **kwargs: self.fail(
                        "upload path called YouTube"
                    ),
                    media_probe=probe,
                ),
            )
            source = database.execute(
                """
                select status, storage_key, content_hash
                from video.video_sources where id = %s
                """,
                (created.source_id,),
            ).fetchone()

        self.assertEqual(len(probes), 1)
        self.assertEqual(outcome.stage, Stage.MEDIA_METADATA)
        self.assertEqual(source["status"], "acquiring")
        self.assertNotEqual(source["storage_key"], created.upload_storage_key)
        self.assertEqual(source["content_hash"], staged.content_hash)
        self.assertFalse(work_dir.exists())

    def test_cancellation_after_download_closes_job_without_promotion(self) -> None:
        work_dir = self.root / "work" / "cancelled"
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/abcdefghijk",
            )
            claimed = claim_next_job(database, worker_id="video-worker")
            acquirer = FakeYouTubeAcquirer(
                on_call=lambda: request_cancellation(
                    database, owner_id=self.owner, job_id=created.job_id
                )
            )
            outcome = run_acquire_source(
                database,
                job=claimed,
                worker_id="video-worker",
                work_dir=work_dir,
                dependencies=AcquisitionDependencies(
                    media_store=self.store,
                    youtube_acquirer=acquirer,
                ),
            )
            source = database.execute(
                "select status, storage_key from video.video_sources where id = %s",
                (created.source_id,),
            ).fetchone()
            stored = get_job(database, owner_id=self.owner, job_id=created.job_id)

        self.assertEqual(outcome.status, Status.CANCELLED)
        self.assertEqual(stored.status, Status.CANCELLED)
        self.assertEqual(source["status"], "pending")
        self.assertIsNone(source["storage_key"])
        self.assertFalse(work_dir.exists())

    def test_youtube_source_runs_through_the_complete_visual_pipeline(self) -> None:
        acquirer = FakeYouTubeAcquirer()

        def probe(path: Path) -> MediaMetadata:
            return MediaMetadata(
                duration_ms=10_000,
                width=1920,
                height=1080,
                video_codec="h264",
                audio_codec="aac",
                format_name="mov,mp4",
                size_bytes=path.stat().st_size,
            )

        dependencies = VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=acquirer,
            media_probe=probe,
            youtube_acquisition_version="2026.07.04",
            frame_selector=select_two_frames,
            frame_ocr=lambda path: OcrResult(
                text=("Attention diagram" if "frame-0" in path.name else "Output"),
                confidence=0.96,
                word_count=2,
            ),
            visual_analyzer=analyze_two_frames,
            # Match the pinned mock provenance, independent of production defaults.
            visual_model="openai/gpt-5.6-luna",
            text_embedder=FakeTextEmbedder(),
            image_embedder=FakeRegionEmbedder(),
            pdf_downloader=self._download_deck,
        )
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/abcdefghijk",
            )
            create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="Lecture slides",
                source_url="https://example.test/slides.pdf",
                role="slides",
                required=True,
            )
            for expected_stage in tuple(Stage):
                claimed = claim_next_job(
                    database,
                    worker_id="video-worker",
                    supported_stages={expected_stage},
                )
                self.assertEqual(claimed.stage, expected_stage)
                outcome = run_video_stage(
                    database,
                    job=claimed,
                    worker_id="video-worker",
                    work_dir=self.root / "work" / str(expected_stage),
                    dependencies=dependencies,
                )
            video = database.execute(
                """
                select title, description, duration_ms
                from video.videos where id = %s
                """,
                (created.video_id,),
            ).fetchone()
            source = database.execute(
                "select status, acquired_at from video.video_sources where id = %s",
                (created.source_id,),
            ).fetchone()
            transcript = database.execute(
                """
                select t.source_kind, t.coverage_ratio,
                       count(s.id) as segment_count
                from video.transcript_sources t
                join video.transcript_segments s
                  on s.transcript_source_id = t.id
                where t.video_source_id = %s
                group by t.id
                """,
                (created.source_id,),
            ).fetchone()
            derived = database.execute(
                """
                select
                    (select count(*) from video.frames
                     where ingestion_version_id = %s) as frames,
                    (select count(*) from video.visual_observations
                     where ingestion_version_id = %s) as observations,
                    (select count(*) from video.visual_regions
                     where ingestion_version_id = %s) as regions,
                    (select count(*) from video.visual_events
                     where ingestion_version_id = %s) as events,
                    (select count(*) from video.evidence_units
                     where ingestion_version_id = %s) as evidence,
                    (select count(*) from video.evidence_units
                     where ingestion_version_id = %s
                       and modality = 'resource_page') as page_evidence,
                    (select count(*) from video.evidence_embeddings
                     where ingestion_version_id = %s
                       and embedding_kind = 'text') as text_vectors,
                    (select count(*) from video.evidence_embeddings
                     where ingestion_version_id = %s
                       and embedding_kind = 'image') as image_vectors
                """,
                (created.version_id,) * 8,
            ).fetchone()
            gates = database.execute(
                "select quality_gates_json from video.ingestion_versions where id = %s",
                (created.version_id,),
            ).fetchone()["quality_gates_json"]

        self.assertEqual(outcome.status, Status.READY)
        self.assertIsNone(outcome.stage)
        self.assertEqual(video["title"], "Lecture 1")
        self.assertEqual(video["description"], "Stanford course lecture")
        self.assertEqual(video["duration_ms"], 10_000)
        self.assertEqual(source["status"], "ready")
        self.assertIsNotNone(source["acquired_at"])
        self.assertEqual(transcript["source_kind"], "youtube_caption")
        self.assertEqual(transcript["segment_count"], 1)
        self.assertEqual(
            dict(derived),
            {
                "frames": 2,
                "observations": 2,
                "regions": 1,
                "events": 1,
                # One transcript cue, two frames, one transition, three slides.
                "evidence": 7,
                "page_evidence": 3,
                "text_vectors": 7,
                "image_vectors": 1,
            },
        )
        self.assertTrue(gates["gates"]["semantic_index_complete"])
        self.assertTrue(gates["gates"]["required_resources_ready"])
        self.assertEqual(gates["resource_page_evidence_count"], 3)

    def test_course_job_stays_lexical_and_buys_no_embeddings(self) -> None:
        frame_limits: list[int | None] = []

        def select_course_frames(
            video_path: Path,
            staging_dir: Path,
            *,
            chapters,
            maximum_per_hour: int | None = None,
        ):
            frame_limits.append(maximum_per_hour)
            return select_two_frames(
                video_path,
                staging_dir,
                chapters=chapters,
                maximum_per_hour=maximum_per_hour,
            )

        def probe(path: Path) -> MediaMetadata:
            return MediaMetadata(
                duration_ms=10_000,
                width=1920,
                height=1080,
                video_codec="h264",
                audio_codec="aac",
                format_name="mov,mp4",
                size_bytes=path.stat().st_size,
            )

        dependencies = VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=FakeYouTubeAcquirer(),
            media_probe=probe,
            frame_selector=select_course_frames,
            frame_ocr=lambda path: OcrResult(
                text="Attention diagram", confidence=0.96, word_count=2
            ),
            visual_analyzer=analyze_two_frames,
            # Match the pinned mock provenance, independent of production defaults.
            visual_model="openai/gpt-5.6-luna",
        )
        with connection(self.database_url) as database:
            course = create_youtube_course(
                database,
                owner_id=self.owner,
                creation_key=uuid4(),
                title="Lexical course",
                lectures=[
                    {"url": "https://youtu.be/abcdefghijk", "title": "One"}
                ],
            )
            for expected_stage in tuple(Stage):
                claimed = claim_next_job(
                    database,
                    worker_id="video-worker",
                    supported_stages={expected_stage},
                )
                outcome = run_video_stage(
                    database,
                    job=claimed,
                    worker_id="video-worker",
                    work_dir=self.root / "work-course" / str(expected_stage),
                    dependencies=dependencies,
                )
            video_id = course.lectures[0].video_id
            stored = database.execute(
                """
                select video.readiness_status,
                       (select count(*) from video.evidence_embeddings
                        where owner_id = video.owner_id
                          and video_id = video.id) as embedding_count,
                       checkpoint.output_manifest_json
                from video.videos as video
                join video.ingestion_stage_checkpoints as checkpoint
                  on checkpoint.video_id = video.id
                 and checkpoint.stage = 'embeddings'
                where video.id = %s
                """,
                (video_id,),
            ).fetchone()

        self.assertEqual(outcome.status, Status.READY)
        self.assertEqual(stored["readiness_status"], "degraded")
        self.assertEqual(stored["embedding_count"], 0)
        self.assertFalse(stored["output_manifest_json"]["semantic_embeddings"])
        self.assertEqual(frame_limits, [60])

    def test_upload_without_captions_uses_budgeted_openrouter_audio(self) -> None:
        payload = encoded_video_bytes()
        calls = []

        def probe(path: Path) -> MediaMetadata:
            return MediaMetadata(
                duration_ms=5_000,
                width=1280,
                height=720,
                video_codec="h264",
                audio_codec="aac",
                format_name="mp4",
                size_bytes=path.stat().st_size,
            )

        def transcribe(
            media_path: Path,
            *,
            duration_ms: int,
            work_dir: Path,
            remaining_budget_usd,
            language: str,
        ) -> AudioTranscription:
            calls.append(
                (media_path, duration_ms, work_dir, remaining_budget_usd, language)
            )
            return AudioTranscription(
                cues=(
                    TranscriptCue(
                        cue_index=0,
                        start_ms=250,
                        end_ms=4_750,
                        text="Visual attention uses queries keys and values.",
                        raw_text=" Visual attention uses queries keys and values. ",
                    ),
                ),
                language="en",
                provenance=AudioTranscriptionProvenance(
                    provider="openrouter",
                    requested_model="openai/whisper-1",
                    total_estimated_cost_usd=0.006,
                    total_cost_usd=0.003,
                    chunk_duration_ms=600_000,
                    sample_rate_hz=16_000,
                    bitrate="64k",
                    chunks=(
                        AudioChunkProvenance(
                            chunk_index=0,
                            start_ms=0,
                            end_ms=5_000,
                            generation_id="gen-audio-1",
                            model="openai/whisper-1-2026-07",
                            input_hash="a" * 64,
                            estimated_cost_usd=0.006,
                            cost_usd=0.003,
                            attempt=1,
                            usage={"seconds": 5, "cost": 0.003},
                        ),
                    ),
                ),
            )

        dependencies = VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=lambda *args, **kwargs: self.fail(
                "upload path called YouTube"
            ),
            media_probe=probe,
            audio_transcriber=transcribe,
        )
        with connection(self.database_url) as database:
            created = initialize_video_upload(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                original_filename="lecture.mp4",
                media_type="video/mp4",
                declared_size_bytes=len(payload),
            )
            writer = self.store.writer(
                owner_id=self.owner,
                storage_key=created.upload_storage_key,
                maximum_bytes=len(payload) + 1,
            )
            writer.write(payload)
            staged = writer.finish(expected_size=len(payload))
            complete_video_upload(
                database,
                created.job_id,
                owner_id=self.owner,
                storage_key=staged.storage_key,
                content_hash=staged.content_hash,
                size_bytes=staged.size_bytes,
                media_type="video/mp4",
            )
            for expected_stage in (
                Stage.ACQUIRE_SOURCE,
                Stage.MEDIA_METADATA,
                Stage.TRANSCRIPT,
            ):
                claimed = claim_next_job(
                    database,
                    worker_id="video-worker",
                    supported_stages={expected_stage},
                )
                outcome = run_video_stage(
                    database,
                    job=claimed,
                    worker_id="video-worker",
                    work_dir=self.root / "work" / str(expected_stage),
                    dependencies=dependencies,
                )
            transcript = database.execute(
                """
                select source_kind, provider, model_name, model_revision,
                       storage_key, provenance_json, cost_usd
                from video.transcript_sources
                where video_source_id = %s
                """,
                (created.source_id,),
            ).fetchone()
            job = database.execute(
                """
                select actual_cost_usd from video.ingestion_jobs where id = %s
                """,
                (created.job_id,),
            ).fetchone()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 5_000)
        self.assertEqual(calls[0][4], "en")
        self.assertEqual(outcome.stage, Stage.RESOURCES)
        self.assertEqual(transcript["source_kind"], "openrouter_transcription")
        self.assertEqual(transcript["provider"], "openrouter")
        self.assertEqual(transcript["model_name"], "openai/whisper-1")
        self.assertEqual(
            transcript["model_revision"], "openai/whisper-1-2026-07"
        )
        self.assertEqual(transcript["provenance_json"]["processed_duration_ms"], 5_000)
        self.assertEqual(float(transcript["cost_usd"]), 0.003)
        self.assertEqual(float(job["actual_cost_usd"]), 0.003)
        raw = self.store.open_path(
            owner_id=self.owner, storage_key=transcript["storage_key"]
        )
        self.assertIn("gen-audio-1", raw.read_text(encoding="utf-8"))

    def _download_deck(self, url, destination, *, maximum_bytes, timeout=60.0):
        del url, maximum_bytes, timeout
        return DownloadedResource(
            path=write_deck(Path(destination)),
            media_type="application/pdf",
            size_bytes=Path(destination).stat().st_size,
            final_url="https://example.test/slides.pdf",
        )


if __name__ == "__main__":
    unittest.main()
