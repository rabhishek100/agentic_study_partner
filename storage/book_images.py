"""Where a book's figures live once they are too big for the database.

`image_blocks` held every figure as base64 text inside Postgres, which made
it 52% of the database — 456 MB of JPEG for 4,526 figures, plus the third
that base64 adds over the bytes it encodes. A figure is a large immutable
object addressable by key, which is what an object store is for, and video
media already moved for the same reason and behind the same contract.

So this reuses that contract rather than inventing a second one. What it adds
is a separate bucket. Video's retention sweep lists its whole bucket and
deletes what no video row names; book figures sitting in that bucket would be
exactly that, and while the authoritativeness guard would very likely refuse,
"very likely refused by a safety net" is not where 456 MB of canonical
figures should be. Different bucket, no shared blast radius.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from uuid import UUID

import boto3
from botocore.config import Config

from video.media_store import (
    FilesystemMediaStore,
    MediaStore,
    MediaStoreError,
    S3MediaStore,
)


NAMESPACE = "book-images"
DEFAULT_CACHE_ROOT = Path("data/book-image-cache")
MAXIMUM_IMAGE_BYTES = 32 * 1024 * 1024

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/tiff": ".tif",
}


def extension_for(mime_type: str) -> str:
    """The stored suffix for a figure, defaulting to a neutral one."""

    return _EXTENSIONS.get((mime_type or "").strip().lower(), ".bin")


def storage_key(*, owner_id: str | UUID, content_hash: str, mime_type: str) -> str:
    """Content-addressed and owner-scoped, matching the video key shape.

    Two books that embed the same figure store it once per owner, and a
    re-ingest of the same book writes the same key rather than a second copy.
    """

    if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
        raise MediaStoreError("book image hash must be a sha256 hex digest")
    return (
        f"{owner_id}/canonical/{NAMESPACE}/sha256/"
        f"{content_hash[:2]}/{content_hash[2:4]}/{content_hash}"
        f"{extension_for(mime_type)}"
    )


def digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _s3_setting(name: str, fallback: str) -> str:
    """Book settings first, then video's connection details.

    The two buckets sit in the same account behind the same endpoint, so
    requiring five duplicated variables to name a different bucket would be
    ceremony. Only the bucket has to be stated; the rest is inherited and
    still overridable.
    """

    value = os.getenv(f"BOOK_IMAGE_S3_{name}", "").strip()
    return value or os.getenv(f"VIDEO_S3_{name}", fallback).strip()


def configured_book_image_store() -> MediaStore:
    """Build the selected backend without making local setup require R2."""

    backend = os.getenv("BOOK_IMAGE_BACKEND", "filesystem").strip().lower()
    if backend in {"", "filesystem"}:
        root = os.getenv("BOOK_IMAGE_ROOT", "").strip()
        return FilesystemMediaStore(Path(root) if root else Path("data/book-images"))
    if backend not in {"r2", "s3"}:
        raise MediaStoreError("BOOK_IMAGE_BACKEND must be filesystem, r2, or s3")

    bucket = os.getenv("BOOK_IMAGE_S3_BUCKET", "").strip()
    if not bucket:
        # Named explicitly on purpose: inheriting the video bucket here is the
        # one mistake this module exists to prevent.
        raise MediaStoreError(
            "BOOK_IMAGE_S3_BUCKET is required and must not be the video bucket"
        )
    if bucket == os.getenv("VIDEO_S3_BUCKET", "").strip():
        raise MediaStoreError(
            "BOOK_IMAGE_S3_BUCKET must differ from VIDEO_S3_BUCKET: video's "
            "retention sweep deletes what no video row names, and book figures "
            "in that bucket are exactly that"
        )

    endpoint = _s3_setting("ENDPOINT", "")
    if not endpoint:
        raise MediaStoreError("BOOK_IMAGE_S3_ENDPOINT or VIDEO_S3_ENDPOINT is required")
    cache_root = os.getenv("BOOK_IMAGE_CACHE_ROOT", "").strip()
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=_s3_setting("REGION", "auto") or "auto",
        aws_access_key_id=_s3_setting("ACCESS_KEY_ID", ""),
        aws_secret_access_key=_s3_setting("SECRET_ACCESS_KEY", ""),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )
    return S3MediaStore(
        bucket=bucket,
        client=client,
        cache_root=Path(cache_root) if cache_root else DEFAULT_CACHE_ROOT,
    )


def store_figure(
    store: MediaStore, *, owner_id: str | UUID, payload: bytes, mime_type: str
) -> tuple[str, str, int]:
    """Put one figure in the store. Returns its key, hash and size.

    Content-addressed, so writing the same figure twice is the same object and
    a re-ingest costs nothing. Returns rather than writes the row, because the
    caller owns the transaction the row belongs to.
    """

    if not payload:
        raise MediaStoreError("book image is empty")
    if len(payload) > MAXIMUM_IMAGE_BYTES:
        raise MediaStoreError("book image exceeds the configured limit")

    content_hash = digest(payload)
    key = storage_key(
        owner_id=owner_id, content_hash=content_hash, mime_type=mime_type
    )
    writer = store.writer(
        owner_id=owner_id, storage_key=key, maximum_bytes=MAXIMUM_IMAGE_BYTES
    )
    writer.write(payload)
    writer.finish(expected_size=len(payload))
    return key, content_hash, len(payload)


def load_figure(row, *, store: MediaStore | None = None) -> bytes:
    """The bytes of one figure, from the object its row names.

    Rows carried inline base64 until 2026-09-03 and this read both shapes
    during the migration. The column is gone and `storage_key` is now NOT
    NULL, so there is one place a figure lives and no fallback to a copy that
    no longer exists.
    """

    key = row["storage_key"] if "storage_key" in row.keys() else None
    if not key:
        raise MediaStoreError("book image row names no object")
    selected = store or configured_book_image_store()
    return selected.open_path(owner_id=row["owner_id"], storage_key=key).read_bytes()
