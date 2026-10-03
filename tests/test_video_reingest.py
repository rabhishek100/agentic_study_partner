"""A replacement version inherits finished work instead of paying again."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from tests.test_video_embeddings import FakeRegionEmbedder, FakeTextEmbedder
from tests.video_fixtures import encoded_video_bytes
from tests.test_video_resources import write_deck
from tests.video_fixtures import (
    FakeYouTubeAcquirer,
    analyze_two_frames,
    select_two_frames,
)
from video.acquisition import MediaMetadata
from video.frames import OcrResult
from video.jobs import claim_next_job
from video.media_store import FilesystemMediaStore
from video.pipeline import VideoPipelineDependencies, run_video_stage
from video.repository import (
    VideoConflictError,
    complete_video_upload,
    initialize_video_upload,
    record_caption_upload,
    VideoNotFoundError,
    create_url_resource,
    create_youtube_video,
    reingest_video,
)
from video.resources import DownloadedResource
from video.states import Stage, Status


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


class VideoReingestTests(unittest.TestCase):
    """Version one is built by the real pipeline so its checkpoints are real.

    Reuse is keyed by dependency hashes the stages compute themselves, so a
    fixture that invents those hashes would prove nothing about whether a
    rebuild actually skips the work.
    """

    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FilesystemMediaStore(self.root / "media")
        self.deck = write_deck(self.root / "source" / "slides.pdf")
        self.downloads: list[str] = []
        self.acquirer = FakeYouTubeAcquirer()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-reingest.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )
        self.temporary.cleanup()

    def _download(self, url, destination, *, maximum_bytes, timeout=60.0):
        del maximum_bytes, timeout
        self.downloads.append(url)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.deck.read_bytes())
        return DownloadedResource(
            path=destination,
            media_type="application/pdf",
            size_bytes=destination.stat().st_size,
            final_url=url,
        )

    def _dependencies(self, *, refuse_paid_work: bool = False):
        def refuse_frames(*args, **kwargs):
            raise AssertionError("frame selection must not run again")

        def refuse_analysis(frames):
            raise AssertionError("visual analysis must not run again")

        return VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=self.acquirer,
            media_probe=probe,
            youtube_acquisition_version="2026.07.04",
            frame_selector=refuse_frames if refuse_paid_work else select_two_frames,
            frame_ocr=lambda path: OcrResult(
                text="Attention diagram", confidence=0.96, word_count=2
            ),
            visual_analyzer=refuse_analysis if refuse_paid_work else analyze_two_frames,
            # Match the pinned mock provenance, independent of production defaults.
            visual_model="openai/gpt-5.6-luna",
            text_embedder=FakeTextEmbedder(),
            image_embedder=FakeRegionEmbedder(),
            pdf_downloader=self._download,
        )

    def _drain(self, database, dependencies) -> object:
        outcome = None
        for _ in range(len(Stage) + 2):
            claimed = claim_next_job(database, worker_id="video-worker")
            if claimed is None:
                break
            outcome = run_video_stage(
                database,
                job=claimed,
                worker_id="video-worker",
                work_dir=self.root / "work" / uuid4().hex,
                dependencies=dependencies,
            )
        return outcome

    def _published_video(self, database):
        created = create_youtube_video(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            url="https://youtu.be/abcdefghijk",
            title="Attention lecture",
        )
        outcome = self._drain(database, self._dependencies())
        self.assertEqual(outcome.status, Status.READY)
        return created

    def test_carries_finished_work_into_the_replacement_version(self) -> None:
        key = uuid4()
        with connection(self.database_url) as database:
            created = self._published_video(database)
            create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="Lecture slides",
                source_url="https://example.test/slides.pdf",
                role="slides",
            )
            rebuild = reingest_video(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                idempotency_key=key,
            )
            replay = reingest_video(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                idempotency_key=key,
            )
            with self.assertRaises(VideoConflictError):
                reingest_video(
                    database,
                    owner_id=self.owner,
                    video_id=created.video_id,
                    idempotency_key=uuid4(),
                )
            counts = database.execute(
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
                    (select count(*) from video.ingestion_stage_checkpoints
                     where ingestion_version_id = %s
                       and status = 'complete') as checkpoints,
                    (select coalesce(sum(actual_cost_usd), 0)
                     from video.ingestion_stage_checkpoints
                     where ingestion_version_id = %s) as carried_cost
                """,
                (rebuild.version_id,) * 6,
            ).fetchone()
            version = database.execute(
                """
                select version_number, status from video.ingestion_versions
                where id = %s
                """,
                (rebuild.version_id,),
            ).fetchone()
            still_published = database.execute(
                """
                select readiness_status, current_ingestion_version_id
                from video.videos where id = %s
                """,
                (created.video_id,),
            ).fetchone()

        self.assertNotEqual(rebuild.version_id, created.version_id)
        self.assertEqual(version["version_number"], 2)
        self.assertEqual(version["status"], "building")
        self.assertEqual(rebuild.job_status, "queued")
        # The same key replays the same rebuild; a different key while one is
        # queued is a conflict, because a video has one active job.
        self.assertEqual(replay.job_id, rebuild.job_id)
        self.assertEqual(
            dict(counts),
            {
                "frames": 2,
                "observations": 2,
                "regions": 1,
                "events": 1,
                "checkpoints": 7,
                # The earlier version already paid for these stages; charging
                # the copy would double-count the video's spend.
                "carried_cost": 0,
            },
        )
        # The reader keeps the answers they already had until the swap.
        self.assertEqual(still_published["readiness_status"], "ready")
        self.assertEqual(
            still_published["current_ingestion_version_id"], created.version_id
        )

    def test_timeline_repair_keeps_the_published_version_but_rebuilds_frames(self) -> None:
        with connection(self.database_url) as database:
            created = self._published_video(database)
            repair = reingest_video(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                idempotency_key=uuid4(),
                quality_profile="course-full-quality-v1",
                rebuild_frames=True,
            )
            state = database.execute(
                """
                select job.stage,
                       (select count(*) from video.frames
                        where ingestion_version_id = job.target_version_id) as frames,
                       (select count(*) from video.visual_observations
                        where ingestion_version_id = job.target_version_id) as observations,
                       source.current_ingestion_version_id
                from video.ingestion_jobs job
                join video.videos source on source.id = job.video_id
                where job.id = %s
                """,
                (repair.job_id,),
            ).fetchone()

        self.assertEqual(state["stage"], "frame_selection")
        self.assertEqual(state["frames"], 0)
        self.assertEqual(state["observations"], 0)
        self.assertEqual(state["current_ingestion_version_id"], created.version_id)

    def test_the_rebuild_reads_the_new_document_without_repaying(self) -> None:
        with connection(self.database_url) as database:
            created = self._published_video(database)
            downloads_before = self.acquirer.calls
            create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="Lecture slides",
                source_url="https://example.test/slides.pdf",
                role="slides",
            )
            rebuild = reingest_video(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                idempotency_key=uuid4(),
            )
            outcome = self._drain(
                database, self._dependencies(refuse_paid_work=True)
            )
            evidence = database.execute(
                """
                select modality, count(*) as count from video.evidence_units
                where ingestion_version_id = %s group by modality order by modality
                """,
                (rebuild.version_id,),
            ).fetchall()
            published = database.execute(
                """
                select readiness_status, current_ingestion_version_id
                from video.videos where id = %s
                """,
                (created.video_id,),
            ).fetchone()
            superseded = database.execute(
                "select status from video.ingestion_versions where id = %s",
                (created.version_id,),
            ).fetchone()

        self.assertEqual(outcome.status, Status.READY)
        # The dependencies would raise if frame selection or the visual model
        # ran, and the video is not downloaded a second time either.
        self.assertEqual(self.acquirer.calls, downloads_before)
        self.assertEqual(self.downloads, ["https://example.test/slides.pdf"])
        self.assertEqual(
            {row["modality"]: row["count"] for row in evidence},
            {
                "resource_page": 3,
                "transcript": 1,
                "visual_event": 1,
                "visual_frame": 2,
            },
        )
        # The swap happens once, at the end, and the old version survives it.
        self.assertEqual(published["current_ingestion_version_id"], rebuild.version_id)
        self.assertIn(published["readiness_status"], {"ready", "degraded"})
        self.assertEqual(superseded["status"], "ready")

    def test_an_uploaded_source_is_not_re_acquired_by_a_rebuild(self) -> None:
        """The acquire stage identifies an upload by the bytes staged for it.

        A rebuild that dropped that identity failed its inherited checkpoint
        and tried to acquire the source again — but an upload's staging object
        is gone once promoted, so the rebuild died at stage one with "uploaded
        video has no completed staging object".
        """

        payload = encoded_video_bytes()
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
            # Captions keep the transcript stage away from paid audio, the
            # same way an uploaded lecture does in practice.
            vtt = self.root / "clip.en.vtt"
            vtt.write_text(
                "WEBVTT\n\n00:00.000 --> 00:09.500\nattention weights\n",
                encoding="utf-8",
            )
            stored_caption = self.store.import_file(
                owner_id=self.owner,
                source=vtt,
                namespace="captions",
                extension=".vtt",
                maximum_bytes=1024 * 1024,
            )
            record_caption_upload(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                video_source_id=created.source_id,
                original_filename="clip.en.vtt",
                storage_backend=self.store.backend,
                storage_key=stored_caption.storage_key,
                content_hash=stored_caption.content_hash,
                size_bytes=stored_caption.size_bytes,
                cue_count=1,
            )
            outcome = self._drain(database, self._dependencies())
            self.assertEqual(outcome.status, Status.READY)

            rebuild = reingest_video(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                idempotency_key=uuid4(),
            )
            claimed = claim_next_job(
                database,
                worker_id="video-worker",
                supported_stages={Stage.ACQUIRE_SOURCE},
            )
            # The staging object is deliberately not touched: the inherited
            # checkpoint must carry the stage without re-acquiring anything.
            advanced = run_video_stage(
                database,
                job=claimed,
                worker_id="video-worker",
                work_dir=self.root / "work" / uuid4().hex,
                dependencies=self._dependencies(refuse_paid_work=True),
            )

        self.assertEqual(rebuild.job_status, "queued")
        self.assertEqual(advanced.stage, Stage.MEDIA_METADATA)

        # And again from the rebuild: the second one must take its staging
        # identity from the job that received the bytes, not from the rebuild
        # before it, which never had any.
        with connection(self.database_url) as database:
            database.execute(
                """
                update video.ingestion_jobs set status = 'failed',
                    completed_at = now(), lease_owner = null,
                    lease_expires_at = null
                where id = %s
                """,
                (rebuild.job_id,),
            )
            second = reingest_video(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                idempotency_key=uuid4(),
            )
            carried = database.execute(
                """
                select staging_storage_key is not null as staged,
                       upload_completed_at is not null as completed
                from video.ingestion_jobs where id = %s
                """,
                (second.job_id,),
            ).fetchone()
        self.assertTrue(carried["staged"])
        self.assertTrue(carried["completed"])

    def test_rejects_a_rebuild_that_has_nothing_to_build_on(self) -> None:
        with connection(self.database_url) as database:
            fresh = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/bcdefghijkl",
            )
            with self.assertRaises(VideoConflictError):
                reingest_video(
                    database,
                    owner_id=self.owner,
                    video_id=fresh.video_id,
                    idempotency_key=uuid4(),
                )
            with self.assertRaises(VideoNotFoundError):
                reingest_video(
                    database,
                    owner_id=self.owner,
                    video_id=uuid4(),
                    idempotency_key=uuid4(),
                )

    def test_another_owner_cannot_rebuild_this_video(self) -> None:
        with connection(self.database_url) as database:
            created = self._published_video(database)
            with self.assertRaises(VideoNotFoundError):
                reingest_video(
                    database,
                    owner_id=self.other_owner,
                    video_id=created.video_id,
                    idempotency_key=uuid4(),
                )


if __name__ == "__main__":
    unittest.main()
