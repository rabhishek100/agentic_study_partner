"""The acquire-source stage promotes URL and upload artifacts durably."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import cv2
import numpy as np

from storage.database import connection, resolve_database_url
from tests.test_video_embeddings import FakeRegionEmbedder, FakeTextEmbedder
from tests.test_video_resources import write_deck
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
from video.pipeline import VideoPipelineDependencies, run_video_stage
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
        video.write_bytes(b"youtube-video")
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
                size_bytes=len(b"youtube-video"),
            ),
            chapters=(Chapter(0, "Complete lecture", 0, 10_000),),
        )


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
            expected_size=len(b"youtube-video"),
            expected_hash=source["content_hash"],
        )

    def test_uploaded_object_is_verified_probed_and_promoted(self) -> None:
        payload = b"uploaded-video"
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
                maximum_bytes=100,
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
            frame_selector=self._select_two_frames,
            frame_ocr=lambda path: OcrResult(
                text=("Attention diagram" if "frame-0" in path.name else "Output"),
                confidence=0.96,
                word_count=2,
            ),
            visual_analyzer=self._analyze_two_frames,
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

    def test_upload_without_captions_uses_budgeted_openrouter_audio(self) -> None:
        payload = b"uploaded-video-with-audio"
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
                maximum_bytes=100,
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

    @staticmethod
    def _select_two_frames(
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

    @staticmethod
    def _analyze_two_frames(frames) -> VisualAnalysis:
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


if __name__ == "__main__":
    unittest.main()
