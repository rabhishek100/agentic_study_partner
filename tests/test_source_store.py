"""The provider-neutral source store, exercised against a fake S3 client.

R2 is the one backend with no local equivalent, so it is the one that would
otherwise only ever be tested in production. The fake here implements the
handful of S3 operations the store actually calls, including the error shapes
botocore raises, so the mapping onto this application's error taxonomy —
`SOURCE_MISSING` versus `STORAGE_UNAVAILABLE`, which decides whether the
pipeline retries a job forever or fails it once — is asserted rather than
assumed.

Presigning is checked for what it *binds*, not for the exact URL: an unbound
presigned PUT is a write credential for the whole bucket.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from botocore.exceptions import ClientError, EndpointConnectionError

from ingestion.errors import ErrorCode, IngestionError
from ingestion.source_store import (
    FILESYSTEM_BACKEND,
    R2_BACKEND,
    SUPABASE_BACKEND,
    FilesystemSourceStore,
    R2SourceStore,
    configured_backend,
    iter_all_keys,
    owner_metadata,
    source_bucket,
    store_for_backend,
)


def _client_error(code: str, status: int, operation: str = "HeadObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeS3:
    """Just enough S3 to exercise the store, plus injectable failure."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.presigned: list[dict] = []
        self.fail_with: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def put(self, bucket: str, key: str, payload: bytes, **extra) -> None:
        self.objects[(bucket, key)] = {
            "Body": payload,
            "ContentType": extra.get("ContentType", "application/pdf"),
            "Metadata": extra.get("Metadata", {}),
        }

    # --- the operations the store calls -----------------------------------

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        self._maybe_fail()
        stored = self.objects.get((Bucket, Key))
        if stored is None:
            raise _client_error("404", 404)
        return {
            "ContentLength": len(stored["Body"]),
            "ContentType": stored["ContentType"],
            "ETag": '"abc123"',
            "Metadata": stored["Metadata"],
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        self._maybe_fail()
        stored = self.objects.get((Bucket, Key))
        if stored is None:
            raise _client_error("NoSuchKey", 404, "GetObject")
        return {"Body": FakeBody(stored["Body"])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **extra) -> dict:
        self._maybe_fail()
        self.put(Bucket, Key, Body, **extra)
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self._maybe_fail()
        self.objects.pop((Bucket, Key), None)
        return {}

    def list_objects_v2(self, **request) -> dict:
        self._maybe_fail()
        bucket = request["Bucket"]
        prefix = request.get("Prefix", "")
        limit = request.get("MaxKeys", 1000)
        token = request.get("ContinuationToken")
        keys = sorted(k for (b, k) in self.objects if b == bucket and k.startswith(prefix))
        start = int(token) if token else 0
        window = keys[start : start + limit]
        truncated = start + limit < len(keys)
        result: dict = {"Contents": [{"Key": k} for k in window], "IsTruncated": truncated}
        if truncated:
            result["NextContinuationToken"] = str(start + limit)
        return result

    def generate_presigned_url(self, operation: str, *, Params: dict, ExpiresIn: int) -> str:
        self._maybe_fail()
        self.presigned.append(
            {"operation": operation, "params": Params, "expires_in": ExpiresIn}
        )
        return f"https://fake.r2/{Params['Bucket']}/{Params['Key']}?sig=x"


class R2SourceStoreTests(unittest.TestCase):
    BUCKET = "book-sources-prod"
    KEY = "owner-1/job-1/original.pdf"

    def setUp(self) -> None:
        self.client = FakeS3()
        self.store = R2SourceStore(client=self.client)

    def test_the_backend_names_itself(self):
        self.assertEqual(self.store.backend, R2_BACKEND)

    def test_a_missing_object_heads_as_none_not_as_an_outage(self):
        """`None` means gone. Raising here would make the pipeline retry forever."""

        self.assertIsNone(self.store.head(self.BUCKET, self.KEY))

    def test_head_reports_size_type_and_metadata(self):
        self.client.put(
            self.BUCKET,
            self.KEY,
            b"%PDF-1.7 body",
            ContentType="application/pdf",
            Metadata={"owner-id": "owner-1", "job-id": "job-1"},
        )
        info = self.store.head(self.BUCKET, self.KEY)
        self.assertIsNotNone(info)
        self.assertEqual(info.size_bytes, len(b"%PDF-1.7 body"))
        self.assertEqual(info.content_type, "application/pdf")
        self.assertEqual(info.etag, "abc123")
        self.assertEqual(info.metadata["owner-id"], "owner-1")

    def test_an_unreachable_endpoint_is_an_outage_not_a_missing_object(self):
        """The distinction the taxonomy exists for, in the direction that matters."""

        self.client.fail_with = EndpointConnectionError(endpoint_url="https://fake.r2")
        with self.assertRaises(IngestionError) as caught:
            self.store.head(self.BUCKET, self.KEY)
        self.assertEqual(caught.exception.code, ErrorCode.STORAGE_UNAVAILABLE)

    def test_a_download_streams_hashes_and_reports_size(self):
        payload = b"x" * 4096
        self.client.put(self.BUCKET, self.KEY, payload)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "original.pdf"
            result = self.store.download(
                self.BUCKET, self.KEY, destination, maximum_bytes=10 * 1024
            )
        self.assertEqual(result.size_bytes, len(payload))
        self.assertEqual(result.sha256, sha256(payload).hexdigest())

    def test_a_download_of_a_missing_object_is_source_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(IngestionError) as caught:
                self.store.download(
                    self.BUCKET,
                    self.KEY,
                    Path(directory) / "x.pdf",
                    maximum_bytes=1024,
                )
        self.assertEqual(caught.exception.code, ErrorCode.SOURCE_MISSING)

    def test_the_byte_limit_is_enforced_while_streaming(self):
        """An object that grew after the API checked it must not fill the disk."""

        self.client.put(self.BUCKET, self.KEY, b"y" * 8192)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "big.pdf"
            with self.assertRaises(IngestionError) as caught:
                self.store.download(
                    self.BUCKET, self.KEY, destination, maximum_bytes=1024
                )
            self.assertEqual(caught.exception.code, ErrorCode.SOURCE_TOO_LARGE)
            # The partial file is not left behind for a later pass to trust.
            self.assertFalse(destination.exists())

    def test_a_put_refuses_to_overwrite_unless_told_to(self):
        self.store.put(self.BUCKET, self.KEY, b"first")
        with self.assertRaises(IngestionError):
            self.store.put(self.BUCKET, self.KEY, b"second")
        self.store.put(self.BUCKET, self.KEY, b"second", overwrite=True)
        self.assertEqual(self.client.objects[(self.BUCKET, self.KEY)]["Body"], b"second")

    def test_a_delete_reports_whether_anything_was_there(self):
        """False rather than raising, or the sweep counts an absent file as a
        failed deletion on every pass, forever."""

        self.assertFalse(self.store.delete(self.BUCKET, self.KEY))
        self.store.put(self.BUCKET, self.KEY, b"body")
        self.assertTrue(self.store.delete(self.BUCKET, self.KEY))

    def test_a_listing_pages_to_exhaustion(self):
        """The property a sweep depends on: no object is invisible past page one."""

        for index in range(250):
            self.client.put(self.BUCKET, f"owner-1/job-{index:03d}/original.pdf", b"z")
        keys = list(iter_all_keys(self.store, self.BUCKET, "owner-1/", page_size=100))
        self.assertEqual(len(keys), 250)
        self.assertEqual(len(set(keys)), 250)

    def test_a_listing_respects_its_prefix(self):
        self.client.put(self.BUCKET, "owner-1/job-1/original.pdf", b"a")
        self.client.put(self.BUCKET, "owner-2/job-1/original.pdf", b"b")
        keys = list(iter_all_keys(self.store, self.BUCKET, "owner-1/"))
        self.assertEqual(keys, ["owner-1/job-1/original.pdf"])

    def test_a_signed_get_of_a_missing_object_is_none(self):
        """A presigned URL is signed offline and would happily point at nothing."""

        self.assertIsNone(self.store.presigned_get(self.BUCKET, self.KEY))

    def test_a_signed_get_names_exactly_one_object(self):
        self.client.put(self.BUCKET, self.KEY, b"body")
        url = self.store.presigned_get(self.BUCKET, self.KEY, expires_in=300)
        self.assertIsNotNone(url)
        signed = self.client.presigned[-1]
        self.assertEqual(signed["operation"], "get_object")
        self.assertEqual(signed["params"]["Bucket"], self.BUCKET)
        self.assertEqual(signed["params"]["Key"], self.KEY)
        self.assertEqual(signed["expires_in"], 300)

    def test_a_signed_put_binds_bucket_key_type_and_expiry(self):
        """Each of these is part of the signature. Without them the URL is a
        write credential for the whole bucket."""

        upload = self.store.presigned_put(
            self.BUCKET,
            self.KEY,
            expires_in=600,
            metadata=owner_metadata("owner-1", "job-1"),
        )
        signed = self.client.presigned[-1]
        self.assertEqual(signed["operation"], "put_object")
        self.assertEqual(signed["params"]["Bucket"], self.BUCKET)
        self.assertEqual(signed["params"]["Key"], self.KEY)
        self.assertEqual(signed["params"]["ContentType"], "application/pdf")
        self.assertEqual(signed["expires_in"], 600)
        self.assertEqual(signed["params"]["Metadata"]["owner-id"], "owner-1")
        self.assertEqual(signed["params"]["Metadata"]["job-id"], "job-1")

        self.assertEqual(upload.method, "PUT")
        # The browser must send exactly what was signed, or the PUT is rejected.
        self.assertEqual(upload.headers["content-type"], "application/pdf")
        self.assertEqual(upload.headers["x-amz-meta-owner-id"], "owner-1")
        self.assertEqual(upload.headers["x-amz-meta-job-id"], "job-1")

    def test_a_non_positive_expiry_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.presigned_put(self.BUCKET, self.KEY, expires_in=0)
        with self.assertRaises(ValueError):
            self.store.presigned_get(self.BUCKET, self.KEY, expires_in=-1)


class FilesystemSourceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FilesystemSourceStore(Path(self.directory.name))

    def test_a_round_trip_preserves_bytes_and_hash(self):
        self.store.put("book-sources", "o/j/original.pdf", b"%PDF-1.4 hello")
        info = self.store.head("book-sources", "o/j/original.pdf")
        self.assertEqual(info.size_bytes, 14)
        with tempfile.TemporaryDirectory() as out:
            result = self.store.download(
                "book-sources",
                "o/j/original.pdf",
                Path(out) / "copy.pdf",
                maximum_bytes=1024,
            )
        self.assertEqual(result.size_bytes, 14)

    def test_a_key_cannot_escape_its_bucket(self):
        with self.assertRaises(IngestionError):
            self.store.head("book-sources", "../../etc/passwd")

    def test_it_refuses_to_pretend_it_can_sign(self):
        """A `file://` URL a browser will not fetch is worse than a clear error."""

        with self.assertRaises(IngestionError):
            self.store.presigned_get("book-sources", "o/j/original.pdf")
        with self.assertRaises(IngestionError):
            self.store.presigned_put("book-sources", "o/j/original.pdf")


class ConfigurationTests(unittest.TestCase):
    def environment(self, **overrides):
        base = {
            "SOURCE_STORAGE_BACKEND": "",
            "INGESTION_SOURCE_BUCKET": "",
            "VIDEO_S3_BUCKET": "",
            "BOOK_IMAGE_S3_BUCKET": "",
        }
        base.update(overrides)
        return patch.dict(os.environ, base)

    def test_the_default_backend_is_supabase(self):
        with self.environment():
            self.assertEqual(configured_backend(), SUPABASE_BACKEND)

    def test_an_unknown_backend_is_refused(self):
        with self.environment(SOURCE_STORAGE_BACKEND="gcs"):
            with self.assertRaises(IngestionError):
                configured_backend()

    def test_the_source_bucket_must_not_be_the_video_bucket(self):
        """The mistake this whole module exists to prevent.

        Video's retention sweep deletes every object in its bucket that no
        video row names. Source PDFs there are precisely that.
        """

        with self.environment(
            SOURCE_STORAGE_BACKEND="r2",
            INGESTION_SOURCE_BUCKET="shared-bucket",
            VIDEO_S3_BUCKET="shared-bucket",
        ):
            with self.assertRaises(IngestionError) as caught:
                source_bucket()
            self.assertIn("VIDEO_S3_BUCKET", str(caught.exception.detail))

    def test_the_source_bucket_must_not_be_the_figure_bucket(self):
        with self.environment(
            SOURCE_STORAGE_BACKEND="r2",
            INGESTION_SOURCE_BUCKET="shared-bucket",
            BOOK_IMAGE_S3_BUCKET="shared-bucket",
        ):
            with self.assertRaises(IngestionError):
                source_bucket()

    def test_a_distinct_bucket_is_accepted(self):
        with self.environment(
            SOURCE_STORAGE_BACKEND="r2",
            INGESTION_SOURCE_BUCKET="book-sources-prod",
            VIDEO_S3_BUCKET="video-media-prod",
            BOOK_IMAGE_S3_BUCKET="book-media-prod",
        ):
            self.assertEqual(source_bucket(), "book-sources-prod")

    def test_a_row_is_read_through_the_backend_it_records(self):
        """What makes the migration row-by-row: both providers live at once."""

        with self.environment(SOURCE_STORAGE_ROOT=self.__class__.__name__):
            self.assertEqual(
                store_for_backend(FILESYSTEM_BACKEND).backend, FILESYSTEM_BACKEND
            )
            self.assertEqual(
                store_for_backend(SUPABASE_BACKEND).backend, SUPABASE_BACKEND
            )
            # A row written before the column existed reads as Supabase.
            self.assertEqual(store_for_backend(None).backend, SUPABASE_BACKEND)


if __name__ == "__main__":
    unittest.main()
