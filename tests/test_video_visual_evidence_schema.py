"""First-class visual evidence, typed locators, and embedding constraints."""

import unittest
from uuid import uuid4

from psycopg import errors as postgres_errors

from storage.database import connection, resolve_database_url
from tests import test_video_workflow_schema as workflow_schema


VISUAL_TABLES = (
    "frames",
    "visual_observations",
    "visual_regions",
    "visual_events",
    "evidence_units",
    "evidence_embeddings",
)


class VideoVisualEvidenceSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_a = str(uuid4())
        self.owner_b = str(uuid4())
        with connection(self.database_url) as database:
            for owner in (self.owner_a, self.owner_b):
                database.execute(
                    """
                    insert into auth.users (id, email, raw_user_meta_data)
                    values (%s, %s, '{}'::jsonb)
                    """,
                    (owner, f"{owner}@video-visual.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_a, self.owner_b],),
            )

    def _scope(self, database, owner: str) -> tuple[str, str, str]:
        fixtures = workflow_schema.VideoWorkflowSchemaTests
        video_id, source_id = fixtures._insert_video_and_source(database, owner)
        version_id = fixtures._insert_version(
            database, owner, video_id, source_id
        )
        return video_id, source_id, version_id

    @staticmethod
    def _insert_frame(
        database,
        owner: str,
        video_id: str,
        version_id: str,
        *,
        frame_index: int = 0,
        timestamp_ms: int = 1000,
    ) -> int:
        content_hash = f"{frame_index + 1:064x}"
        return int(
            database.execute(
                """
                insert into video.frames (
                    owner_id, video_id, ingestion_version_id, frame_index,
                    timestamp_ms, selection_reasons, full_storage_backend,
                    full_storage_key, full_content_hash,
                    preview_storage_backend, preview_storage_key,
                    preview_content_hash, perceptual_hash, width, height,
                    ocr_text, ocr_confidence, ocr_engine, ocr_version
                ) values (
                    %s, %s, %s, %s, %s, array['scene_change'], 'filesystem',
                    %s, %s, 'filesystem', %s, %s, %s, 1920, 1080,
                    'Backpropagation through the computation graph', 0.96,
                    'tesseract', '5.5'
                ) returning id
                """,
                (
                    owner,
                    video_id,
                    version_id,
                    frame_index,
                    timestamp_ms,
                    f"{owner}/{video_id}/{version_id}/frames/{content_hash}.png",
                    content_hash,
                    f"{owner}/{video_id}/{version_id}/previews/{content_hash}.webp",
                    content_hash,
                    f"{frame_index + 1:016x}",
                ),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_observation(
        database,
        owner: str,
        video_id: str,
        version_id: str,
        frame_id: int,
    ) -> int:
        return int(
            database.execute(
                """
                insert into video.visual_observations (
                    owner_id, video_id, ingestion_version_id, frame_id, status,
                    visual_types, summary, visible_text, importance, confidence,
                    model_name, model_revision, prompt_version, input_hash
                ) values (
                    %s, %s, %s, %s, 'success', array['slide','diagram'],
                    'A neural network computation graph with gradient arrows.',
                    'loss gradient weights', 0.9, 0.95,
                    'google/gemini-2.5-flash', 'openrouter', 'visual-v1', %s
                ) returning id
                """,
                (owner, video_id, version_id, frame_id, "a" * 64),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_region(
        database,
        owner: str,
        video_id: str,
        version_id: str,
        frame_id: int,
        observation_id: int,
    ) -> int:
        return int(
            database.execute(
                """
                insert into video.visual_regions (
                    owner_id, video_id, ingestion_version_id, frame_id,
                    visual_observation_id, region_index, region_type,
                    x, y, width, height, summary, crop_storage_backend,
                    crop_storage_key, crop_content_hash, model_name,
                    model_revision, prompt_version, input_hash, confidence
                ) values (
                    %s, %s, %s, %s, %s, 0, 'diagram',
                    0.1, 0.1, 0.8, 0.7, 'Gradient-flow computation graph',
                    'filesystem', %s, %s, 'google/gemini-2.5-flash',
                    'openrouter', 'regions-v1', %s, 0.94
                ) returning id
                """,
                (
                    owner,
                    video_id,
                    version_id,
                    frame_id,
                    observation_id,
                    f"{owner}/{video_id}/{version_id}/regions/diagram.webp",
                    "b" * 64,
                    "c" * 64,
                ),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_frame_evidence(
        database,
        owner: str,
        video_id: str,
        version_id: str,
        frame_id: int,
        *,
        evidence_id: str = "d" * 64,
    ) -> str:
        database.execute(
            """
            insert into video.evidence_units (
                id, owner_id, video_id, ingestion_version_id, modality,
                frame_id, retrieval_text, start_ms, end_ms, content_hash
            ) values (
                %s, %s, %s, %s, 'visual_frame', %s,
                'Backpropagation computation graph and gradient arrows',
                1000, 1000, %s
            )
            """,
            (evidence_id, owner, video_id, version_id, frame_id, "e" * 64),
        )
        return evidence_id

    @staticmethod
    def _as_owner(database, owner: str) -> None:
        database.execute("set local role authenticated")
        database.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub": "{owner}", "role": "authenticated"}}',),
        )

    def test_visual_tables_have_read_only_owner_rls(self) -> None:
        with connection(self.database_url, readonly=True) as database:
            rows = database.execute(
                """
                select c.relname as table_name, c.relrowsecurity,
                       a.attnotnull as owner_not_null, p.cmd, p.roles
                from pg_class as c
                join pg_namespace as n on n.oid = c.relnamespace
                join pg_attribute as a
                  on a.attrelid = c.oid and a.attname = 'owner_id'
                join pg_policies as p
                  on p.schemaname = n.nspname and p.tablename = c.relname
                where n.nspname = 'video' and c.relname = any(%s)
                order by c.relname
                """,
                (list(VISUAL_TABLES),),
            ).fetchall()
            privileges = database.execute(
                """
                select table_name,
                       array_agg(privilege_type::text order by privilege_type)
                           as privileges
                from information_schema.role_table_grants
                where table_schema = 'video' and grantee = 'authenticated'
                  and table_name = any(%s)
                group by table_name
                """,
                (list(VISUAL_TABLES),),
            ).fetchall()

        self.assertEqual(
            [row["table_name"] for row in rows], sorted(VISUAL_TABLES)
        )
        for row in rows:
            self.assertTrue(row["relrowsecurity"], row["table_name"])
            self.assertTrue(row["owner_not_null"], row["table_name"])
            self.assertEqual(row["cmd"], "SELECT")
            self.assertEqual(row["roles"], ["authenticated"])
        self.assertEqual(
            {row["table_name"]: row["privileges"] for row in privileges},
            {table: ["SELECT"] for table in VISUAL_TABLES},
        )

    def test_extracted_diagram_is_first_class_image_evidence(self) -> None:
        with connection(self.database_url) as database:
            video_id, _, version_id = self._scope(database, self.owner_a)
            frame_id = self._insert_frame(
                database, self.owner_a, video_id, version_id
            )
            observation_id = self._insert_observation(
                database, self.owner_a, video_id, version_id, frame_id
            )
            region_id = self._insert_region(
                database,
                self.owner_a,
                video_id,
                version_id,
                frame_id,
                observation_id,
            )
            evidence_id = self._insert_frame_evidence(
                database, self.owner_a, video_id, version_id, frame_id
            )
            database.execute(
                """
                insert into video.evidence_embeddings (
                    owner_id, video_id, ingestion_version_id, evidence_id,
                    embedding_kind, visual_region_id, evidence_frame_id,
                    model_name, model_revision, dimension,
                    document_format_version, embedding_input_hash, embedding
                ) values (
                    %s, %s, %s, %s, 'image', %s, %s,
                    'openai/clip-vit-large-patch14', 'openrouter', 3,
                    'diagram-region-v1', %s, %s::extensions.vector
                )
                """,
                (
                    self.owner_a,
                    video_id,
                    version_id,
                    evidence_id,
                    region_id,
                    frame_id,
                    "f" * 64,
                    "[0.1,0.2,0.3]",
                ),
            )
            row = database.execute(
                """
                select f.timestamp_ms, o.visual_types, r.region_type,
                       e.modality, m.embedding_kind,
                       extensions.vector_dims(m.embedding) as dimensions
                from video.frames as f
                join video.visual_observations as o on o.frame_id = f.id
                join video.visual_regions as r on r.frame_id = f.id
                join video.evidence_units as e on e.frame_id = f.id
                join video.evidence_embeddings as m
                  on m.evidence_id = e.id and m.owner_id = e.owner_id
                where f.id = %s
                """,
                (frame_id,),
            ).fetchone()

        self.assertEqual(row["timestamp_ms"], 1000)
        self.assertEqual(row["visual_types"], ["slide", "diagram"])
        self.assertEqual(row["region_type"], "diagram")
        self.assertEqual(row["modality"], "visual_frame")
        self.assertEqual(row["embedding_kind"], "image")
        self.assertEqual(row["dimensions"], 3)

    def test_image_embeddings_require_a_matching_diagram_or_drawing_crop(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, _, version_id = self._scope(database, self.owner_a)
            frame_id = self._insert_frame(
                database, self.owner_a, video_id, version_id
            )
            evidence_id = self._insert_frame_evidence(
                database, self.owner_a, video_id, version_id, frame_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.evidence_embeddings (
                            owner_id, video_id, ingestion_version_id,
                            evidence_id, embedding_kind, model_name,
                            model_revision, dimension, document_format_version,
                            embedding_input_hash, embedding
                        ) values (
                            %s, %s, %s, %s, 'image', 'clip', 'v1', 2,
                            'image-v1', %s, %s::extensions.vector
                        )
                        """,
                        (
                            self.owner_a,
                            video_id,
                            version_id,
                            evidence_id,
                            "1" * 64,
                            "[0.1,0.2]",
                        ),
                    )

            observation_id = self._insert_observation(
                database, self.owner_a, video_id, version_id, frame_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.visual_regions (
                            owner_id, video_id, ingestion_version_id, frame_id,
                            visual_observation_id, region_index, region_type,
                            x, y, width, height, summary,
                            crop_storage_backend, crop_storage_key,
                            crop_content_hash, model_name, model_revision,
                            prompt_version, input_hash
                        ) values (
                            %s, %s, %s, %s, %s, 0, 'chart',
                            0, 0, 1, 1, 'Chart', 'filesystem', %s, %s,
                            'model', 'v1', 'regions-v1', %s
                        )
                        """,
                        (
                            self.owner_a,
                            video_id,
                            version_id,
                            frame_id,
                            observation_id,
                            f"{self.owner_a}/chart.webp",
                            "2" * 64,
                            "3" * 64,
                        ),
                    )

    def test_ocr_and_model_text_are_searchable_without_image_embedding(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, _, version_id = self._scope(database, self.owner_a)
            frame_id = self._insert_frame(
                database, self.owner_a, video_id, version_id
            )
            evidence_id = self._insert_frame_evidence(
                database, self.owner_a, video_id, version_id, frame_id
            )
            database.execute(
                """
                insert into video.evidence_embeddings (
                    owner_id, video_id, ingestion_version_id, evidence_id,
                    embedding_kind, model_name, model_revision, dimension,
                    document_format_version, embedding_input_hash, embedding
                ) values (
                    %s, %s, %s, %s, 'text', 'text-embedding-3-small', 'v1',
                    2, 'evidence-text-v1', %s, %s::extensions.vector
                )
                """,
                (
                    self.owner_a,
                    video_id,
                    version_id,
                    evidence_id,
                    "4" * 64,
                    "[0.3,0.4]",
                ),
            )
            found = database.execute(
                """
                select id from video.evidence_units
                where owner_id = %s
                  and search_vector @@ plainto_tsquery('simple', %s)
                """,
                (self.owner_a, "backpropagation gradient"),
            ).fetchall()
            embedding = database.execute(
                """
                select embedding_kind, visual_region_id
                from video.evidence_embeddings where evidence_id = %s
                """,
                (evidence_id,),
            ).fetchone()

        self.assertEqual([row["id"] for row in found], [evidence_id])
        self.assertEqual(
            dict(embedding), {"embedding_kind": "text", "visual_region_id": None}
        )

    def test_typed_evidence_requires_exactly_one_owner_safe_locator(self) -> None:
        with connection(self.database_url) as database:
            video_a, _, version_a = self._scope(database, self.owner_a)
            frame_a = self._insert_frame(
                database, self.owner_a, video_a, version_a
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.evidence_units (
                            id, owner_id, video_id, ingestion_version_id,
                            modality, frame_id, resource_page_id, retrieval_text,
                            start_ms, end_ms, content_hash
                        ) values (
                            %s, %s, %s, %s, 'visual_frame', %s, 999999,
                            'ambiguous', 1000, 1000, %s
                        )
                        """,
                        (
                            "5" * 64,
                            self.owner_a,
                            video_a,
                            version_a,
                            frame_a,
                            "6" * 64,
                        ),
                    )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.evidence_units (
                            id, owner_id, video_id, ingestion_version_id,
                            modality, frame_id, retrieval_text, start_ms,
                            end_ms, content_hash
                        ) values (
                            %s, %s, %s, %s, 'visual_frame', %s,
                            'wrong timestamp', 2000, 2000, %s
                        )
                        """,
                        (
                            "c" * 64,
                            self.owner_a,
                            video_a,
                            version_a,
                            frame_a,
                            "d" * 64,
                        ),
                    )

            video_b, _, version_b = self._scope(database, self.owner_b)
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    self._insert_frame_evidence(
                        database,
                        self.owner_b,
                        video_b,
                        version_b,
                        frame_a,
                        evidence_id="7" * 64,
                    )

    def test_visual_events_preserve_before_after_frames_and_time_range(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, _, version_id = self._scope(database, self.owner_a)
            start_frame = self._insert_frame(
                database,
                self.owner_a,
                video_id,
                version_id,
                frame_index=0,
                timestamp_ms=1000,
            )
            end_frame = self._insert_frame(
                database,
                self.owner_a,
                video_id,
                version_id,
                frame_index=1,
                timestamp_ms=3000,
            )
            event_id = database.execute(
                """
                insert into video.visual_events (
                    owner_id, video_id, ingestion_version_id,
                    start_frame_id, end_frame_id, event_type, start_ms, end_ms,
                    summary, model_name, model_revision, prompt_version,
                    input_hash
                ) values (
                    %s, %s, %s, %s, %s, 'annotation', 1000, 3000,
                    'The instructor adds gradient arrows to the graph.',
                    'google/gemini-2.5-flash', 'openrouter', 'events-v1', %s
                ) returning id
                """,
                (
                    self.owner_a,
                    video_id,
                    version_id,
                    start_frame,
                    end_frame,
                    "9" * 64,
                ),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.evidence_units (
                    id, owner_id, video_id, ingestion_version_id, modality,
                    visual_event_id, retrieval_text, start_ms, end_ms,
                    content_hash
                ) values (
                    %s, %s, %s, %s, 'visual_event', %s,
                    'Instructor annotates the computation graph with arrows',
                    1000, 3000, %s
                )
                """,
                (
                    "a" * 64,
                    self.owner_a,
                    video_id,
                    version_id,
                    event_id,
                    "b" * 64,
                ),
            )
            row = database.execute(
                """
                select e.event_type, e.start_frame_id, e.end_frame_id,
                       u.modality, u.start_ms, u.end_ms
                from video.visual_events as e
                join video.evidence_units as u on u.visual_event_id = e.id
                where e.id = %s
                """,
                (event_id,),
            ).fetchone()

        self.assertEqual(row["event_type"], "annotation")
        self.assertEqual(row["start_frame_id"], start_frame)
        self.assertEqual(row["end_frame_id"], end_frame)
        self.assertEqual(row["modality"], "visual_event")
        self.assertEqual((row["start_ms"], row["end_ms"]), (1000, 3000))

    def test_embedding_dimension_must_match_the_stored_vector(self) -> None:
        with connection(self.database_url) as database:
            video_id, _, version_id = self._scope(database, self.owner_a)
            frame_id = self._insert_frame(
                database, self.owner_a, video_id, version_id
            )
            evidence_id = self._insert_frame_evidence(
                database, self.owner_a, video_id, version_id, frame_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.evidence_embeddings (
                            owner_id, video_id, ingestion_version_id,
                            evidence_id, embedding_kind, model_name,
                            model_revision, dimension, document_format_version,
                            embedding_input_hash, embedding
                        ) values (
                            %s, %s, %s, %s, 'text', 'embed', 'v1', 3,
                            'text-v1', %s, %s::extensions.vector
                        )
                        """,
                        (
                            self.owner_a,
                            video_id,
                            version_id,
                            evidence_id,
                            "8" * 64,
                            "[0.1,0.2]",
                        ),
                    )

    def test_authenticated_visual_reads_are_scoped_and_writes_denied(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_a, _, version_a = self._scope(database, self.owner_a)
            frame_a = self._insert_frame(
                database, self.owner_a, video_a, version_a
            )
            video_b, _, version_b = self._scope(database, self.owner_b)
            self._insert_frame(database, self.owner_b, video_b, version_b)

            with database.transaction():
                self._as_owner(database, self.owner_a)
                visible = database.execute("select id from video.frames").fetchall()
            self.assertEqual([row["id"] for row in visible], [frame_a])

            with self.assertRaises(postgres_errors.InsufficientPrivilege):
                with database.transaction():
                    self._as_owner(database, self.owner_a)
                    database.execute(
                        "update video.frames set ocr_text = 'forged' where id = %s",
                        (frame_a,),
                    )

    def test_deleting_a_version_removes_derived_evidence_not_sources(self) -> None:
        with connection(self.database_url) as database:
            video_id, source_id, version_id = self._scope(database, self.owner_a)
            frame_id = self._insert_frame(
                database, self.owner_a, video_id, version_id
            )
            observation_id = self._insert_observation(
                database, self.owner_a, video_id, version_id, frame_id
            )
            self._insert_region(
                database,
                self.owner_a,
                video_id,
                version_id,
                frame_id,
                observation_id,
            )
            self._insert_frame_evidence(
                database, self.owner_a, video_id, version_id, frame_id
            )
            database.execute(
                "delete from video.ingestion_versions where id = %s",
                (version_id,),
            )
            derived_counts = {
                table: database.execute(
                    f"select count(*) as count from video.{table} "
                    "where owner_id = %s",
                    (self.owner_a,),
                ).fetchone()["count"]
                for table in VISUAL_TABLES
            }
            canonical = database.execute(
                """
                select
                    (select count(*) from video.videos
                     where id = %s and owner_id = %s) as videos,
                    (select count(*) from video.video_sources
                     where id = %s and owner_id = %s) as sources
                """,
                (video_id, self.owner_a, source_id, self.owner_a),
            ).fetchone()

        self.assertEqual(derived_counts, {table: 0 for table in VISUAL_TABLES})
        self.assertEqual(dict(canonical), {"videos": 1, "sources": 1})


if __name__ == "__main__":
    unittest.main()
