"""Video media backends are bounded, immutable, and owner scoped."""

from hashlib import md5, sha256
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from video.media_store import (
    FilesystemMediaStore,
    InvalidStorageKey,
    MediaConflict,
    MediaStoreError,
    MediaSizeMismatch,
    MediaTooLarge,
    S3MediaStore,
    configured_media_store,
)


class MissingObject(Exception):
    response = {
        "Error": {"Code": "NoSuchKey"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.uploads = 0
        self.downloads = 0
        self.modified: dict[tuple[str, str], datetime] = {}
        self.etags: dict[tuple[str, str], str] = {}

    def head_object(self, *, Bucket: str, Key: str):
        try:
            payload, metadata = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise MissingObject() from error
        etag = self.etags.get(
            (Bucket, Key), f"etag-{sha256(payload).hexdigest()}"
        )
        return {
            "ContentLength": len(payload),
            "ETag": f'"{etag}"',
            "Metadata": metadata,
        }

    def upload_file(
        self, filename: str, bucket: str, key: str, ExtraArgs: dict
    ) -> None:
        self.uploads += 1
        self.objects[(bucket, key)] = (
            Path(filename).read_bytes(),
            dict(ExtraArgs.get("Metadata") or {}),
        )
        self.modified[(bucket, key)] = datetime.now(timezone.utc)

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.downloads += 1
        try:
            payload, _ = self.objects[(bucket, key)]
        except KeyError as error:
            raise MissingObject() from error
        Path(filename).write_bytes(payload)

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)
        self.modified.pop((Bucket, Key), None)

    def list_objects_v2(self, *, Bucket: str, MaxKeys: int, **kwargs):
        del kwargs
        values = [
            {
                "Key": key,
                "Size": len(payload),
                "LastModified": self.modified[(bucket, key)],
            }
            for (bucket, key), (payload, _) in sorted(self.objects.items())
            if bucket == Bucket
        ][:MaxKeys]
        return {"Contents": values, "IsTruncated": False}


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


class S3VideoMediaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = FakeS3Client()
        self.store = S3MediaStore(
            bucket="private-video-media",
            client=self.client,
            cache_root=self.root / "cache",
        )
        self.owner = uuid4()
        self.key = f"{self.owner}/staging/{uuid4()}/original.mp4"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stream_upload_is_immutable_and_download_cache_is_reused(self) -> None:
        payload = b"r2-video-content"
        writer = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=100
        )
        writer.write(payload[:4])
        writer.write(payload[4:])
        stored = writer.finish(expected_size=len(payload))

        first_path = self.store.open_path(
            owner_id=self.owner, storage_key=self.key
        )
        second_path = self.store.open_path(
            owner_id=self.owner, storage_key=self.key
        )

        self.assertTrue(stored.created)
        self.assertEqual(first_path, second_path)
        self.assertEqual(first_path.read_bytes(), payload)
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(self.client.downloads, 1)

    def put_foreign_object(self, key: str, payload: bytes, *, etag: str) -> None:
        """An object nothing of ours wrote: no sha256 metadata, a real ETag."""

        self.client.objects[(self.store.bucket, key)] = (payload, {})
        self.client.modified[(self.store.bucket, key)] = datetime.now(timezone.utc)
        self.client.etags[(self.store.bucket, key)] = etag

    def test_a_same_size_object_is_not_accepted_on_its_length_alone(self) -> None:
        """R2 holds objects we did not write, and those carry no sha256.

        Nearly every frame recovered from the outage is one of these. Size was
        the only thing checked, so a different object of the same length —
        a truncated re-encode, a mixed-up key, a partially overwritten
        upload — verified clean. The ETag is an MD5 of the whole object
        whenever it was stored in one part, and that is what catches it.
        """

        key = f"{self.owner}/canonical/frames/sha256/aa/bb/aabb.webp"
        honest = b"the-frame-we-asked-for"
        impostor = b"a-different-frame-xxxx"
        self.assertEqual(len(honest), len(impostor))

        # The bucket reports the ETag of the object we asked for and serves
        # different bytes of the same length.
        self.put_foreign_object(key, impostor, etag=md5(honest).hexdigest())

        with self.assertRaises(MediaConflict):
            self.store.open_path(owner_id=self.owner, storage_key=key)

    def test_a_foreign_object_verifies_and_then_stays_cached(self) -> None:
        """An object without sha256 metadata must not be re-downloaded forever.

        The cache marker recorded our own computed digest under the name the
        remote's assertion was compared against, so for these objects every
        read missed the cache and paid for a fresh download.
        """

        key = f"{self.owner}/canonical/frames/sha256/cc/dd/ccdd.webp"
        payload = b"a-recovered-frame"
        self.put_foreign_object(key, payload, etag=md5(payload).hexdigest())

        first = self.store.open_path(owner_id=self.owner, storage_key=key)
        second = self.store.open_path(owner_id=self.owner, storage_key=key)

        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), payload)
        self.assertEqual(self.client.downloads, 1)

    def test_content_addressed_import_replays_without_an_upload(self) -> None:
        source = self.root / "source.mp4"
        source.write_bytes(b"canonical-r2-video")

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
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(verified.content_hash, first.content_hash)

    def test_conflicting_existing_key_is_not_overwritten(self) -> None:
        first = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=100
        )
        first.write(b"first")
        first.finish(expected_size=5)

        changed = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=100
        )
        changed.write(b"other")
        with self.assertRaises(MediaConflict):
            changed.finish(expected_size=5)

        self.assertEqual(
            self.client.objects[(self.store.bucket, self.key)][0], b"first"
        )
        self.assertEqual(self.client.uploads, 1)

    def test_remove_is_idempotent_and_owner_scoped(self) -> None:
        writer = self.store.writer(
            owner_id=self.owner, storage_key=self.key, maximum_bytes=100
        )
        writer.write(b"payload")
        writer.finish(expected_size=7)

        self.assertTrue(
            self.store.remove(owner_id=self.owner, storage_key=self.key)
        )
        self.assertFalse(
            self.store.remove(owner_id=self.owner, storage_key=self.key)
        )
        with self.assertRaises(InvalidStorageKey):
            self.store.open_path(owner_id=uuid4(), storage_key=self.key)

    def test_backend_factory_keeps_local_default_and_rejects_partial_r2(self) -> None:
        with patch.dict(
            "os.environ",
            {"VIDEO_MEDIA_BACKEND": "filesystem", "VIDEO_MEDIA_ROOT": str(self.root)},
            clear=True,
        ):
            self.assertIsInstance(configured_media_store(), FilesystemMediaStore)
        with patch.dict("os.environ", {"VIDEO_MEDIA_BACKEND": "r2"}, clear=True):
            with self.assertRaisesRegex(MediaStoreError, "VIDEO_S3_BUCKET"):
                configured_media_store()


if __name__ == "__main__":
    unittest.main()
