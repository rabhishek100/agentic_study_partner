"""Video-domain repository transactions, ownership, and source parsing."""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.repository import (
    VideoAlreadyExistsError,
    VideoConflictError,
    VideoNotFoundError,
    confirm_resource_suggestion,
    create_url_resource,
    create_youtube_video,
    delete_unacquired_video,
    detach_video_resource,
    dismiss_resource_suggestion,
    initialize_video_upload,
    list_job_events,
    list_resource_suggestions,
    list_standalone_videos,
    list_video_resources,
    load_ingestion_job,
    load_standalone_video,
    update_video_metadata,
)
from video.sources import InvalidVideoSource, parse_youtube_url, upload_extension


class VideoSourceTests(unittest.TestCase):
    def test_supported_youtube_forms_normalize_to_one_watch_url(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=abcdefghijk&t=60",
            "https://youtu.be/abcdefghijk?si=tracking",
            "https://youtube.com/embed/abcdefghijk",
            "https://youtube.com/shorts/abcdefghijk",
            "https://youtube.com/live/abcdefghijk",
        ):
            with self.subTest(url=url):
                source = parse_youtube_url(url)
                self.assertEqual(source.video_id, "abcdefghijk")
                self.assertEqual(
                    source.canonical_url,
                    "https://www.youtube.com/watch?v=abcdefghijk",
                )

    def test_non_youtube_playlist_only_and_malformed_urls_are_rejected(self) -> None:
        for url in (
            "https://example.com/watch?v=abcdefghijk",
            "https://youtube.com/playlist?list=PL123",
            "https://youtube.com/watch?v=short",
            "youtube.com/watch?v=abcdefghijk",
        ):
            with self.subTest(url=url), self.assertRaises(InvalidVideoSource):
                parse_youtube_url(url)

    def test_upload_extension_must_match_supported_media_type(self) -> None:
        self.assertEqual(upload_extension("lecture.mp4", "video/mp4"), ".mp4")
        with self.assertRaises(InvalidVideoSource):
            upload_extension("lecture.mov", "video/mp4")
        with self.assertRaises(InvalidVideoSource):
            upload_extension("lecture.mp4", "application/octet-stream")


class VideoRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_a, self.owner_b = uuid4(), uuid4()
        with connection(self.database_url) as database:
            for owner in (self.owner_a, self.owner_b):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-repository.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_a, self.owner_b],),
            )

    def create_youtube(self, database, *, owner=None, key=None, title="Lecture"):
        return create_youtube_video(
            database,
            owner_id=owner or self.owner_a,
            idempotency_key=key or uuid4(),
            url="https://youtu.be/abcdefghijk?si=tracking",
            title=title,
        )

    def test_youtube_creation_atomically_creates_source_version_job_and_event(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            rows = database.execute(
                """
                select
                    (select count(*) from video.videos
                     where id = %s and owner_id = %s) as videos,
                    (select count(*) from video.video_sources
                     where video_id = %s and owner_id = %s) as sources,
                    (select count(*) from video.ingestion_versions
                     where video_id = %s and owner_id = %s) as versions,
                    (select count(*) from video.ingestion_jobs
                     where video_id = %s and owner_id = %s) as jobs,
                    (select count(*) from video.ingestion_job_events
                     where job_id = %s and owner_id = %s) as events
                """,
                (
                    created.video_id,
                    self.owner_a,
                    created.video_id,
                    self.owner_a,
                    created.video_id,
                    self.owner_a,
                    created.video_id,
                    self.owner_a,
                    created.job_id,
                    self.owner_a,
                ),
            ).fetchone()
            source = database.execute(
                """
                select source_url, youtube_video_id, status
                from video.video_sources where id = %s
                """,
                (created.source_id,),
            ).fetchone()

        self.assertTrue(created.created)
        self.assertEqual(created.job_status, "queued")
        self.assertEqual(dict(rows), {key: 1 for key in rows})
        self.assertEqual(source["youtube_video_id"], "abcdefghijk")
        self.assertEqual(
            source["source_url"],
            "https://www.youtube.com/watch?v=abcdefghijk",
        )
        self.assertEqual(source["status"], "pending")

    def test_idempotency_replays_and_duplicate_source_rolls_back(self) -> None:
        key = uuid4()
        with connection(self.database_url) as database:
            first = self.create_youtube(database, key=key)
            replay = self.create_youtube(database, key=key, title="Ignored")
            with self.assertRaises(VideoAlreadyExistsError):
                with database.transaction():
                    self.create_youtube(database, key=uuid4())
            count = database.execute(
                "select count(*) as count from video.videos where owner_id = %s",
                (self.owner_a,),
            ).fetchone()["count"]

        self.assertFalse(replay.created)
        self.assertEqual(replay.video_id, first.video_id)
        self.assertEqual(replay.job_id, first.job_id)
        self.assertEqual(count, 1)

    def test_different_owners_can_ingest_the_same_youtube_video(self) -> None:
        with connection(self.database_url) as database:
            first = self.create_youtube(database, owner=self.owner_a)
            second = self.create_youtube(database, owner=self.owner_b)

        self.assertNotEqual(first.video_id, second.video_id)

    def test_upload_initialization_reserves_no_fake_supabase_object(self) -> None:
        with connection(self.database_url) as database:
            created = initialize_video_upload(
                database,
                owner_id=self.owner_a,
                idempotency_key=uuid4(),
                original_filename="../course/lecture.mp4",
                media_type="video/mp4",
                declared_size_bytes=250_000_000,
            )
            source = database.execute(
                """
                select original_filename, storage_backend, storage_key, status
                from video.video_sources where id = %s
                """,
                (created.source_id,),
            ).fetchone()

        self.assertEqual(created.job_status, "awaiting_upload")
        self.assertTrue(created.upload_storage_key.startswith(f"{self.owner_a}/"))
        self.assertIn(str(created.job_id), created.upload_storage_key)
        self.assertEqual(source["original_filename"], "lecture.mp4")
        self.assertIsNone(source["storage_backend"])
        self.assertIsNone(source["storage_key"])
        self.assertEqual(source["status"], "pending")

    def test_listing_is_owner_scoped_processing_visible_and_course_excluded(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            standalone = self.create_youtube(database)
            course_video = create_youtube_video(
                database,
                owner_id=self.owner_a,
                idempotency_key=uuid4(),
                url="https://youtu.be/lmnopqrstuv",
                title="Course lecture",
            )
            other = self.create_youtube(database, owner=self.owner_b)
            course_id = database.execute(
                """
                insert into video.courses (owner_id, title)
                values (%s, 'Course') returning id
                """,
                (self.owner_a,),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.course_lectures (
                    owner_id, course_id, video_id, lecture_index
                ) values (%s, %s, %s, 0)
                """,
                (self.owner_a, course_id, course_video.video_id),
            )
            listed = list_standalone_videos(database, owner_id=self.owner_a)

        self.assertEqual([row["id"] for row in listed], [standalone.video_id])
        self.assertEqual(listed[0]["readiness_status"], "processing")
        self.assertNotEqual(listed[0]["id"], other.video_id)

    def test_load_update_and_delete_are_owner_and_standalone_scoped(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            self.assertIsNone(
                load_standalone_video(
                    database, created.video_id, owner_id=self.owner_b
                )
            )
            updated = update_video_metadata(
                database,
                created.video_id,
                owner_id=self.owner_a,
                title="  New title  ",
                description="  Explanation  ",
            )
            foreign_delete = delete_unacquired_video(
                database, created.video_id, owner_id=self.owner_b
            )
            own_delete = delete_unacquired_video(
                database, created.video_id, owner_id=self.owner_a
            )

        self.assertEqual(updated["title"], "New title")
        self.assertEqual(updated["description"], "Explanation")
        self.assertFalse(foreign_delete)
        self.assertTrue(own_delete)

    def test_external_resource_detach_preserves_canonical_resource(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            resource = create_url_resource(
                database,
                owner_id=self.owner_a,
                video_id=created.video_id,
                resource_kind="external_link",
                title="Course notes",
                source_url="https://example.test/notes",
                role="notes",
            )
            listed = list_video_resources(
                database, created.video_id, owner_id=self.owner_a
            )
            detached = detach_video_resource(
                database,
                created.video_id,
                resource["id"],
                owner_id=self.owner_a,
            )
            survivor = database.execute(
                "select id from video.resources where id = %s",
                (resource["id"],),
            ).fetchone()

        self.assertEqual(listed[0]["role"], "notes")
        self.assertTrue(detached)
        self.assertIsNotNone(survivor)

    def _suggestion(self, database, video_id, *, kind="external_link"):
        return database.execute(
            """
            insert into video.resource_suggestions (
                owner_id, video_id, suggested_kind, url, normalized_url, title
            ) values (%s, %s, %s, %s, %s, 'Suggested material')
            returning id
            """,
            (
                self.owner_a,
                video_id,
                kind,
                "https://example.test/material",
                "https://example.test/material",
            ),
        ).fetchone()["id"]

    def test_suggestion_confirmation_is_atomic_and_idempotent(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            suggestion_id = self._suggestion(database, created.video_id)
            first = confirm_resource_suggestion(
                database,
                created.video_id,
                suggestion_id,
                owner_id=self.owner_a,
                role="reference",
            )
            replay = confirm_resource_suggestion(
                database,
                created.video_id,
                suggestion_id,
                owner_id=self.owner_a,
            )
            suggestions = list_resource_suggestions(
                database, created.video_id, owner_id=self.owner_a
            )

        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(suggestions[0]["status"], "confirmed")
        self.assertEqual(suggestions[0]["confirmed_resource_id"], first["id"])

    def test_suggestion_resolution_cannot_reverse_direction(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            dismissed_id = self._suggestion(database, created.video_id)
            dismissed = dismiss_resource_suggestion(
                database,
                created.video_id,
                dismissed_id,
                owner_id=self.owner_a,
            )
            with self.assertRaises(VideoConflictError):
                confirm_resource_suggestion(
                    database,
                    created.video_id,
                    dismissed_id,
                    owner_id=self.owner_a,
                )

        self.assertEqual(dismissed["status"], "dismissed")

    def test_job_reads_are_owner_scoped_and_do_not_return_private_fields(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            job = load_ingestion_job(
                database, created.job_id, owner_id=self.owner_a
            )
            foreign = load_ingestion_job(
                database, created.job_id, owner_id=self.owner_b
            )
            events = list_job_events(
                database, created.job_id, owner_id=self.owner_a
            )

        self.assertIsNotNone(job)
        self.assertIsNone(foreign)
        self.assertEqual(events[0]["event_type"], "created")
        for private in (
            "owner_id",
            "staging_storage_key",
            "lease_owner",
            "last_error_message",
            "provenance_json",
        ):
            self.assertNotIn(private, job)
            self.assertNotIn(private, events[0])

    def test_foreign_video_resource_operations_are_not_found(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_youtube(database)
            with self.assertRaises(VideoNotFoundError):
                create_url_resource(
                    database,
                    owner_id=self.owner_b,
                    video_id=created.video_id,
                    resource_kind="external_link",
                    title="Nope",
                    source_url="https://example.test/nope",
                    role="reference",
                )


if __name__ == "__main__":
    unittest.main()
