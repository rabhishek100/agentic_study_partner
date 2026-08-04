"""Evidence vectors rebuild deterministically and make retrieval hybrid."""

from decimal import Decimal
import unittest
from uuid import uuid4

from psycopg.types.json import Jsonb

from storage.database import connection, resolve_database_url
from video.embeddings import (
    EmbeddingResult,
    RegionImage,
    rebuild_evidence_embeddings,
)
from video.errors import VideoBudgetExceeded
from video.evidence_store import evaluate_quality_gates, rebuild_evidence
from video.jobs import claim_next_job
from video.repository import create_youtube_video
from video.retrieval import retrieve_video_evidence
from video.transcript_store import persist_transcript
from video.transcripts import parse_webvtt


TRANSCRIPT_VECTOR = (1.0, 0.0, 0.0, 0.0)
VISUAL_VECTOR = (0.0, 1.0, 0.0, 0.0)
OTHER_VECTOR = (0.0, 0.0, 1.0, 0.0)
REGION_VECTOR = (0.0, 1.0)
REGION_OTHER_VECTOR = (1.0, 0.0)


class FakeTextEmbedder:
    """Deterministic keyword-routed vectors with provider-reported cost."""

    model_name = "fake/text-embedding"
    model_revision = "hosted"
    dimension = 4

    def __init__(self, *, cost: str = "0.000100") -> None:
        self.cost = Decimal(cost)
        self.batches: list[tuple[str, ...]] = []

    def _vector(self, text: str) -> tuple[float, ...]:
        lowered = text.lower()
        if "attention" in lowered or "diagram" in lowered:
            return VISUAL_VECTOR
        if "spoken transcript" in lowered or "said" in lowered:
            return TRANSCRIPT_VECTOR
        return OTHER_VECTOR

    def embed_documents(self, texts) -> EmbeddingResult:
        self.batches.append(tuple(texts))
        return EmbeddingResult(
            vectors=tuple(self._vector(text) for text in texts),
            model="fake/text-embedding-2026",
            cost_usd=self.cost * len(texts),
        )

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_documents([text])


class FakeRegionEmbedder:
    """Diagram crops and their questions share one small vector space."""

    model_name = "fake/image-embedding"
    model_revision = "hosted"
    dimension = 2

    def __init__(self, *, cost: str = "0.000200") -> None:
        self.cost = Decimal(cost)
        self.regions: list[RegionImage] = []
        self.queries: list[str] = []

    def embed_regions(self, regions) -> EmbeddingResult:
        self.regions.extend(regions)
        return EmbeddingResult(
            vectors=tuple(REGION_VECTOR for _ in regions),
            model="fake/image-embedding-2026",
            cost_usd=self.cost * len(regions),
        )

    def embed_query(self, text: str) -> EmbeddingResult:
        self.queries.append(text)
        vector = REGION_VECTOR if "drawing" in text.lower() else REGION_OTHER_VECTOR
        return EmbeddingResult(
            vectors=(vector,),
            model="fake/image-embedding-2026",
            cost_usd=self.cost,
        )


class VideoEmbeddingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-embeddings.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def _prepare(self, database):
        """Build one indexed version parked at the embedding stage."""

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
                "WEBVTT\n\n00:00.000 --> 00:10.000\nthe lecturer said hello\n"
            ),
        )
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
            observation_id = database.execute(
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
                ) returning id
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
            ).fetchone()["id"]
            if index == 0:
                self.region_id = database.execute(
                    """
                    insert into video.visual_regions (
                        owner_id, video_id, ingestion_version_id, frame_id,
                        visual_observation_id, region_index, region_type,
                        x, y, width, height, summary, crop_storage_backend,
                        crop_storage_key, crop_content_hash, model_name,
                        model_revision, prompt_version, input_hash, confidence
                    ) values (
                        %s, %s, %s, %s, %s, 0, 'diagram',
                        0.1, 0.1, 0.5, 0.5, %s, 'filesystem', %s, %s,
                        'openai/gpt-5.6-luna', 'openai/gpt-5.6-luna',
                        'video-visual-v1', %s, 0.9
                    ) returning id
                    """,
                    (
                        self.owner,
                        created.video_id,
                        created.version_id,
                        frame_id,
                        observation_id,
                        "Hand drawn attention schematic",
                        f"{self.owner}/regions/{frame_id}-0.jpg",
                        f"{index + 9:064x}",
                        f"{index + 11:064x}",
                    ),
                ).fetchone()["id"]
                self.frame_id = frame_id

        claimed = claim_next_job(database, worker_id="video-worker")
        database.execute(
            "update video.ingestion_jobs set stage = 'indexing' where id = %s",
            (created.job_id,),
        )
        rebuild_evidence(
            database,
            job_id=created.job_id,
            worker_id="video-worker",
            attempt_count=claimed.attempt_count,
            transcript_source_id=transcript.id,
        )
        database.execute(
            "update video.ingestion_jobs set stage = 'embeddings' where id = %s",
            (created.job_id,),
        )
        return created, transcript, claimed.attempt_count

    def _publish(self, database, created) -> None:
        database.execute(
            """
            update video.ingestion_versions
            set status = 'ready', completed_at = now(), published_at = now(),
                quality_gates_json = '{"readiness":"ready"}'::jsonb
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

    def test_builds_text_and_region_vectors_then_reuses_them(self) -> None:
        text_embedder = FakeTextEmbedder()
        image_embedder = FakeRegionEmbedder()
        loaded: list[str] = []

        def load_region_image(owner_id, storage_key):
            self.assertEqual(owner_id, self.owner)
            loaded.append(storage_key)
            return b"crop-bytes"

        with connection(self.database_url) as database:
            created, transcript, attempt = self._prepare(database)
            first = rebuild_evidence_embeddings(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=attempt,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
                load_region_image=load_region_image,
                remaining_budget_usd=Decimal("0.50"),
            )
            stored = database.execute(
                """
                select embedding_kind, evidence_id, visual_region_id,
                       evidence_frame_id, model_name, dimension,
                       document_format_version
                from video.evidence_embeddings
                where ingestion_version_id = %s
                order by embedding_kind, evidence_id
                """,
                (created.version_id,),
            ).fetchall()
            second = rebuild_evidence_embeddings(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=attempt,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
                load_region_image=load_region_image,
                remaining_budget_usd=Decimal("0.50"),
            )
            database.execute(
                "update video.ingestion_jobs set stage = 'quality_gates' where id = %s",
                (created.job_id,),
            )
            quality = evaluate_quality_gates(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=attempt,
                transcript_source_id=transcript.id,
            )

        self.assertEqual(first.text_count, 3)
        self.assertEqual(first.image_count, 1)
        self.assertEqual(first.reused_count, 0)
        self.assertEqual(first.cost_usd, Decimal("0.000500"))
        self.assertEqual(first.text_model, "fake/text-embedding-2026")
        self.assertEqual(first.image_model, "fake/image-embedding-2026")
        self.assertEqual(loaded, [f"{self.owner}/regions/{self.frame_id}-0.jpg"])
        self.assertEqual(len(stored), 4)
        image_rows = [row for row in stored if row["embedding_kind"] == "image"]
        self.assertEqual(len(image_rows), 1)
        self.assertEqual(image_rows[0]["visual_region_id"], self.region_id)
        self.assertEqual(image_rows[0]["evidence_frame_id"], self.frame_id)
        self.assertEqual(image_rows[0]["dimension"], 2)
        self.assertEqual(
            {row["document_format_version"] for row in stored},
            {"video-evidence-text-v1", "video-evidence-region-v1"},
        )
        # A rerun with the same models and inputs must not pay again.
        self.assertEqual(second.text_count, 0)
        self.assertEqual(second.image_count, 0)
        self.assertEqual(second.reused_count, 4)
        self.assertEqual(second.cost_usd, Decimal(0))
        self.assertEqual(len(text_embedder.batches), 1)
        self.assertEqual(len(image_embedder.regions), 1)
        self.assertTrue(quality.metrics["gates"]["semantic_index_complete"])
        self.assertEqual(quality.metrics["text_embedding_count"], 3)
        self.assertEqual(quality.metrics["image_embedding_count"], 1)

    def test_changed_evidence_text_replaces_only_its_own_vector(self) -> None:
        text_embedder = FakeTextEmbedder()
        image_embedder = FakeRegionEmbedder()
        with connection(self.database_url) as database:
            created, _, attempt = self._prepare(database)
            rebuild_evidence_embeddings(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=attempt,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
                load_region_image=lambda owner_id, key: b"crop-bytes",
                remaining_budget_usd=Decimal("0.50"),
            )
            changed = database.execute(
                """
                update video.evidence_units
                set retrieval_text = 'the lecturer said something different'
                where ingestion_version_id = %s and modality = 'transcript'
                returning id
                """,
                (created.version_id,),
            ).fetchone()["id"]
            rebuilt = rebuild_evidence_embeddings(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=attempt,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
                load_region_image=lambda owner_id, key: b"crop-bytes",
                remaining_budget_usd=Decimal("0.50"),
            )
            remaining = database.execute(
                """
                select count(*) as count from video.evidence_embeddings
                where ingestion_version_id = %s
                """,
                (created.version_id,),
            ).fetchone()["count"]

        self.assertEqual(rebuilt.text_count, 1)
        self.assertEqual(rebuilt.image_count, 0)
        self.assertEqual(rebuilt.deleted_count, 1)
        self.assertEqual(rebuilt.reused_count, 3)
        self.assertEqual(remaining, 4)
        self.assertEqual(text_embedder.batches[-1][0].count("said something"), 1)
        self.assertEqual(len(image_embedder.regions), 1)

    def test_stops_before_a_request_the_job_cannot_afford(self) -> None:
        with connection(self.database_url) as database:
            created, _, attempt = self._prepare(database)
            with self.assertRaises(VideoBudgetExceeded):
                rebuild_evidence_embeddings(
                    database,
                    job_id=created.job_id,
                    worker_id="video-worker",
                    attempt_count=attempt,
                    text_embedder=FakeTextEmbedder(),
                    image_embedder=FakeRegionEmbedder(),
                    load_region_image=lambda owner_id, key: b"crop-bytes",
                    remaining_budget_usd=Decimal("0.000001"),
                )
            written = database.execute(
                """
                select count(*) as count from video.evidence_embeddings
                where ingestion_version_id = %s
                """,
                (created.version_id,),
            ).fetchone()["count"]

        self.assertEqual(written, 0)

    def test_hybrid_retrieval_recovers_evidence_lexical_search_misses(self) -> None:
        text_embedder = FakeTextEmbedder()
        image_embedder = FakeRegionEmbedder()
        with connection(self.database_url) as database:
            created, _, attempt = self._prepare(database)
            rebuild_evidence_embeddings(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=attempt,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
                load_region_image=lambda owner_id, key: b"crop-bytes",
                remaining_budget_usd=Decimal("0.50"),
            )
            self._publish(database, created)
            _, lexical = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="self-attention",
                limit=4,
            )
            _, hybrid = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="self-attention",
                limit=4,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
            )
            _, drawing = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="the drawing on the board",
                limit=4,
                image_embedder=image_embedder,
            )

        # "self-attention" appears in no evidence text, so lexical search alone
        # returns nothing and the video looks empty to answer generation.
        self.assertEqual(lexical, ())
        self.assertTrue(hybrid)
        self.assertTrue(any(item.is_visual for item in hybrid))
        self.assertIn(
            "text_vector", {item.retrieval_method for item in hybrid}
        )
        self.assertEqual([item.rank for item in hybrid], list(range(1, len(hybrid) + 1)))
        self.assertTrue(
            any(
                item.retrieval_method == "image_vector" and item.is_visual
                for item in drawing
            )
        )
        self.assertEqual(image_embedder.queries[-1], "the drawing on the board")


if __name__ == "__main__":
    unittest.main()
