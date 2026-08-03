"""Canonical video-schema constraints, ownership, RLS, and cascades."""

import unittest
from uuid import uuid4

from psycopg import errors as postgres_errors

from storage.database import connection, resolve_database_url


TABLES = (
    "videos",
    "video_sources",
    "chapters",
    "resources",
    "video_resources",
    "courses",
    "course_lectures",
    "course_resources",
)


class VideoSchemaTests(unittest.TestCase):
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
                    (owner, f"{owner}@video-schema.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_a, self.owner_b],),
            )

    @staticmethod
    def _insert_video(database, owner: str, *, title: str = "Lecture") -> str:
        return str(
            database.execute(
                """
                insert into video.videos (owner_id, title, source_kind)
                values (%s, %s, 'youtube')
                returning id
                """,
                (owner, title),
            ).fetchone()["id"]
        )

    @staticmethod
    def _insert_source(database, owner: str, video_id: str) -> str:
        return str(
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
                    %s, 1024, 'video/mp4', now()
                )
                returning id
                """,
                (
                    owner,
                    video_id,
                    f"{owner}/sha256/aa/{'a' * 64}.mp4",
                    "a" * 64,
                ),
            ).fetchone()["id"]
        )

    @staticmethod
    def _as_owner(database, owner: str) -> None:
        database.execute("set local role authenticated")
        database.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub": "{owner}", "role": "authenticated"}}',),
        )

    def test_expected_tables_have_owner_columns_and_read_only_rls(self) -> None:
        with connection(self.database_url, readonly=True) as database:
            rows = database.execute(
                """
                select
                    c.relname as table_name,
                    c.relrowsecurity,
                    a.attnotnull as owner_not_null,
                    p.cmd,
                    p.roles
                from pg_class as c
                join pg_namespace as n on n.oid = c.relnamespace
                join pg_attribute as a
                  on a.attrelid = c.oid and a.attname = 'owner_id'
                join pg_policies as p
                  on p.schemaname = n.nspname and p.tablename = c.relname
                where n.nspname = 'video'
                  and c.relkind = 'r'
                  and c.relname = any(%s)
                order by c.relname
                """,
                (list(TABLES),),
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
                order by table_name
                """,
                (list(TABLES),),
            ).fetchall()

        self.assertEqual([row["table_name"] for row in rows], sorted(TABLES))
        for row in rows:
            self.assertTrue(row["relrowsecurity"], row["table_name"])
            self.assertTrue(row["owner_not_null"], row["table_name"])
            self.assertEqual(row["cmd"], "SELECT")
            self.assertEqual(row["roles"], ["authenticated"])
        self.assertEqual(
            {
                row["table_name"]: row["privileges"]
                for row in privileges
            },
            {table: ["SELECT"] for table in TABLES},
        )

    def test_canonical_video_source_chapter_and_resource_links_round_trip(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_id = self._insert_video(database, self.owner_a)
            source_id = self._insert_source(database, self.owner_a, video_id)
            database.execute(
                """
                insert into video.chapters (
                    owner_id, video_id, video_source_id, chapter_index,
                    chapter_kind, title, start_ms, end_ms
                ) values (%s, %s, %s, 0, 'youtube', 'Introduction', 0, 60000)
                """,
                (self.owner_a, video_id, source_id),
            )
            resource_id = str(
                database.execute(
                    """
                    insert into video.resources (
                        owner_id, resource_kind, origin, status, title, source_url
                    ) values (
                        %s, 'external_link', 'discovered', 'ready',
                        'Course notes', 'https://example.test/notes'
                    ) returning id
                    """,
                    (self.owner_a,),
                ).fetchone()["id"]
            )
            database.execute(
                """
                insert into video.video_resources (
                    owner_id, video_id, resource_id, role
                ) values (%s, %s, %s, 'notes')
                """,
                (self.owner_a, video_id, resource_id),
            )

            row = database.execute(
                """
                select v.title, s.youtube_video_id, c.title as chapter,
                       r.title as resource
                from video.videos as v
                join video.video_sources as s
                  on s.video_id = v.id and s.owner_id = v.owner_id
                join video.chapters as c
                  on c.video_id = v.id and c.owner_id = v.owner_id
                join video.video_resources as vr
                  on vr.video_id = v.id and vr.owner_id = v.owner_id
                join video.resources as r
                  on r.id = vr.resource_id and r.owner_id = vr.owner_id
                where v.owner_id = %s and v.id = %s
                """,
                (self.owner_a, video_id),
            ).fetchone()

        self.assertEqual(
            dict(row),
            {
                "title": "Lecture",
                "youtube_video_id": "abcdefghijk",
                "chapter": "Introduction",
                "resource": "Course notes",
            },
        )

    def test_cross_owner_course_and_resource_links_are_rejected(self) -> None:
        with connection(self.database_url) as database:
            video_b = self._insert_video(database, self.owner_b)
            course_a = database.execute(
                """
                insert into video.courses (owner_id, title)
                values (%s, 'Private course') returning id
                """,
                (self.owner_a,),
            ).fetchone()["id"]
            resource_b = database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title, source_url
                ) values (
                    %s, 'external_link', 'url', 'ready',
                    'Private notes', 'https://example.test/private'
                ) returning id
                """,
                (self.owner_b,),
            ).fetchone()["id"]

            with self.assertRaises(postgres_errors.ForeignKeyViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.course_lectures (
                            owner_id, course_id, video_id, lecture_index
                        ) values (%s, %s, %s, 0)
                        """,
                        (self.owner_a, course_a, video_b),
                    )
            with self.assertRaises(postgres_errors.ForeignKeyViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.course_resources (
                            owner_id, course_id, resource_id, role
                        ) values (%s, %s, %s, 'notes')
                        """,
                        (self.owner_a, course_a, resource_b),
                    )

    def test_ready_sources_require_owner_scoped_canonical_storage(self) -> None:
        with connection(self.database_url) as database:
            video_id = self._insert_video(database, self.owner_a)
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.video_sources (
                            owner_id, video_id, source_kind, status,
                            source_url, youtube_video_id, storage_backend,
                            storage_key, content_hash, size_bytes, media_type,
                            acquired_at
                        ) values (
                            %s, %s, 'youtube', 'ready',
                            'https://www.youtube.com/watch?v=abcdefghijk',
                            'abcdefghijk', 'filesystem', %s, %s, 10,
                            'video/mp4', now()
                        )
                        """,
                        (
                            self.owner_a,
                            video_id,
                            f"{self.owner_b}/sha256/aa/video.mp4",
                            "a" * 64,
                        ),
                    )

    def test_ready_pdf_requires_a_complete_owner_scoped_canonical_record(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            with self.assertRaises(postgres_errors.CheckViolation):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.resources (
                            owner_id, resource_kind, origin, status, title,
                            source_url, storage_backend, storage_key,
                            content_hash, size_bytes, media_type
                        ) values (
                            %s, 'pdf', 'url', 'ready', 'Slides',
                            'https://example.test/slides.pdf', 'filesystem',
                            %s, %s, 2048, 'application/pdf'
                        )
                        """,
                        (
                            self.owner_a,
                            f"{self.owner_a}/sha256/bb/slides.pdf",
                            "b" * 64,
                        ),
                    )

            resource = database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title,
                    source_url, storage_backend, storage_key,
                    content_hash, size_bytes, media_type, page_count
                ) values (
                    %s, 'pdf', 'url', 'ready', 'Slides',
                    'https://example.test/slides.pdf', 'filesystem',
                    %s, %s, 2048, 'application/pdf', 12
                ) returning status, page_count
                """,
                (
                    self.owner_a,
                    f"{self.owner_a}/sha256/bb/slides.pdf",
                    "b" * 64,
                ),
            ).fetchone()

        self.assertEqual(dict(resource), {"status": "ready", "page_count": 12})

    def test_authenticated_reads_are_owner_scoped_and_writes_are_denied(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            video_a = self._insert_video(database, self.owner_a, title="Owner A")
            self._insert_video(database, self.owner_b, title="Owner B")

            with database.transaction():
                self._as_owner(database, self.owner_a)
                visible = database.execute(
                    "select id, title from video.videos"
                ).fetchall()
            self.assertEqual(
                [(str(row["id"]), row["title"]) for row in visible],
                [(video_a, "Owner A")],
            )

            with self.assertRaises(postgres_errors.InsufficientPrivilege):
                with database.transaction():
                    self._as_owner(database, self.owner_a)
                    database.execute(
                        """
                        insert into video.videos (
                            owner_id, title, source_kind
                        ) values (%s, 'Browser write', 'upload')
                        """,
                        (self.owner_a,),
                    )

    def test_deleting_owner_cascades_all_canonical_video_records(self) -> None:
        with connection(self.database_url) as database:
            video_id = self._insert_video(database, self.owner_a)
            source_id = self._insert_source(database, self.owner_a, video_id)
            database.execute(
                """
                insert into video.chapters (
                    owner_id, video_id, video_source_id, chapter_index,
                    chapter_kind, title, start_ms, end_ms
                ) values (%s, %s, %s, 0, 'youtube', 'Start', 0, 1000)
                """,
                (self.owner_a, video_id, source_id),
            )
            resource_id = database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title, source_url
                ) values (
                    %s, 'external_link', 'url', 'ready',
                    'Notes', 'https://example.test/notes'
                ) returning id
                """,
                (self.owner_a,),
            ).fetchone()["id"]
            course_id = database.execute(
                """
                insert into video.courses (owner_id, title)
                values (%s, 'Course') returning id
                """,
                (self.owner_a,),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.video_resources (
                    owner_id, video_id, resource_id, role
                ) values (%s, %s, %s, 'notes')
                """,
                (self.owner_a, video_id, resource_id),
            )
            database.execute(
                """
                insert into video.course_lectures (
                    owner_id, course_id, video_id, lecture_index
                ) values (%s, %s, %s, 0)
                """,
                (self.owner_a, course_id, video_id),
            )
            database.execute(
                """
                insert into video.course_resources (
                    owner_id, course_id, resource_id, role
                ) values (%s, %s, %s, 'notes')
                """,
                (self.owner_a, course_id, resource_id),
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
                for table in TABLES
            }

        self.assertEqual(counts, {table: 0 for table in TABLES})


if __name__ == "__main__":
    unittest.main()
