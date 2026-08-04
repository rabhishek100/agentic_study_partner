"""Real bytes, real decoding, real OCR — the path the fakes never exercised.

Every other suite injects a frame selector and an analyzer, which proves the
orchestration and nothing about whether this pipeline can read a video. The
first real file to reach it did so in production, where it failed on a codec
the image could not decode. This puts an actual encoded video through actual
OpenCV and actual Tesseract; only the paid model calls stay faked.
"""

from decimal import Decimal
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import cv2
import numpy as np

from storage.database import connection, resolve_database_url
from video.acquisition import AcquisitionError, verify_decodable
from video.embeddings import EmbeddingResult
from video.jobs import claim_next_job
from video.media_store import FilesystemMediaStore
from video.pipeline import VideoPipelineDependencies, run_video_stage
from video.repository import complete_video_upload, initialize_video_upload
from video.states import Stage, Status
from video.vision import (
    FrameVisualAnalysis,
    TechnicalDetails,
    VisualAnalysis,
    VisualProvenance,
)


WIDTH, HEIGHT, FPS, SECONDS = 640, 360, 5, 6


def write_video(path: Path) -> Path:
    """Encode a short video whose frames carry legible, changing text."""

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"avc1"), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():  # pragma: no cover - depends on local codecs
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
        )
    for index in range(FPS * SECONDS):
        frame = np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)
        # A hard cut halfway through, so change detection has something to find.
        label = "ATTENTION" if index < FPS * SECONDS // 2 else "SOFTMAX"
        cv2.putText(
            frame, label, (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 0), 5
        )
        writer.write(frame)
    writer.release()
    return path


class FakeEmbedder:
    model_name = "local/fake-embedding"
    model_revision = "hosted"
    dimension = 4

    def embed_documents(self, texts):
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            model=self.model_name,
            cost_usd=Decimal(0),
        )

    def embed_query(self, text):
        return self.embed_documents([text])

    def embed_regions(self, regions):
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0, 0.0, 0.0) for _ in regions),
            model=self.model_name,
            cost_usd=Decimal(0),
        )


def analyze(frames) -> VisualAnalysis:
    return VisualAnalysis(
        sequence_summary="Text on screen changes",
        frames=tuple(
            FrameVisualAnalysis(
                frame_index=frame.frame_index,
                visual_types=("slide",),
                importance=0.7,
                confidence=0.9,
                summary=f"Frame at {frame.timestamp_ms} ms",
                visible_text=(frame.ocr_text or "")[:200],
                technical_details=TechnicalDetails(
                    concepts=("attention",),
                    relationships=(),
                    equations=(),
                    code_or_commands=(),
                    chart_or_ui_details=(),
                ),
                transition_after=None,
                regions=(),
            )
            for frame in frames
        ),
        provenance=VisualProvenance(
            provider="local",
            requested_model="local/fake-vision",
            model="local/fake-vision",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            input_hash="a" * 64,
            prompt_version="technical-lecture-visual-v1",
            attempt=1,
        ),
    )


@unittest.skipIf(shutil.which("tesseract") is None, "requires Tesseract")
class RealMediaIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FilesystemMediaStore(self.root / "media")
        self.video = write_video(self.root / "source" / "clip.mp4")
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-real-media.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))
        self.temporary.cleanup()

    def test_an_encoded_video_decodes_ocrs_and_publishes(self) -> None:
        payload = self.video.read_bytes()
        dependencies = VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=lambda *args, **kwargs: None,
            visual_analyzer=analyze,
            text_embedder=FakeEmbedder(),
            image_embedder=FakeEmbedder(),
        )
        with connection(self.database_url) as database:
            created = initialize_video_upload(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                original_filename="clip.mp4",
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
            outcome = None
            for _ in range(len(Stage) + 2):
                claimed = claim_next_job(database, worker_id="real-media-worker")
                if claimed is None:
                    break
                if claimed.stage is Stage.TRANSCRIPT:
                    # No captions and no audio: the transcript stage is the one
                    # part that must call a provider, so it is out of scope.
                    break
                outcome = run_video_stage(
                    database,
                    job=claimed,
                    worker_id="real-media-worker",
                    work_dir=self.root / "work" / uuid4().hex,
                    dependencies=dependencies,
                )
            probed = database.execute(
                """
                select media_metadata_json from video.video_sources
                where id = %s
                """,
                (created.source_id,),
            ).fetchone()

        self.assertEqual(outcome.stage, Stage.TRANSCRIPT)
        # ffprobe read the real container, not a fixture.
        self.assertGreaterEqual(
            int(probed["media_metadata_json"]["duration_ms"]), SECONDS * 900
        )

    def test_frame_selection_and_ocr_read_the_actual_pixels(self) -> None:
        from video.frames import run_tesseract, select_frames

        staging = self.root / "frames"
        candidates = select_frames(self.video, staging, chapters=())
        self.assertTrue(candidates, "change detection found no frames")
        text = " ".join(
            run_tesseract(candidate.full_path).text for candidate in candidates
        ).upper()
        # The words were drawn into the frames; reading them back proves the
        # decode and OCR path works on real media rather than on a fixture.
        self.assertIn("ATTENTION", text)

    def test_no_transaction_is_held_open_across_a_provider_call(self) -> None:
        """A held transaction keeps FOR UPDATE locks on the job and version.

        In production that starved the lease renewal on the worker's other
        connection: the lease expired mid-stage, every paid observation sat in
        an uncommitted transaction, and the whole stage rolled back after
        spending real money on calls that could never land.
        """

        from psycopg import pq

        observed: list[str] = []

        def watching_analyzer(frames):
            observed.append(connection_holder[0].info.transaction_status.name)
            return analyze(frames)

        connection_holder: list = []
        payload = self.video.read_bytes()
        dependencies = VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=lambda *args, **kwargs: None,
            visual_analyzer=watching_analyzer,
            text_embedder=FakeEmbedder(),
            image_embedder=FakeEmbedder(),
        )
        with connection(self.database_url) as database:
            connection_holder.append(database)
            created = initialize_video_upload(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                original_filename="clip.mp4",
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
            for stage in (
                Stage.ACQUIRE_SOURCE,
                Stage.MEDIA_METADATA,
                Stage.FRAME_SELECTION,
                Stage.OCR,
                Stage.VISUAL_ANALYSIS,
            ):
                database.execute(
                    "update video.ingestion_jobs set stage = %s, status = 'queued',"
                    " lease_owner = null, lease_expires_at = null where id = %s",
                    (str(stage), created.job_id),
                )
                claimed = claim_next_job(
                    database, worker_id="txn-worker", supported_stages={stage}
                )
                run_video_stage(
                    database,
                    job=claimed,
                    worker_id="txn-worker",
                    work_dir=self.root / "work" / uuid4().hex,
                    dependencies=dependencies,
                )

        self.assertTrue(observed, "the visual analyzer was never called")
        for status in observed:
            self.assertEqual(status, pq.TransactionStatus.IDLE.name)

    def test_an_undecodable_file_is_refused_at_acquisition(self) -> None:
        broken = self.root / "broken.mp4"
        broken.write_bytes(b"\x00\x01\x02not really a video\x03" * 64)

        with self.assertRaisesRegex(AcquisitionError, "cannot (open|decode)"):
            verify_decodable(broken)


if __name__ == "__main__":
    unittest.main()
