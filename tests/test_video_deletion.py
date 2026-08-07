"""Removing a lecture, and the bytes it leaves behind.

A failed ingestion that had already acquired its source could not be deleted
at all, because deleting the row would have stranded the video file on the
volume with nothing pointing at it. The card stayed forever. Deleting the
media makes removal possible, and introduces the one way this can go
catastrophically wrong: canonical media is addressed by content hash, so two
lectures given the same file are two rows over one object on disk. That is not
hypothetical — the production account has a caption uploaded to two videos,
which is one key with two referents.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.media_store import FilesystemMediaStore, InvalidStorageKey
from video.repository import (
    create_youtube_video,
    delete_video,
    list_standalone_videos,
    load_standalone_video,
)
from tests.video_fixtures import publish_video_with_evidence


class MediaRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FilesystemMediaStore(Path(self.directory.name))
        self.owner = uuid4()

    def write(self, storage_key: str, payload: bytes = b"bytes") -> None:
        writer = self.store.writer(
            owner_id=self.owner, storage_key=storage_key, maximum_bytes=1024
        )
        writer.write(payload)
        writer.finish(expected_size=len(payload))

    def test_removing_an_object_reports_whether_it_was_there(self) -> None:
        key = f"{self.owner}/canonical/videos/sha256/ab/cd/abcd.mp4"
        self.write(key)

        self.assertTrue(self.store.remove(owner_id=self.owner, storage_key=key))
        # Idempotent on purpose: the caller unlinks a set of keys after the
        # rows naming them are committed, so a retry after a partial failure
        # must finish rather than raise on the first one already gone.
        self.assertFalse(self.store.remove(owner_id=self.owner, storage_key=key))

    def test_removing_an_object_prunes_the_directories_it_emptied(self) -> None:
        key = f"{self.owner}/canonical/videos/sha256/ab/cd/abcd.mp4"
        self.write(key)
        self.store.remove(owner_id=self.owner, storage_key=key)

        self.assertFalse((self.store.root / str(self.owner) / "canonical").exists())
        # The owner's own root is the boundary and survives.
        self.assertTrue((self.store.root / str(self.owner)).exists())

    def test_pruning_stops_at_a_directory_another_object_still_uses(self) -> None:
        kept = f"{self.owner}/canonical/videos/sha256/ab/cd/kept.mp4"
        removed = f"{self.owner}/canonical/videos/sha256/ab/cd/gone.mp4"
        self.write(kept)
        self.write(removed)
        self.store.remove(owner_id=self.owner, storage_key=removed)

        self.assertTrue(
            self.store.open_path(owner_id=self.owner, storage_key=kept).is_file()
        )

    def test_a_key_outside_its_owner_is_refused_rather_than_removed(self) -> None:
        for key in (
            f"{uuid4()}/canonical/videos/other.mp4",
            f"{self.owner}/../escape.mp4",
        ):
            with self.subTest(key=key), self.assertRaises(InvalidStorageKey):
                self.store.remove(owner_id=self.owner, storage_key=key)


class VideoDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-deletion.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s", (self.owner,)
            )

    def publish(self, database, **overrides):
        """A published lecture whose ingestion job actually finished.

        The shared fixture leaves its job `running`, which is the one state
        that legitimately blocks deletion — so a test that skipped this would
        pass for the wrong reason and prove nothing about acquired media.
        """

        published = publish_video_with_evidence(
            database, owner_id=self.owner, **overrides
        )
        database.execute(
            "update video.ingestion_jobs set status = 'ready', "
            "completed_at = now() where video_id = %s",
            (published.video_id,),
        )
        return published

    def test_a_video_whose_source_was_acquired_can_now_be_deleted(self) -> None:
        """The stuck card: acquired before failing, undeletable afterwards."""

        with connection(self.database_url) as database:
            published = self.publish(database)
            # The stuck card exactly: the source reached 'ready' during
            # acquisition, and the run then failed after it.
            database.execute(
                "update video.videos set readiness_status = 'failed', "
                "ready_at = null, current_ingestion_version_id = null "
                "where id = %s",
                (published.video_id,),
            )
            deletion = delete_video(
                database, published.video_id, owner_id=self.owner
            )
            remaining = load_standalone_video(
                database, published.video_id, owner_id=self.owner
            )

        self.assertIsNotNone(deletion)
        self.assertIsNone(remaining)
        self.assertIn(
            f"{self.owner}/canonical/videos/v.mp4", deletion.orphaned_keys
        )

    def test_media_another_video_still_references_is_never_orphaned(self) -> None:
        """The hazard that exists in production right now.

        The same caption file uploaded to two lectures hashes to one key. If
        deleting either lecture reported that key as unused, the survivor's
        transcript would point at bytes that are gone.
        """

        shared = f"{self.owner}/canonical/captions/sha256/aa/bb/shared.vtt"
        with connection(self.database_url) as database:
            first = self.publish(database, title="First")
            second = self.publish(
                database, title="Second", url="https://youtu.be/bcdefghijkl"
            )
            database.execute(
                "update video.transcript_sources set storage_key = %s "
                "where owner_id = %s",
                (shared, self.owner),
            )
            deletion = delete_video(database, first.video_id, owner_id=self.owner)
            survivor = load_standalone_video(
                database, second.video_id, owner_id=self.owner
            )

        self.assertIsNotNone(survivor)
        self.assertIn(shared, deletion.retained_keys)
        self.assertNotIn(shared, deletion.orphaned_keys)

    def test_a_video_s_own_frames_are_orphaned_by_deleting_it(self) -> None:
        with connection(self.database_url) as database:
            published = self.publish(database)
            frame_keys = {
                row["full_storage_key"]
                for row in database.execute(
                    "select full_storage_key from video.frames where video_id = %s",
                    (published.video_id,),
                ).fetchall()
            }
            deletion = delete_video(
                database, published.video_id, owner_id=self.owner
            )

        self.assertTrue(frame_keys)
        self.assertTrue(frame_keys.issubset(set(deletion.orphaned_keys)))

    def test_a_running_job_blocks_deletion(self) -> None:
        """It holds a lease and writes into the rows this would remove."""

        with connection(self.database_url) as database:
            published = self.publish(database)
            database.execute(
                "update video.ingestion_jobs set status = 'running', "
                "completed_at = null where id = %s",
                (published.job_id,),
            )
            deletion = delete_video(
                database, published.video_id, owner_id=self.owner
            )
            still_listed = list_standalone_videos(database, owner_id=self.owner)

        self.assertIsNone(deletion)
        self.assertEqual(
            [row["id"] for row in still_listed], [published.video_id]
        )
        self.assertFalse(still_listed[0]["deletable"])

    def test_an_acquired_video_now_reports_itself_deletable(self) -> None:
        """The flag the interface reads must agree with what delete_video does.

        Otherwise the reader is offered a button that 409s, or denied one that
        would have worked — which is how the stuck card looked.
        """

        with connection(self.database_url) as database:
            self.publish(database)
            listed = list_standalone_videos(database, owner_id=self.owner)

        self.assertTrue(listed[0]["deletable"])

    def test_deletion_is_owner_scoped(self) -> None:
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://youtu.be/abcdefghijk",
                title="Lecture",
            )
            stranger = delete_video(
                database, created.video_id, owner_id=uuid4()
            )

        self.assertIsNone(stranger)


if __name__ == "__main__":
    unittest.main()
