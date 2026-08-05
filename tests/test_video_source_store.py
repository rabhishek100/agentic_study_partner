"""Canonical video media facts are persisted behind worker lease fences."""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.acquisition import Chapter, MediaMetadata
from video.jobs import (
    advance_stage,
    begin_stage_checkpoint,
    claim_next_job,
    complete_stage_checkpoint,
)
from video.repository import create_youtube_video
from video.source_store import (
    VideoSourceConflictError,
    VideoSourceNotFoundError,
    load_acquisition_target,
    publish_media_metadata,
    record_acquired_media,
)
from video.states import Stage


class VideoSourceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-source-store.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def create_claim(self, database, *, title=None):
        created = create_youtube_video(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            url="https://youtu.be/abcdefghijk",
            title=title,
        )
        claimed = claim_next_job(database, worker_id="video-worker")
        self.assertEqual(claimed.id, created.job_id)
        return created, claimed

    def record(self, database, claimed, **overrides):
        values = {
            "job_id": claimed.id,
            "worker_id": "video-worker",
            "attempt_count": claimed.attempt_count,
            "storage_backend": "filesystem",
            "storage_key": f"{self.owner}/canonical/videos/source.mp4",
            "content_hash": "a" * 64,
            "size_bytes": 100,
            "media_type": "video/mp4",
            "acquisition_provider": "yt-dlp",
            "acquisition_version": "2026.7.4",
            "provenance": {
                "metadata": {
                    "storage_key": f"{self.owner}/canonical/metadata/info.json",
                    "content_hash": "b" * 64,
                }
            },
        }
        values.update(overrides)
        return record_acquired_media(database, **values)

    def advance_to_metadata(self, database, claimed) -> None:
        dependency_hash = "c" * 64
        begin_stage_checkpoint(
            database,
            job_id=claimed.id,
            worker_id="video-worker",
            attempt_count=claimed.attempt_count,
            stage=Stage.ACQUIRE_SOURCE,
            dependency_hash=dependency_hash,
        )
        complete_stage_checkpoint(
            database,
            job_id=claimed.id,
            worker_id="video-worker",
            attempt_count=claimed.attempt_count,
            stage=Stage.ACQUIRE_SOURCE,
            dependency_hash=dependency_hash,
            output_manifest={"video_content_hash": "a" * 64},
        )
        advance_stage(
            database,
            job_id=claimed.id,
            worker_id="video-worker",
            attempt_count=claimed.attempt_count,
            next_stage=Stage.MEDIA_METADATA,
        )

    @staticmethod
    def media() -> MediaMetadata:
        return MediaMetadata(
            duration_ms=10_000,
            width=1920,
            height=1080,
            video_codec="h264",
            audio_codec="aac",
            format_name="mov,mp4",
            size_bytes=100,
        )

    def test_acquisition_target_and_canonical_media_are_fenced_and_idempotent(
        self,
    ) -> None:
        with connection(self.database_url) as database:
            created, claimed = self.create_claim(database)
            target = load_acquisition_target(
                database,
                job_id=claimed.id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
            )
            first = self.record(database, claimed)
            replay = self.record(database, claimed)
            source = database.execute(
                """
                select status, storage_key, content_hash, acquisition_provider,
                       acquisition_version, acquired_at, provenance_json
                from video.video_sources where id = %s
                """,
                (created.source_id,),
            ).fetchone()

        self.assertEqual(target.source_url, "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(target.youtube_video_id, "abcdefghijk")
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(source["status"], "acquiring")
        self.assertEqual(source["content_hash"], "a" * 64)
        self.assertEqual(source["acquisition_provider"], "yt-dlp")
        self.assertEqual(source["acquisition_version"], "2026.7.4")
        self.assertIsNone(source["acquired_at"])
        self.assertEqual(source["provenance_json"]["metadata"]["content_hash"], "b" * 64)

    def test_wrong_worker_and_changed_canonical_bytes_are_rejected(self) -> None:
        with connection(self.database_url) as database:
            _, claimed = self.create_claim(database)
            with self.assertRaises(VideoSourceNotFoundError):
                load_acquisition_target(
                    database,
                    job_id=claimed.id,
                    worker_id="stale-worker",
                    attempt_count=claimed.attempt_count,
                )
            self.record(database, claimed)
            with self.assertRaises(VideoSourceConflictError):
                self.record(database, claimed, content_hash="d" * 64)

    def test_metadata_publishes_duration_playback_and_official_chapters(self) -> None:
        with connection(self.database_url) as database:
            created, claimed = self.create_claim(database)
            self.record(database, claimed)
            self.advance_to_metadata(database, claimed)
            chapters = (
                Chapter(0, "Introduction", 0, 4_000),
                Chapter(1, "Attention", 4_000, 10_000),
            )
            first = publish_media_metadata(
                database,
                job_id=claimed.id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                media=self.media(),
                chapters=chapters,
                discovered_title="Stanford CME 295: Lecture 1",
                discovered_description="Course lecture",
            )
            replay = publish_media_metadata(
                database,
                job_id=claimed.id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                media=self.media(),
                chapters=chapters,
                discovered_title="Stanford CME 295: Lecture 1",
                discovered_description="Course lecture",
            )
            source = database.execute(
                """
                select status, media_metadata_json, acquired_at
                from video.video_sources where id = %s
                """,
                (created.source_id,),
            ).fetchone()
            video = database.execute(
                """
                select title, description, duration_ms, playback_json
                from video.videos where id = %s
                """,
                (created.video_id,),
            ).fetchone()
            stored_chapters = database.execute(
                """
                select chapter_index, chapter_kind, title, start_ms, end_ms
                from video.chapters where video_source_id = %s
                order by chapter_index
                """,
                (created.source_id,),
            ).fetchall()

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(source["status"], "ready")
        self.assertIsNotNone(source["acquired_at"])
        self.assertEqual(source["media_metadata_json"]["duration_ms"], 10_000)
        self.assertEqual(video["title"], "Stanford CME 295: Lecture 1")
        self.assertEqual(video["description"], "Course lecture")
        self.assertEqual(video["duration_ms"], 10_000)
        self.assertEqual(video["playback_json"]["kind"], "youtube")
        self.assertEqual(
            [row["chapter_kind"] for row in stored_chapters],
            ["youtube", "youtube"],
        )

    def test_user_metadata_is_preserved_and_metadata_drift_conflicts(self) -> None:
        with connection(self.database_url) as database:
            created, claimed = self.create_claim(database, title="My lecture notes")
            database.execute(
                "update video.videos set description = 'My description' where id = %s",
                (created.video_id,),
            )
            self.record(database, claimed)
            self.advance_to_metadata(database, claimed)
            publish_media_metadata(
                database,
                job_id=claimed.id,
                worker_id="video-worker",
                attempt_count=claimed.attempt_count,
                media=self.media(),
                discovered_title="Provider title",
                discovered_description="Provider description",
            )
            with self.assertRaises(VideoSourceConflictError):
                publish_media_metadata(
                    database,
                    job_id=claimed.id,
                    worker_id="video-worker",
                    attempt_count=claimed.attempt_count,
                    media=MediaMetadata(**{**self.media().__dict__, "width": 1280}),
                )
            video = database.execute(
                "select title, description from video.videos where id = %s",
                (created.video_id,),
            ).fetchone()

        self.assertEqual(video["title"], "My lecture notes")
        self.assertEqual(video["description"], "My description")

    def test_invalid_chapters_roll_back_without_publishing_the_source(self) -> None:
        with connection(self.database_url) as database:
            created, claimed = self.create_claim(database)
            self.record(database, claimed)
            self.advance_to_metadata(database, claimed)
            with self.assertRaises(ValueError):
                publish_media_metadata(
                    database,
                    job_id=claimed.id,
                    worker_id="video-worker",
                    attempt_count=claimed.attempt_count,
                    media=self.media(),
                    chapters=[Chapter(0, "Late start", 1_000, 10_000)],
                )
            source = database.execute(
                "select status from video.video_sources where id = %s",
                (created.source_id,),
            ).fetchone()
            counts = database.execute(
                """
                select
                    (select count(*) from video.chapters
                     where video_id = %s) as chapters,
                    (select count(*) from video.videos
                     where id = %s and duration_ms is not null) as durations
                """,
                (created.video_id, created.video_id),
            ).fetchone()

        self.assertEqual(source["status"], "acquiring")
        self.assertEqual(dict(counts), {"chapters": 0, "durations": 0})


if __name__ == "__main__":
    unittest.main()
