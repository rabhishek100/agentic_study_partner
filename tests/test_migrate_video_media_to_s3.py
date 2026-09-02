"""The filesystem-to-R2 cutover is verified and safely resumable."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from scripts.migrate_video_media_to_s3 import migrate_media_tree
from tests.test_video_media_store import FakeS3Client
from video.media_store import MediaConflict, S3MediaStore


class VideoMediaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.owner = uuid4()
        self.key = f"{self.owner}/canonical/videos/source.mp4"
        self.source = self.root / self.key
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"canonical-video")
        self.client = FakeS3Client()
        self.store = S3MediaStore(
            bucket="course-media",
            client=self.client,
            cache_root=self.root / ".cache",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_does_not_upload(self) -> None:
        summary = migrate_media_tree(
            source_root=self.root, target=self.store, apply=False
        )
        self.assertEqual(summary.files, 1)
        self.assertEqual(summary.uploaded, 0)
        self.assertEqual(self.client.uploads, 0)

    def test_apply_is_idempotent_and_verified(self) -> None:
        first = migrate_media_tree(source_root=self.root, target=self.store, apply=True)
        second = migrate_media_tree(source_root=self.root, target=self.store, apply=True)
        self.assertEqual(first.uploaded, 1)
        self.assertEqual(second.uploaded, 0)
        self.assertEqual(second.verified, 1)
        self.assertEqual(self.client.uploads, 1)

    def test_conflicting_remote_object_fails_closed(self) -> None:
        self.client.objects[("course-media", self.key)] = (b"different", {})
        with self.assertRaises(MediaConflict):
            migrate_media_tree(source_root=self.root, target=self.store, apply=True)


if __name__ == "__main__":
    unittest.main()
