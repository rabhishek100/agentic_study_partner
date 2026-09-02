"""Video evidence stays typed, timestamped, lexical, and version scoped."""

import unittest
from uuid import uuid4

from psycopg.types.json import Jsonb

from storage.database import connection, resolve_database_url
from video.evidence_store import (
    evaluate_quality_gates,
    persist_quality_gates,
    rebuild_evidence,
)
from video.retrieval import retrieve_video_evidence
from video.repository import create_youtube_video
from video.transcript_store import persist_transcript
from video.transcripts import parse_webvtt


class VideoEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-evidence-store.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def test_rebuilds_typed_evidence_and_computes_explainable_gates(self) -> None:
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/abcdefghijk",
                title="Lecture",
            )
            database.execute(
                "update video.videos set duration_ms = 10000 where id = %s",
                (created.video_id,),
            )
            database.execute(
                """
                update video.video_sources
                set status = 'ready', storage_backend = 'filesystem',
                    storage_key = %s, content_hash = %s, size_bytes = 100,
                    media_type = 'video/mp4', acquired_at = now()
                where id = %s
                """,
                (
                    f"{self.owner}/canonical/videos/video.mp4",
                    "a" * 64,
                    created.source_id,
                ),
            )
            transcript = persist_transcript(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                video_source_id=created.source_id,
                source_kind="youtube_caption",
                language="en",
                provider="youtube",
                storage_backend="filesystem",
                storage_key=f"{self.owner}/canonical/transcripts/captions.vtt",
                content_hash="b" * 64,
                cues=parse_webvtt(
                    "WEBVTT\n\n00:00.000 --> 00:10.000\nattention mechanism\n"
                ),
            )
            frame_ids = []
            for index, timestamp in enumerate((0, 9_000)):
                frame_id = database.execute(
                    """
                    insert into video.frames (
                        owner_id, video_id, ingestion_version_id, frame_index,
                        timestamp_ms, selection_reasons, full_storage_backend,
                        full_storage_key, full_content_hash,
                        preview_storage_backend, preview_storage_key,
                        preview_content_hash, perceptual_hash, width, height,
                        ocr_text, ocr_confidence, ocr_engine, ocr_version
                    ) values (
                        %s, %s, %s, %s, %s, array['periodic_safeguard'],
                        'filesystem', %s, %s, 'filesystem', %s, %s,
                        %s, 1280, 720, %s, 0.95, 'tesseract', '5.5'
                    ) returning id
                    """,
                    (
                        self.owner,
                        created.video_id,
                        created.version_id,
                        index,
                        timestamp,
                        f"{self.owner}/frames/{index}.jpg",
                        f"{index + 1:064x}",
                        f"{self.owner}/previews/{index}.jpg",
                        f"{index + 3:064x}",
                        f"{index + 5:016x}",
                        "Attention diagram" if index == 0 else "Output layer",
                    ),
                ).fetchone()["id"]
                frame_ids.append(frame_id)
                if index == 0:
                    database.execute(
                        """
                        insert into video.visual_observations (
                            owner_id, video_id, ingestion_version_id, frame_id,
                            status, visual_types, summary, visible_text,
                            technical_details_json, importance, confidence,
                            model_name, model_revision, prompt_version, input_hash
                        ) values (
                            %s, %s, %s, %s, 'success', array['diagram'],
                            %s, %s, %s, 0.9, 0.95,
                            'openai/gpt-5.6-luna', 'openai/gpt-5.6-luna',
                            'video-visual-v1', %s
                        )
                        """,
                        (
                            self.owner,
                            created.video_id,
                            created.version_id,
                            frame_id,
                            "Query, key, and value vectors are connected",
                            "Scaled dot-product attention",
                            Jsonb({"items": ["Q", "K", "V"]}),
                            f"{index + 7:064x}",
                        ),
                    )

            # Course ingestion deliberately caps the frames sent to the model.
            # Quality is measured against that selected set, while all frames
            # remain captured for OCR and timestamp coverage.
            database.execute(
                """
                insert into video.ingestion_stage_checkpoints (
                    owner_id, video_id, ingestion_version_id, stage, status,
                    dependency_hash, output_manifest_json, completed_at
                ) values (%s, %s, %s, 'visual_analysis', 'complete', %s, %s, now())
                """,
                (
                    self.owner,
                    created.video_id,
                    created.version_id,
                    "f" * 64,
                    Jsonb({"frame_count": 1}),
                ),
            )

            claimed = database.execute(
                """
                update video.ingestion_jobs
                set status = 'running', stage = 'indexing',
                    attempt_count = attempt_count + 1,
                    lease_owner = 'video-worker',
                    lease_expires_at = now() + interval '5 minutes',
                    heartbeat_at = now()
                where id = %s and owner_id = %s and status = 'queued'
                returning attempt_count
                """,
                (created.job_id, self.owner),
            ).fetchone()
            self.assertIsNotNone(claimed)
            built = rebuild_evidence(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed["attempt_count"],
                transcript_source_id=transcript.id,
            )
            evidence = database.execute(
                """
                select modality, start_ms, end_ms, retrieval_text
                from video.evidence_units
                where ingestion_version_id = %s order by modality, start_ms
                """,
                (created.version_id,),
            ).fetchall()
            database.execute(
                "update video.ingestion_jobs set stage = 'quality_gates' where id = %s",
                (created.job_id,),
            )
            quality = evaluate_quality_gates(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed["attempt_count"],
                transcript_source_id=transcript.id,
            )
            persist_quality_gates(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=claimed["attempt_count"],
                result=quality,
            )
            stored_quality = database.execute(
                """
                select quality_gates_json from video.ingestion_versions
                where id = %s
                """,
                (created.version_id,),
            ).fetchone()["quality_gates_json"]
            database.execute(
                """
                update video.ingestion_versions
                set status = 'ready', completed_at = now(), published_at = now()
                where id = %s
                """,
                (created.version_id,),
            )
            database.execute(
                """
                update video.videos
                set readiness_status = 'ready',
                    current_ingestion_version_id = %s, ready_at = now()
                where id = %s
                """,
                (created.version_id, created.video_id),
            )
            retrieved_version, retrieved = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="mechanism",
                limit=3,
            )
            missing_version, missing = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="words that do not occur",
                limit=3,
            )
            _, visual_first = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="diagram",
                limit=3,
            )

        self.assertEqual(built.transcript_count, 1)
        self.assertEqual(built.visual_frame_count, 2)
        self.assertEqual(built.total_count, 3)
        self.assertEqual(
            [row["modality"] for row in evidence],
            ["transcript", "visual_frame", "visual_frame"],
        )
        self.assertEqual(evidence[1]["start_ms"], evidence[1]["end_ms"])
        self.assertIn("Attention diagram", evidence[1]["retrieval_text"])
        self.assertEqual(quality.metrics["frame_count"], 2)
        self.assertEqual(quality.metrics["visual_analysis_target_count"], 1)
        self.assertEqual(quality.metrics["visual_success_ratio"], 1.0)
        # Lexical evidence alone publishes as degraded: every other gate holds,
        # and only the missing semantic index keeps it from full readiness.
        self.assertEqual(quality.readiness, "degraded")
        self.assertEqual(
            [name for name, passed in quality.metrics["gates"].items() if not passed],
            ["semantic_index_complete"],
        )
        self.assertEqual(stored_quality["readiness"], "degraded")
        self.assertEqual(retrieved_version, created.version_id)
        self.assertEqual(missing_version, created.version_id)
        self.assertEqual(missing, ())
        self.assertEqual(retrieved[0].modality, "transcript")
        self.assertTrue(any(item.is_visual for item in retrieved))
        self.assertTrue(
            any(
                item.retrieval_method == "timeline_expansion" for item in retrieved
            )
        )
        self.assertEqual([item.rank for item in retrieved], list(range(1, len(retrieved) + 1)))
        self.assertTrue(any(item.is_visual for item in visual_first))
        self.assertTrue(
            any(item.modality == "transcript" for item in visual_first)
        )


if __name__ == "__main__":
    unittest.main()
