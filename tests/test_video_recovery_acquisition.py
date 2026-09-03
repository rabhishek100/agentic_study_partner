"""R2 recovery identifies immutable artifacts and resumes partial downloads."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from video.recovery_acquisition import RecoveryObject, S3YouTubeRecoveryAcquirer


class _Paginator:
    def __init__(self, client) -> None:
        self.client = client

    def paginate(self, *, Bucket, Prefix):
        del Bucket
        yield {
            "Contents": [
                {
                    "Key": key,
                    "Size": len(value),
                    "LastModified": modified,
                }
                for key, (value, modified) in self.client.objects.items()
                if key.startswith(Prefix)
            ]
        }


class _S3:
    def __init__(self, objects) -> None:
        self.objects = objects
        self.ranges = []

    def get_paginator(self, name):
        if name != "list_objects_v2":
            raise AssertionError(name)
        return _Paginator(self)

    def get_object(self, *, Bucket, Key, Range=None):
        del Bucket
        value = self.objects[Key][0]
        if Range:
            self.ranges.append(Range)
            value = value[int(Range.removeprefix("bytes=").removesuffix("-")) :]
        return {"Body": BytesIO(value)}


def _key(owner, namespace, value, extension):
    digest = sha256(value).hexdigest()
    return (
        f"{owner}/canonical/{namespace}/sha256/{digest[:2]}/{digest[2:4]}/"
        f"{digest}{extension}"
    )


class VideoRecoveryAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = uuid4()
        now = datetime.now(UTC)
        self.video = b"course-video-bytes"
        self.extra_video = b"unrelated-cme-video-bytes-that-are-larger"
        self.caption = b"WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n"
        metadata = json.dumps(
            {
                "id": "abcdefghijk",
                "title": "Lecture 1",
                "description": "Course lecture",
                "filesize_approx": len(self.video),
            }
        ).encode()
        self.video_key = _key(self.owner, "videos", self.video, ".mp4")
        self.metadata_key = _key(self.owner, "metadata", metadata, ".json")
        self.caption_key = _key(self.owner, "transcripts", self.caption, ".vtt")
        objects = {
            self.video_key: (self.video, now),
            _key(self.owner, "videos", self.extra_video, ".mp4"): (
                self.extra_video,
                now - timedelta(days=1),
            ),
            self.metadata_key: (metadata, now + timedelta(seconds=1)),
            self.caption_key: (self.caption, now + timedelta(seconds=2)),
        }
        self.client = _S3(objects)
        self.acquirer = S3YouTubeRecoveryAcquirer(
            client=self.client, bucket="recovery", owner_id=self.owner
        )

    def test_inventory_matches_course_video_and_caption_but_ignores_extra_media(self):
        artifacts = self.acquirer.inventory()["abcdefghijk"]

        self.assertEqual(artifacts.video.key, self.video_key)
        self.assertEqual(artifacts.info.key, self.metadata_key)
        self.assertEqual(artifacts.caption.key, self.caption_key)
        self.assertEqual(artifacts.title, "Lecture 1")

    def test_a_lecture_described_twice_does_not_block_the_rest_of_the_course(self):
        """A re-ingested lecture must not make the other lectures unrecoverable.

        The bucket holds two metadata objects for the same YouTube ID, which
        is what a second ingest of one lecture leaves behind. That used to
        raise and abandon the whole inventory, so one duplicated lecture cost
        the entire course. The newest description wins and the other is named.
        """

        now = datetime.now(UTC)
        second_video = b"second-course-video!!"
        second_metadata = json.dumps(
            {
                "id": "bcdefghijkl",
                "title": "Lecture 2",
                "description": "Second lecture",
                "filesize_approx": len(second_video),
            }
        ).encode()
        stale_duplicate = json.dumps(
            {
                "id": "abcdefghijk",
                "title": "Lecture 1 (first pass)",
                "description": "Superseded description",
                "filesize_approx": len(self.video),
            }
        ).encode()

        self.client.objects[_key(self.owner, "videos", second_video, ".mp4")] = (
            second_video,
            now,
        )
        self.client.objects[_key(self.owner, "metadata", second_metadata, ".json")] = (
            second_metadata,
            now + timedelta(seconds=1),
        )
        duplicate_key = _key(self.owner, "metadata", stale_duplicate, ".json")
        self.client.objects[duplicate_key] = (
            stale_duplicate,
            now - timedelta(days=2),
        )

        inventory = self.acquirer.inventory()

        # Both lectures survive, and the duplicate did not consume a video.
        self.assertEqual(sorted(inventory), ["abcdefghijk", "bcdefghijkl"])
        self.assertEqual(inventory["abcdefghijk"].title, "Lecture 1")
        self.assertEqual(inventory["bcdefghijkl"].title, "Lecture 2")
        self.assertEqual(
            [item.key for item in self.acquirer.superseded_metadata],
            [duplicate_key],
        )

    def test_the_newest_description_of_a_lecture_is_the_one_used(self):
        newer = json.dumps(
            {
                "id": "abcdefghijk",
                "title": "Lecture 1 (corrected)",
                "description": "Corrected description",
                "filesize_approx": len(self.video),
            }
        ).encode()
        self.client.objects[_key(self.owner, "metadata", newer, ".json")] = (
            newer,
            datetime.now(UTC) + timedelta(days=1),
        )

        artifacts = self.acquirer.inventory()["abcdefghijk"]

        self.assertEqual(artifacts.title, "Lecture 1 (corrected)")
        self.assertEqual(len(self.acquirer.superseded_metadata), 1)

    def test_download_resumes_a_verified_partial_object(self):
        artifact = RecoveryObject(
            key=self.video_key,
            size_bytes=len(self.video),
            content_hash=sha256(self.video).hexdigest(),
            last_modified=datetime.now(UTC),
        )
        with TemporaryDirectory() as directory:
            target = Path(directory) / "source.mp4"
            partial = target.with_name("source.mp4.part")
            partial.write_bytes(self.video[:7])

            downloaded = self.acquirer._download(artifact, target)

            self.assertEqual(downloaded.read_bytes(), self.video)
            self.assertFalse(partial.exists())
            self.assertEqual(self.client.ranges, ["bytes=7-"])


if __name__ == "__main__":
    unittest.main()
