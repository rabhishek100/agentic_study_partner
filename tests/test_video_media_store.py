"""Filesystem video media is bounded, atomic, and owner scoped."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from video.media_store import (
    FilesystemMediaStore,
    InvalidStorageKey,
    MediaConflict,
    MediaSizeMismatch,
    MediaTooLarge,
)


class VideoMediaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FilesystemMediaStore(self.root)
        self.owner = uuid4()
        self.key = f"{self.owner}/staging/{uuid4()}/original.mp4"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_streams_hashes_and_replays_identical_bytes(self) -> None:
        payload = b"video-bytes-in-several-chunks"
        writer = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=100
        )
        writer.write(payload[:8])
        writer.write(payload[8:])
        stored = writer.finish(expected_size=len(payload))

        replay = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=100
        )
        replay.write(payload)
        replayed = replay.finish(expected_size=len(payload))

        self.assertTrue(stored.created)
        self.assertFalse(replayed.created)
        self.assertEqual(stored.content_hash, sha256(payload).hexdigest())
        self.assertEqual(
            self.store.open_path(owner_id=self.owner, storage_key=self.key).read_bytes(),
            payload,
        )
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_rejects_escape_and_cross_owner_keys(self) -> None:
        for key in (
            f"{uuid4()}/staging/job/original.mp4",
            f"{self.owner}/../other/original.mp4",
            f"/{self.owner}/staging/job/original.mp4",
            f"{self.owner}\\staging\\job\\original.mp4",
        ):
            with self.subTest(key=key), self.assertRaises(InvalidStorageKey):
                self.store.writer(
                    owner_id=self.owner, storage_key=key, maximum_bytes=10
                )

    def test_size_failures_remove_partial_objects(self) -> None:
        too_large = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=3
        )
        with self.assertRaises(MediaTooLarge):
            too_large.write(b"four")
        too_large.abort()

        mismatch = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=10
        )
        mismatch.write(b"tiny")
        with self.assertRaises(MediaSizeMismatch):
            mismatch.finish(expected_size=5)

        self.assertFalse((self.root / self.key).exists())
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_existing_staging_object_is_never_overwritten(self) -> None:
        first = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=10
        )
        first.write(b"first")
        first.finish(expected_size=5)

        changed = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=10
        )
        changed.write(b"other")
        with self.assertRaises(MediaConflict):
            changed.finish(expected_size=5)

        self.assertEqual((self.root / self.key).read_bytes(), b"first")

    def test_imports_content_addressed_canonical_media_idempotently(self) -> None:
        source = self.root / "work.mp4"
        source.write_bytes(b"canonical-video")

        first = self.store.import_file(
            owner_id=self.owner,
            source=source,
            namespace="videos",
            extension=".mp4",
            maximum_bytes=100,
        )
        replay = self.store.import_file(
            owner_id=self.owner,
            source=source,
            namespace="videos",
            extension=".mp4",
            maximum_bytes=100,
        )
        verified = self.store.verify_object(
            owner_id=self.owner,
            storage_key=first.storage_key,
            expected_size=first.size_bytes,
            expected_hash=first.content_hash,
        )

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertIn(f"{self.owner}/canonical/videos/sha256/", first.storage_key)
        self.assertEqual(verified.content_hash, first.content_hash)


if __name__ == "__main__":
    unittest.main()
