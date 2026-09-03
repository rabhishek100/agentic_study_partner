"""Book figures live in object storage, addressed by their own content."""

from base64 import b64encode
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from storage.book_images import (
    configured_book_image_store,
    digest,
    extension_for,
    load_figure,
    storage_key,
    store_figure,
)
from video.media_store import FilesystemMediaStore, MediaStoreError


OWNER = uuid4()


class BookImageKeyTests(unittest.TestCase):
    def test_a_key_is_owner_scoped_and_content_addressed(self) -> None:
        content_hash = digest(b"figure bytes")
        key = storage_key(
            owner_id=OWNER, content_hash=content_hash, mime_type="image/jpeg"
        )

        self.assertTrue(key.startswith(f"{OWNER}/canonical/book-images/sha256/"))
        self.assertIn(content_hash, key)
        self.assertTrue(key.endswith(".jpg"))

    def test_the_suffix_follows_the_declared_type(self) -> None:
        self.assertEqual(extension_for("image/png"), ".png")
        self.assertEqual(extension_for("image/JPEG"), ".jpg")
        # An unfamiliar type is stored rather than refused; the row records
        # what it is, and the object is bytes either way.
        self.assertEqual(extension_for("image/heic"), ".bin")

    def test_a_hash_that_is_not_a_digest_is_refused(self) -> None:
        with self.assertRaises(MediaStoreError):
            storage_key(owner_id=OWNER, content_hash="nope", mime_type="image/jpeg")


class BookImageStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = FilesystemMediaStore(Path(self.temporary.name))

    def test_a_figure_round_trips_byte_for_byte(self) -> None:
        payload = b"\xff\xd8\xff\xe0" + b"jpeg-ish" * 64

        key, content_hash, size = store_figure(
            self.store, owner_id=OWNER, payload=payload, mime_type="image/jpeg"
        )
        row = {
            "owner_id": OWNER,
            "storage_key": key,
            "base64_content": None,
        }

        self.assertEqual(size, len(payload))
        self.assertEqual(content_hash, digest(payload))
        self.assertEqual(load_figure(row, store=self.store), payload)

    def test_the_same_figure_twice_is_one_object(self) -> None:
        """A book re-ingested, or two books sharing a diagram, stores it once."""

        payload = b"a repeated publisher badge"

        first, _, _ = store_figure(
            self.store, owner_id=OWNER, payload=payload, mime_type="image/png"
        )
        second, _, _ = store_figure(
            self.store, owner_id=OWNER, payload=payload, mime_type="image/png"
        )

        self.assertEqual(first, second)

    def test_an_empty_figure_is_refused(self) -> None:
        with self.assertRaises(MediaStoreError):
            store_figure(
                self.store, owner_id=OWNER, payload=b"", mime_type="image/jpeg"
            )

    def test_a_row_still_holding_base64_is_read_from_it(self) -> None:
        """Both shapes stay readable for as long as the column survives."""

        payload = b"inline figure"
        row = {
            "owner_id": OWNER,
            "storage_key": None,
            "base64_content": b64encode(payload).decode("ascii"),
        }

        self.assertEqual(load_figure(row, store=self.store), payload)

    def test_a_migrated_row_never_falls_back_to_the_column(self) -> None:
        """A row that has been migrated reads the object, not the leftover copy.

        During the migration both are present and they must agree; if they ever
        do not, the object is the one the row now points at, and silently
        preferring the column would hide that.
        """

        stored = b"the object"
        key, _, _ = store_figure(
            self.store, owner_id=OWNER, payload=stored, mime_type="image/png"
        )
        row = {
            "owner_id": OWNER,
            "storage_key": key,
            "base64_content": b64encode(b"the stale column").decode("ascii"),
        }

        self.assertEqual(load_figure(row, store=self.store), stored)

    def test_a_row_naming_nothing_is_an_error_not_an_empty_image(self) -> None:
        row = {"owner_id": OWNER, "storage_key": None, "base64_content": None}

        with self.assertRaises(MediaStoreError):
            load_figure(row, store=self.store)


class BookImageBackendTests(unittest.TestCase):
    def test_the_video_bucket_is_refused(self) -> None:
        """The whole reason this store exists is to not be that bucket.

        Video's retention sweep lists its bucket and deletes what no video row
        names. Book figures there are exactly that, and relying on the
        authoritativeness guard to notice is not a design.
        """

        with patch.dict(
            os.environ,
            {
                "BOOK_IMAGE_BACKEND": "r2",
                "BOOK_IMAGE_S3_BUCKET": "shared-bucket",
                "VIDEO_S3_BUCKET": "shared-bucket",
                "VIDEO_S3_ENDPOINT": "https://example.invalid",
            },
            clear=False,
        ):
            with self.assertRaises(MediaStoreError) as caught:
                configured_book_image_store()

        self.assertIn("must differ", str(caught.exception))

    def test_r2_without_a_bucket_is_refused_rather_than_inherited(self) -> None:
        with patch.dict(
            os.environ, {"BOOK_IMAGE_BACKEND": "r2", "BOOK_IMAGE_S3_BUCKET": ""},
            clear=False,
        ):
            with self.assertRaises(MediaStoreError):
                configured_book_image_store()

    def test_local_development_needs_no_bucket_at_all(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"BOOK_IMAGE_BACKEND": "filesystem", "BOOK_IMAGE_ROOT": directory},
                clear=False,
            ):
                store = configured_book_image_store()

        self.assertIsInstance(store, FilesystemMediaStore)


if __name__ == "__main__":
    unittest.main()
