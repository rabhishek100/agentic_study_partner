"""Visual evidence persistence remains owner, version, and lease scoped."""

from decimal import Decimal
import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.jobs import claim_next_job
from video.repository import create_youtube_video
from video.states import Stage
from video.visual_store import (
    OCRResultInput,
    SelectedFrameInput,
    VideoVisualStoreConflictError,
    VideoVisualStoreNotFoundError,
    VisualObservationInput,
    load_stage_frames,
    persist_ocr_results,
    persist_selected_frames,
    persist_visual_observation,
)


class VideoVisualStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-visual-store.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def create_claim(self, database):
        created = create_youtube_video(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            url="https://youtu.be/abcdefghijk",
        )
        claimed = claim_next_job(database, worker_id="visual-worker")
        self.assertEqual(claimed.id, created.job_id)
        database.execute(
            "update video.videos set duration_ms = 10000 where id = %s",
            (created.video_id,),
        )
        return created, claimed

    @staticmethod
    def set_stage(database, job_id, stage: Stage) -> None:
        database.execute(
            "update video.ingestion_jobs set stage = %s where id = %s",
            (str(stage), job_id),
        )

    def frame(self, index: int, timestamp_ms: int, *, width: int = 1920):
        digest = f"{index + 1:064x}"
        return SelectedFrameInput(
            frame_index=index,
            timestamp_ms=timestamp_ms,
            selection_reasons=("scene_change", "ocr_change"),
            full_storage_backend="filesystem",
            full_storage_key=f"{self.owner}/frames/{digest}.png",
            full_content_hash=digest,
            preview_storage_backend="filesystem",
            preview_storage_key=f"{self.owner}/previews/{digest}.webp",
            preview_content_hash=digest,
            perceptual_hash=f"{index + 1:016x}",
            width=width,
            height=1080,
        )

    def store_frames(self, database, claimed):
        self.set_stage(database, claimed.id, Stage.FRAME_SELECTION)
        return persist_selected_frames(
            database,
            job_id=claimed.id,
            worker_id="visual-worker",
            attempt_count=claimed.attempt_count,
            frames=(self.frame(0, 1000), self.frame(1, 5000)),
        )

    def observation(self, frame_ids, **overrides):
        values = {
            "frame_id": frame_ids[0],
            "group_frame_ids": tuple(frame_ids),
            "status": "success",
            "visual_types": ("slide", "diagram"),
            "summary": "A computation graph with gradient arrows.",
            "visible_text": "loss gradient weights",
            "technical_details": {
                "region_proposals": [
                    {
                        "type": "diagram",
                        "bbox": [0.11, 0.22, 0.73, 0.51],
                        "raw_label": "network diagram",
                    }
                ]
            },
            "importance": 0.9,
            "confidence": 0.95,
            "model_name": "google/gemini-2.5-flash",
            "model_revision": "openrouter-2026-08",
            "prompt_version": "visual-v1",
            "input_hash": "a" * 64,
            "cost_usd": Decimal("0.001234"),
        }
        values.update(overrides)
        return VisualObservationInput(**values)

    def test_complete_frame_batch_is_fenced_idempotent_and_replaceable(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            self.set_stage(database, claimed.id, Stage.FRAME_SELECTION)
            with self.assertRaises(VideoVisualStoreNotFoundError):
                persist_selected_frames(
                    database,
                    job_id=claimed.id,
                    worker_id="stale-worker",
                    attempt_count=claimed.attempt_count,
                    frames=(self.frame(0, 1000),),
                )
            first = self.store_frames(database, claimed)
            replay = persist_selected_frames(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                frames=(self.frame(0, 1000), self.frame(1, 5000)),
            )
            changed = persist_selected_frames(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                frames=(self.frame(0, 1000, width=1280), self.frame(1, 5000)),
            )
            stored_count = database.execute(
                "select count(*) as count from video.frames where owner_id = %s",
                (self.owner,),
            ).fetchone()["count"]

        self.assertFalse(first.replayed)
        self.assertFalse(first.replaced)
        self.assertTrue(replay.replayed)
        self.assertEqual(
            [frame.id for frame in replay.frames], [frame.id for frame in first.frames]
        )
        self.assertTrue(changed.replaced)
        self.assertFalse(changed.replayed)
        self.assertEqual(changed.frames[0].width, 1280)
        self.assertEqual(stored_count, 2)

    def test_frame_loads_are_stage_and_version_scoped(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            selected = self.store_frames(database, claimed)
            self.set_stage(database, claimed.id, Stage.OCR)
            loaded = load_stage_frames(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                stage=Stage.OCR,
            )
            with self.assertRaises(VideoVisualStoreNotFoundError):
                load_stage_frames(
                    database,
                    job_id=claimed.id,
                    worker_id="visual-worker",
                    attempt_count=claimed.attempt_count,
                    stage=Stage.INDEXING,
                )
            with self.assertRaises(ValueError):
                load_stage_frames(
                    database,
                    job_id=claimed.id,
                    worker_id="visual-worker",
                    attempt_count=claimed.attempt_count,
                    stage=Stage.TRANSCRIPT,
                )

        self.assertEqual(
            [frame.id for frame in loaded], [frame.id for frame in selected.frames]
        )
        self.assertTrue(all(frame.owner_id == self.owner for frame in loaded))

    def test_ocr_requires_complete_coverage_and_updates_derived_drift(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            selected = self.store_frames(database, claimed)
            self.set_stage(database, claimed.id, Stage.OCR)
            inputs = tuple(
                OCRResultInput(
                    frame_id=frame.id,
                    text=f"Frame {frame.frame_index} visible text",
                    confidence=0.96,
                    engine="tesseract",
                    version="5.5.1",
                )
                for frame in selected.frames
            )
            with self.assertRaises(VideoVisualStoreConflictError):
                persist_ocr_results(
                    database,
                    job_id=claimed.id,
                    worker_id="visual-worker",
                    attempt_count=claimed.attempt_count,
                    results=inputs[:1],
                )
            first = persist_ocr_results(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                results=inputs,
            )
            replay = persist_ocr_results(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                results=inputs,
            )
            changed_inputs = (
                OCRResultInput(
                    frame_id=inputs[0].frame_id,
                    text="Corrected OCR text",
                    confidence=0.99,
                    engine="tesseract",
                    version="5.5.1",
                ),
                inputs[1],
            )
            changed = persist_ocr_results(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                results=changed_inputs,
            )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertFalse(changed.replayed)
        self.assertEqual(changed.frames[0].ocr_text, "Corrected OCR text")

    def test_visual_group_replay_cost_and_bbox_proposals_are_durable(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            selected = self.store_frames(database, claimed)
            self.set_stage(database, claimed.id, Stage.VISUAL_ANALYSIS)
            frame_ids = tuple(frame.id for frame in selected.frames)
            value = self.observation(frame_ids)
            first = persist_visual_observation(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                observation=value,
            )
            replay = persist_visual_observation(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                observation=value,
            )
            row = database.execute(
                """
                select technical_details_json, cost_usd
                from video.visual_observations
                where owner_id = %s and frame_id = %s
                """,
                (self.owner, frame_ids[0]),
            ).fetchone()
            count = database.execute(
                """
                select count(*) as count, sum(cost_usd) as cost
                from video.visual_observations where owner_id = %s
                """,
                (self.owner,),
            ).fetchone()

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.id, replay.id)
        self.assertEqual(count["count"], 1)
        self.assertEqual(count["cost"], Decimal("0.001234"))
        self.assertEqual(
            row["technical_details_json"]["analysis_group_frame_ids"],
            list(frame_ids),
        )
        self.assertEqual(
            row["technical_details_json"]["region_proposals"][0]["bbox"],
            [0.11, 0.22, 0.73, 0.51],
        )

    def test_observation_drift_replaces_row_and_cascades_stale_regions(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            selected = self.store_frames(database, claimed)
            self.set_stage(database, claimed.id, Stage.VISUAL_ANALYSIS)
            frame_ids = tuple(frame.id for frame in selected.frames)
            first = persist_visual_observation(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                observation=self.observation(frame_ids),
            )
            database.execute(
                """
                insert into video.visual_regions (
                    owner_id, video_id, ingestion_version_id, frame_id,
                    visual_observation_id, region_index, region_type,
                    x, y, width, height, summary, crop_storage_backend,
                    crop_storage_key, crop_content_hash, model_name,
                    model_revision, prompt_version, input_hash
                )
                select owner_id, video_id, ingestion_version_id, frame_id,
                       id, 0, 'diagram', 0.1, 0.2, 0.3, 0.4, 'Old crop',
                       'filesystem', %s, %s, model_name, model_revision,
                       prompt_version, input_hash
                from video.visual_observations where id = %s
                """,
                (f"{self.owner}/crops/old.png", "b" * 64, first.id),
            )
            replacement = persist_visual_observation(
                database,
                job_id=claimed.id,
                worker_id="visual-worker",
                attempt_count=claimed.attempt_count,
                observation=self.observation(
                    frame_ids,
                    summary="A corrected computation graph description.",
                    input_hash="c" * 64,
                    cost_usd="0.002000",
                ),
            )
            rows = database.execute(
                """
                select id, summary, cost_usd from video.visual_observations
                where owner_id = %s
                """,
                (self.owner,),
            ).fetchall()
            regions = database.execute(
                """
                select count(*) as count from video.visual_regions
                where owner_id = %s
                """,
                (self.owner,),
            ).fetchone()["count"]

        self.assertTrue(replacement.replaced)
        self.assertNotEqual(replacement.id, first.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost_usd"], Decimal("0.002000"))
        self.assertEqual(regions, 0)

    def test_visual_types_and_group_scope_are_validated_before_insert(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            selected = self.store_frames(database, claimed)
            self.set_stage(database, claimed.id, Stage.VISUAL_ANALYSIS)
            frame_ids = tuple(frame.id for frame in selected.frames)
            with self.assertRaises(ValueError):
                persist_visual_observation(
                    database,
                    job_id=claimed.id,
                    worker_id="visual-worker",
                    attempt_count=claimed.attempt_count,
                    observation=self.observation(frame_ids, visual_types=("photo",)),
                )
            with self.assertRaises(VideoVisualStoreConflictError):
                persist_visual_observation(
                    database,
                    job_id=claimed.id,
                    worker_id="visual-worker",
                    attempt_count=claimed.attempt_count,
                    observation=self.observation((frame_ids[0], 999999999)),
                )
            count = database.execute(
                """
                select count(*) as count from video.visual_observations
                where owner_id = %s
                """,
                (self.owner,),
            ).fetchone()["count"]

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
