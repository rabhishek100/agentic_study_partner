"""Versioned video-ingestion workflow constraints and owner isolation."""

import unittest
from uuid import uuid4

from psycopg import errors as postgres_errors

from storage.database import connection, resolve_database_url


WORKFLOW_TABLES = (
    "transcript_sources",
    "transcript_segments",
    "resource_pages",
    "ingestion_versions",
    "ingestion_jobs",
    "ingestion_job_events",
    "ingestion_stage_checkpoints",
    "resource_suggestions",
)


class VideoWorkflowSchemaTests(unittest.TestCase):
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
                    (owner, f"{owner}@video-workflow.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_a, self.owner_b],),
            )

    @staticmethod
    def _as_owner(database, owner: str) -> None:
        database.execute("set local role authenticated")
        database.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub": "{owner}", "role": "authenticated"}}',),
        )

    @staticmethod
    def _insert_video_and_source(database, owner: str) -> tuple[str, str]:
        video_id = str(
            database.execute(
                """
                insert into video.videos (owner_id, title, source_kind)
                values (%s, 'Lecture', 'youtube') returning id
                """,
                (owner,),
            ).fetchone()["id"]
        )
        source_id = str(
            database.execute(
                """
                insert into video.video_sources (
                    owner_id, video_id, source_kind, status,
                    source_url, youtube_video_id, storage_backend, storage_key,
                    content_hash, size_bytes, media_type, acquired_at
                ) values (
                    %s, %s, 'youtube', 'ready',
                    'https://www.youtube.com/watch?v=abcdefghijk',
                    'abcdefghijk', 'filesystem', %s,
                    %s, 4096, 'video/mp4', now()
                ) returning id
                """,
                (
                    owner,
                    video_id,
                    f"{owner}/sha256/aa/{'a' * 64}.mp4",
                    "a" * 64,
                ),
            ).fetchone()["id"]
        )
        return video_id, source_id

    @staticmethod
    def _insert_version(
        database,
        owner: str,
        video_id: str,
        source_id: str,
        *,
        number: int = 1,
        status: str = "building",
    ) -> str:
        terminal = status in {"ready", "degraded", "failed", "cancelled"}
        published = status in {"ready", "degraded"}
        return str(
            database.execute(
                """
                insert into video.ingestion_versions (
                    owner_id, video_id, video_source_id, version_number,
                    status, config_json, config_hash, quality_gates_json,
                    completed_at, published_at
                ) values (
                    %s, %s, %s, %s, %s, '{}'::jsonb, %s,
                    case when %s then '{"passed": true}'::jsonb
                         else '{}'::jsonb end,
                    case when %s then now() else null end,
                    case when %s then now() else null end
                ) returning id
                """,
                (
                    owner,
                    video_id,
                    source_id,
                    number,
                    status,
                    "c" * 64,
                    published,
                    terminal,
                    published,
                ),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_job(
        database,
        owner: str,
        video_id: str,
        version_id: str,
        *,
        status: str = "queued",
    ) -> str:
        completed = status in {"ready", "failed", "cancelled"}
        return str(
            database.execute(
                """
                insert into video.ingestion_jobs (
                    owner_id, video_id, target_version_id, idempotency_key,
                    status, stage, completed_at
                ) values (
                    %s, %s, %s, %s, %s, 'acquire_source',
                    case when %s then now() else null end
                ) returning id
                """,
                (owner, video_id, version_id, uuid4(), status, completed),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_ready_pdf(database, owner: str) -> str:
        content_hash = uuid4().hex + uuid4().hex
        return str(
            database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title,
                    source_url, storage_backend, storage_key, content_hash,
                    size_bytes, media_type, page_count
                ) values (
                    %s, 'pdf', 'url', 'ready', 'Lecture slides',
                    'https://example.test/slides.pdf', 'filesystem', %s,
                    %s, 2048, 'application/pdf', 12
                ) returning id
                """,
                (
                    owner,
                    f"{owner}/sha256/{content_hash[:2]}/{content_hash}.pdf",
                    content_hash,
                ),
            ).fetchone()["id"]
        )

    def test_workflow_tables_have_read_only_owner_rls(self) -> None:
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
                where n.nspname = 'video'
                  and c.relname = any(%s)
                order by c.relname
                """,
                (list(WORKFLOW_TABLES),),
            ).fetchall()
            privileges = database.execute(
                """
                select table_name,
                       array_agg(privilege_type::text order by privilege_type)
                           as privileges
                from information_schema.role_table_grants
                where table_schema = 'video'
                  and grantee = 'authenticated'
                  and table_name = any(%s)
                group by table_name
                """,
                (list(WORKFLOW_TABLES),),
            ).fetchall()

        self.assertEqual(
            [row["table_name"] for row in rows], sorted(WORKFLOW_TABLES)
        )
        for row in rows:
            self.assertTrue(row["relrowsecurity"], row["table_name"])
            self.assertTrue(row["owner_not_null"], row["table_name"])
            self.assertEqual(row["cmd"], "SELECT")
            self.assertEqual(row["roles"], ["authenticated"])
        self.assertEqual(
            {row["table_name"]: row["privileges"] for row in privileges},
            {table: ["SELECT"] for table in WORKFLOW_TABLES},
        )

    def test_timestamped_transcript_round_trips_with_source_provenance(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, source_id = self._insert_video_and_source(
                database, self.owner_a
            )
            transcript_id = database.execute(
                """
                insert into video.transcript_sources (
                    owner_id, video_id, video_source_id, source_kind, language,
                    provider, storage_backend, storage_key, content_hash,
                    timing_kind, coverage_ratio, detected_speech
                ) values (
                    %s, %s, %s, 'youtube_caption', 'en', 'youtube',
                    'filesystem', %s, %s, 'cue', 0.98, true
                ) returning id
                """,
                (
                    self.owner_a,
                    video_id,
                    source_id,
                    f"{self.owner_a}/sha256/tt/transcript.vtt",
                    "d" * 64,
                ),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.transcript_segments (
                    owner_id, video_id, transcript_source_id, cue_index,
                    start_ms, end_ms, text, confidence
                ) values (%s, %s, %s, 0, 1500, 4200, %s, 0.99)
                """,
                (self.owner_a, video_id, transcript_id, "Gradient descent"),
            )
            row = database.execute(
                """
                select t.source_kind, s.start_ms, s.end_ms, s.text
                from video.transcript_sources as t
                join video.transcript_segments as s
                  on s.transcript_source_id = t.id
                 and s.owner_id = t.owner_id
                where t.owner_id = %s and t.video_id = %s
                """,
                (self.owner_a, video_id),
            ).fetchone()

        self.assertEqual(
            dict(row),
            {
                "source_kind": "youtube_caption",
                "start_ms": 1500,
                "end_ms": 4200,
                "text": "Gradient descent",
            },
        )

    def test_resource_pages_only_accept_pdf_resources_and_valid_renders(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            pdf_id = self._insert_ready_pdf(database, self.owner_a)
            page = database.execute(
                """
                insert into video.resource_pages (
                    owner_id, resource_id, page_number, text_content,
                    content_hash, parser_version, parser_config_hash,
                    render_storage_backend, render_storage_key,
                    render_content_hash
                ) values (
                    %s, %s, 1, 'Backpropagation diagram', %s, 'pymupdf-v1',
                    %s, 'filesystem', %s, %s
                ) returning page_number, text_content
                """,
                (
                    self.owner_a,
                    pdf_id,
                    "e" * 64,
                    "f" * 64,
                    f"{self.owner_a}/resources/{pdf_id}/page-1.webp",
                    "1" * 64,
                ),
            ).fetchone()
            link_id = database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title, source_url
                ) values (
                    %s, 'external_link', 'url', 'ready', 'Notes',
                    'https://example.test/notes'
                ) returning id
                """,
                (self.owner_a,),
            ).fetchone()["id"]
            with self.assertRaises(postgres_errors.ForeignKeyViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.resource_pages (
                            owner_id, resource_id, page_number, content_hash,
                            parser_version, parser_config_hash
                        ) values (%s, %s, 1, %s, 'test', %s)
                        """,
                        (self.owner_a, link_id, "2" * 64, "3" * 64),
                    )

        self.assertEqual(
            dict(page),
            {"page_number": 1, "text_content": "Backpropagation diagram"},
        )

    def test_only_a_same_owner_same_video_version_can_be_published(self) -> None:
        with connection(self.database_url) as database:
            video_a, source_a = self._insert_video_and_source(
                database, self.owner_a
            )
            version_a = self._insert_version(
                database,
                self.owner_a,
                video_a,
                source_a,
                status="ready",
            )
            database.execute(
                """
                update video.videos
                set readiness_status = 'ready', ready_at = now(),
                    current_ingestion_version_id = %s
                where id = %s and owner_id = %s
                """,
                (version_a, video_a, self.owner_a),
            )

            video_b, source_b = self._insert_video_and_source(
                database, self.owner_b
            )
            version_b = self._insert_version(
                database,
                self.owner_b,
                video_b,
                source_b,
                status="ready",
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        update video.videos
                        set current_ingestion_version_id = %s
                        where id = %s and owner_id = %s
                        """,
                        (version_b, video_a, self.owner_a),
                    )

            failed_version = self._insert_version(
                database,
                self.owner_a,
                video_a,
                source_a,
                number=2,
                status="failed",
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        update video.videos
                        set current_ingestion_version_id = %s
                        where id = %s and owner_id = %s
                        """,
                        (failed_version, video_a, self.owner_a),
                    )

            current = database.execute(
                """
                select current_ingestion_version_id
                from video.videos where id = %s and owner_id = %s
                """,
                (video_a, self.owner_a),
            ).fetchone()["current_ingestion_version_id"]

        self.assertEqual(str(current), version_a)

    def test_versions_and_jobs_cannot_spend_past_their_hard_caps(self) -> None:
        with connection(self.database_url) as database:
            video_id, source_id = self._insert_video_and_source(
                database, self.owner_a
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.ingestion_versions (
                            owner_id, video_id, video_source_id, version_number,
                            config_json, config_hash, cost_cap_usd,
                            actual_cost_usd
                        ) values (%s, %s, %s, 1, '{}'::jsonb, %s, 0.50, 0.51)
                        """,
                        (self.owner_a, video_id, source_id, "a" * 64),
                    )
            version_id = self._insert_version(
                database, self.owner_a, video_id, source_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.ingestion_jobs (
                            owner_id, video_id, target_version_id,
                            idempotency_key, status, cost_cap_usd,
                            actual_cost_usd
                        ) values (%s, %s, %s, %s, 'queued', 0.50, 0.500001)
                        """,
                        (self.owner_a, video_id, version_id, uuid4()),
                    )

    def test_one_active_job_and_dependency_keyed_checkpoint_reuse(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, source_id = self._insert_video_and_source(
                database, self.owner_a
            )
            version_one = self._insert_version(
                database, self.owner_a, video_id, source_id
            )
            self._insert_job(
                database, self.owner_a, video_id, version_one, status="queued"
            )
            with self.assertRaises(postgres_errors.UniqueViolation):
                with database.transaction():
                    self._insert_job(
                        database,
                        self.owner_a,
                        video_id,
                        version_one,
                        status="retry_scheduled",
                    )

            first_checkpoint = database.execute(
                """
                insert into video.ingestion_stage_checkpoints (
                    owner_id, video_id, ingestion_version_id, stage, status,
                    dependency_hash, started_at, completed_at
                ) values (
                    %s, %s, %s, 'transcript', 'complete', %s, now(), now()
                ) returning id
                """,
                (self.owner_a, video_id, version_one, "4" * 64),
            ).fetchone()["id"]
            version_two = self._insert_version(
                database,
                self.owner_a,
                video_id,
                source_id,
                number=2,
            )
            reused = database.execute(
                """
                insert into video.ingestion_stage_checkpoints (
                    owner_id, video_id, ingestion_version_id, stage, status,
                    dependency_hash, reused_from_checkpoint_id,
                    started_at, completed_at
                ) values (
                    %s, %s, %s, 'transcript', 'complete', %s, %s, now(), now()
                ) returning id
                """,
                (
                    self.owner_a,
                    video_id,
                    version_two,
                    "4" * 64,
                    first_checkpoint,
                ),
            ).fetchone()["id"]
            version_three = self._insert_version(
                database,
                self.owner_a,
                video_id,
                source_id,
                number=3,
            )
            with self.assertRaises(postgres_errors.ForeignKeyViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.ingestion_stage_checkpoints (
                            owner_id, video_id, ingestion_version_id, stage,
                            status, dependency_hash, reused_from_checkpoint_id,
                            started_at, completed_at
                        ) values (
                            %s, %s, %s, 'transcript', 'complete', %s, %s,
                            now(), now()
                        )
                        """,
                        (
                            self.owner_a,
                            video_id,
                            version_three,
                            "9" * 64,
                            first_checkpoint,
                        ),
                    )
            database.execute(
                "delete from video.ingestion_stage_checkpoints where id = %s",
                (first_checkpoint,),
            )
            source = database.execute(
                """
                select reused_from_checkpoint_id
                from video.ingestion_stage_checkpoints where id = %s
                """,
                (reused,),
            ).fetchone()["reused_from_checkpoint_id"]

        self.assertIsNone(source)

    def test_resource_suggestions_require_explicit_resolution(self) -> None:
        with connection(self.database_url) as database:
            video_id, source_id = self._insert_video_and_source(
                database, self.owner_a
            )
            version_id = self._insert_version(
                database, self.owner_a, video_id, source_id
            )
            job_id = self._insert_job(
                database, self.owner_a, video_id, version_id
            )
            suggestion_id = database.execute(
                """
                insert into video.resource_suggestions (
                    owner_id, video_id, job_id, suggested_kind,
                    url, normalized_url, title
                ) values (
                    %s, %s, %s, 'pdf', %s, %s, 'Lecture slides'
                ) returning id
                """,
                (
                    self.owner_a,
                    video_id,
                    job_id,
                    "https://example.test/slides.pdf?utm_source=youtube",
                    "https://example.test/slides.pdf",
                ),
            ).fetchone()["id"]
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        update video.resource_suggestions
                        set status = 'confirmed', resolved_at = now()
                        where id = %s
                        """,
                        (suggestion_id,),
                    )
            database.execute(
                """
                update video.resource_suggestions
                set status = 'dismissed', resolved_at = now()
                where id = %s
                """,
                (suggestion_id,),
            )
            status = database.execute(
                "select status from video.resource_suggestions where id = %s",
                (suggestion_id,),
            ).fetchone()["status"]

        self.assertEqual(status, "dismissed")

    def test_authenticated_workflow_reads_are_scoped_and_writes_denied(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_a, source_a = self._insert_video_and_source(
                database, self.owner_a
            )
            version_a = self._insert_version(
                database, self.owner_a, video_a, source_a
            )
            video_b, source_b = self._insert_video_and_source(
                database, self.owner_b
            )
            self._insert_version(database, self.owner_b, video_b, source_b)

            with database.transaction():
                self._as_owner(database, self.owner_a)
                visible = database.execute(
                    "select id from video.ingestion_versions"
                ).fetchall()
            self.assertEqual([str(row["id"]) for row in visible], [version_a])

            with self.assertRaises(postgres_errors.InsufficientPrivilege):
                with database.transaction():
                    self._as_owner(database, self.owner_a)
                    database.execute(
                        """
                        update video.ingestion_versions
                        set quality_gates_json = '{"forged": true}'::jsonb
                        where id = %s
                        """,
                        (version_a,),
                    )

    def test_deleting_an_owner_cascades_workflow_records(self) -> None:
        with connection(self.database_url) as database:
            video_id, source_id = self._insert_video_and_source(
                database, self.owner_a
            )
            transcript_id = database.execute(
                """
                insert into video.transcript_sources (
                    owner_id, video_id, video_source_id, source_kind, language,
                    provider, storage_backend, storage_key, content_hash,
                    timing_kind
                ) values (
                    %s, %s, %s, 'youtube_caption', 'en', 'youtube',
                    'filesystem', %s, %s, 'cue'
                ) returning id
                """,
                (
                    self.owner_a,
                    video_id,
                    source_id,
                    f"{self.owner_a}/sha256/transcript.vtt",
                    "5" * 64,
                ),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.transcript_segments (
                    owner_id, video_id, transcript_source_id, cue_index,
                    start_ms, end_ms, text
                ) values (%s, %s, %s, 0, 0, 1000, 'Opening')
                """,
                (self.owner_a, video_id, transcript_id),
            )
            pdf_id = self._insert_ready_pdf(database, self.owner_a)
            database.execute(
                """
                insert into video.resource_pages (
                    owner_id, resource_id, page_number, content_hash,
                    parser_version, parser_config_hash
                ) values (%s, %s, 1, %s, 'test', %s)
                """,
                (self.owner_a, pdf_id, "6" * 64, "7" * 64),
            )
            version_id = self._insert_version(
                database, self.owner_a, video_id, source_id
            )
            job_id = self._insert_job(
                database, self.owner_a, video_id, version_id
            )
            database.execute(
                """
                insert into video.ingestion_job_events (
                    owner_id, job_id, event_type
                ) values (%s, %s, 'queued')
                """,
                (self.owner_a, job_id),
            )
            database.execute(
                """
                insert into video.ingestion_stage_checkpoints (
                    owner_id, video_id, ingestion_version_id, stage, status,
                    dependency_hash
                ) values (%s, %s, %s, 'transcript', 'pending', %s)
                """,
                (self.owner_a, video_id, version_id, "8" * 64),
            )
            database.execute(
                """
                insert into video.resource_suggestions (
                    owner_id, video_id, job_id, suggested_kind,
                    url, normalized_url
                ) values (%s, %s, %s, 'pdf', %s, %s)
                """,
                (
                    self.owner_a,
                    video_id,
                    job_id,
                    "https://example.test/slides.pdf",
                    "https://example.test/slides.pdf",
                ),
            )
            database.execute(
                "delete from auth.users where id = %s", (self.owner_a,)
            )
            counts = {
                table: database.execute(
                    f"select count(*) as count from video.{table} "
                    "where owner_id = %s",
                    (self.owner_a,),
                ).fetchone()["count"]
                for table in WORKFLOW_TABLES
            }

        self.assertEqual(counts, {table: 0 for table in WORKFLOW_TABLES})


if __name__ == "__main__":
    unittest.main()
