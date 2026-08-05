"""Video conversation persistence, version pinning, cost, and isolation."""

import unittest
from uuid import UUID, uuid4

from psycopg import errors as postgres_errors

from storage.database import connection, resolve_database_url
from tests import test_video_workflow_schema as workflow_schema


CONVERSATION_TABLES = ("conversations", "conversation_turns")


class VideoConversationSchemaTests(unittest.TestCase):
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
                    (owner, f"{owner}@video-conversation.test"),
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
    def _published_scope(database, owner: str) -> tuple[str, str]:
        fixtures = workflow_schema.VideoWorkflowSchemaTests
        video_id, source_id = fixtures._insert_video_and_source(database, owner)
        version_id = fixtures._insert_version(
            database,
            owner,
            video_id,
            source_id,
            status="ready",
        )
        database.execute(
            """
            update video.videos
            set readiness_status = 'ready', ready_at = now(),
                current_ingestion_version_id = %s
            where id = %s and owner_id = %s
            """,
            (version_id, video_id, owner),
        )
        return video_id, version_id

    @staticmethod
    def _insert_conversation(database, owner: str, video_id: str) -> str:
        return str(
            database.execute(
                """
                insert into video.conversations (
                    owner_id, video_id, title, retrieval_mode,
                    retrieval_config_json, prompt_snapshot_json
                ) values (
                    %s, %s, 'Gradient descent', 'hybrid_rerank',
                    '{"top_k": 8}'::jsonb, '{"profile": "grounded-v1"}'::jsonb
                ) returning id
                """,
                (owner, video_id),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_complete_turn(
        database,
        owner: str,
        video_id: str,
        version_id: str,
        conversation_id: str,
        *,
        turn_index: int = 0,
        cost: str = "0.0125",
    ) -> int:
        return int(
            database.execute(
                """
                insert into video.conversation_turns (
                    owner_id, conversation_id, video_id, ingestion_version_id,
                    turn_index, status, question, rewritten_query, answer,
                    result_json, actual_cost_usd, trace_id, completed_at
                ) values (
                    %s, %s, %s, %s, %s, 'complete',
                    'What does that diagram show?',
                    'What does the gradient-flow diagram at the prior timestamp show?',
                    'It shows gradients flowing backward through the graph.',
                    '{"citations": [{"timestamp_ms": 1000, "modality": "visual_frame"}]}'::jsonb,
                    %s, 'trace-video-turn-1', now()
                ) returning id
                """,
                (
                    owner,
                    conversation_id,
                    video_id,
                    version_id,
                    turn_index,
                    cost,
                ),
            ).fetchone()["id"]
        )

    def test_conversation_tables_have_read_only_owner_rls(self) -> None:
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
                (list(CONVERSATION_TABLES),),
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
                (list(CONVERSATION_TABLES),),
            ).fetchall()

        self.assertEqual(
            [row["table_name"] for row in rows], sorted(CONVERSATION_TABLES)
        )
        for row in rows:
            self.assertTrue(row["relrowsecurity"], row["table_name"])
            self.assertTrue(row["owner_not_null"], row["table_name"])
            self.assertEqual(row["cmd"], "SELECT")
            self.assertEqual(row["roles"], ["authenticated"])
        self.assertEqual(
            {row["table_name"]: row["privileges"] for row in privileges},
            {table: ["SELECT"] for table in CONVERSATION_TABLES},
        )

    def test_complete_turn_pins_rewrite_evidence_version_cost_and_trace(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, version_id = self._published_scope(database, self.owner_a)
            conversation_id = self._insert_conversation(
                database, self.owner_a, video_id
            )
            turn_id = self._insert_complete_turn(
                database,
                self.owner_a,
                video_id,
                version_id,
                conversation_id,
            )
            row = database.execute(
                """
                select question, rewritten_query, answer, result_json,
                       ingestion_version_id, actual_cost_usd, trace_id
                from video.conversation_turns where id = %s
                """,
                (turn_id,),
            ).fetchone()

        self.assertEqual(row["ingestion_version_id"], UUID(version_id))
        self.assertIn("prior timestamp", row["rewritten_query"])
        self.assertIn("flowing backward", row["answer"])
        self.assertEqual(
            row["result_json"]["citations"][0],
            {"timestamp_ms": 1000, "modality": "visual_frame"},
        )
        self.assertEqual(str(row["actual_cost_usd"]), "0.012500")
        self.assertEqual(row["trace_id"], "trace-video-turn-1")

    def test_turns_reject_unpublished_versions(self) -> None:
        fixtures = workflow_schema.VideoWorkflowSchemaTests
        with connection(self.database_url) as database:
            video_id, source_id = fixtures._insert_video_and_source(
                database, self.owner_a
            )
            building_version = fixtures._insert_version(
                database, self.owner_a, video_id, source_id
            )
            conversation_id = self._insert_conversation(
                database, self.owner_a, video_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.conversation_turns (
                            owner_id, conversation_id, video_id,
                            ingestion_version_id, turn_index, question
                        ) values (%s, %s, %s, %s, 0, 'Can I ask yet?')
                        """,
                        (
                            self.owner_a,
                            conversation_id,
                            video_id,
                            building_version,
                        ),
                    )

    def test_answer_cost_cannot_exceed_five_cents(self) -> None:
        with connection(self.database_url) as database:
            video_id, version_id = self._published_scope(database, self.owner_a)
            conversation_id = self._insert_conversation(
                database, self.owner_a, video_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    self._insert_complete_turn(
                        database,
                        self.owner_a,
                        video_id,
                        version_id,
                        conversation_id,
                        cost="0.050001",
                    )

    def test_complete_turn_requires_rewrite_answer_and_evidence_snapshot(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id, version_id = self._published_scope(database, self.owner_a)
            conversation_id = self._insert_conversation(
                database, self.owner_a, video_id
            )
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.conversation_turns (
                            owner_id, conversation_id, video_id,
                            ingestion_version_id, turn_index, status, question,
                            rewritten_query, answer, completed_at
                        ) values (
                            %s, %s, %s, %s, 0, 'complete', 'Explain it',
                            'Explain the diagram', 'Unsupported answer', now()
                        )
                        """,
                        (
                            self.owner_a,
                            conversation_id,
                            video_id,
                            version_id,
                        ),
                    )

    def test_turn_order_is_unique_within_a_conversation(self) -> None:
        with connection(self.database_url) as database:
            video_id, version_id = self._published_scope(database, self.owner_a)
            conversation_id = self._insert_conversation(
                database, self.owner_a, video_id
            )
            self._insert_complete_turn(
                database,
                self.owner_a,
                video_id,
                version_id,
                conversation_id,
            )
            with self.assertRaises(postgres_errors.UniqueViolation):
                with database.transaction():
                    self._insert_complete_turn(
                        database,
                        self.owner_a,
                        video_id,
                        version_id,
                        conversation_id,
                    )

    def test_authenticated_conversation_reads_are_scoped_and_writes_denied(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_a, _ = self._published_scope(database, self.owner_a)
            conversation_a = self._insert_conversation(
                database, self.owner_a, video_a
            )
            video_b, _ = self._published_scope(database, self.owner_b)
            self._insert_conversation(database, self.owner_b, video_b)

            with database.transaction():
                self._as_owner(database, self.owner_a)
                visible = database.execute(
                    "select id from video.conversations"
                ).fetchall()
            self.assertEqual(
                [str(row["id"]) for row in visible], [conversation_a]
            )

            with self.assertRaises(postgres_errors.InsufficientPrivilege):
                with database.transaction():
                    self._as_owner(database, self.owner_a)
                    database.execute(
                        "update video.conversations set title = 'forged'"
                    )

    def test_deleting_conversation_removes_turns_but_keeps_video(self) -> None:
        with connection(self.database_url) as database:
            video_id, version_id = self._published_scope(database, self.owner_a)
            conversation_id = self._insert_conversation(
                database, self.owner_a, video_id
            )
            self._insert_complete_turn(
                database,
                self.owner_a,
                video_id,
                version_id,
                conversation_id,
            )
            database.execute(
                "delete from video.conversations where id = %s",
                (conversation_id,),
            )
            turns = database.execute(
                """
                select count(*) as count from video.conversation_turns
                where conversation_id = %s
                """,
                (conversation_id,),
            ).fetchone()["count"]
            videos = database.execute(
                """
                select count(*) as count from video.videos
                where id = %s and owner_id = %s
                """,
                (video_id, self.owner_a),
            ).fetchone()["count"]

        self.assertEqual(turns, 0)
        self.assertEqual(videos, 1)


if __name__ == "__main__":
    unittest.main()
