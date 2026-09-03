"""Source-PDF access, as the rest of the application already spells it.

The provider-neutral implementation lives in `source_store.py`. This module
stayed because a dozen call sites — the pipeline, the cleanup sweep, the API's
signing path, deck extraction, four scripts — spell these operations as free
functions, and rewriting all of them in the same change that moved providers
would have made the provider move unreviewable.

So the functions are unchanged in name, signature and error behaviour, and now
delegate to the configured backend. Callers that need to read a *specific*
row's object pass the backend that row records, which is what lets the
migration run one object at a time with both providers live.

New code should prefer `source_store.configured_source_store()` directly.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from psycopg import Connection

from .source_store import (
    DEFAULT_DOWNLOAD_EXPIRY_SECONDS,
    DOWNLOAD_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    DownloadResult,
    ObjectInfo,
    SupabaseSourceStore,
    configured_source_store,
    iter_all_keys,
    store_for_backend,
)


__all__ = [
    "DownloadResult",
    "ObjectInfo",
    "delete_object",
    "download_object",
    "iter_all_keys",
    "list_prefix",
    "object_info",
    "object_uploader",
    "signed_object_url",
    "storage_client",
    "upload_object",
]


@contextmanager
def storage_client(timeout: float = REQUEST_TIMEOUT_SECONDS) -> Iterator[httpx.Client]:
    """A raw Supabase Storage client.

    Only the local development stack and the integration tests that upload
    fixtures still want this. It is deliberately not part of the provider
    interface: nothing provider-neutral can hand back an httpx client.
    """

    with SupabaseSourceStore().client(timeout) as client:
        yield client


def object_info(bucket: str, path: str, *, backend: str | None = None) -> ObjectInfo | None:
    """Stored object metadata, or None when the object does not exist."""

    return store_for_backend(backend).head(bucket, path) if backend else (
        configured_source_store().head(bucket, path)
    )


def object_uploader(connection: Connection, bucket: str, path: str) -> str | None:
    """The user id Supabase Storage recorded for an upload, if it recorded one.

    Objects written with the service role have no uploader, so callers treat
    None as "fall back to the owner-scoped path check". R2 has no equivalent
    notion, which is why the presigned upload binds owner and job as object
    metadata instead — see `source_store.owner_metadata`.
    """

    row = connection.execute(
        "select owner_id from storage.objects where bucket_id = %s and name = %s",
        (bucket, path),
    ).fetchone()
    if row is None:
        return None
    return row["owner_id"]


def download_object(
    bucket: str,
    path: str,
    destination: Path,
    *,
    maximum_bytes: int,
    backend: str | None = None,
) -> DownloadResult:
    """Stream an object to disk, hashing it and enforcing the byte limit."""

    store = store_for_backend(backend) if backend else configured_source_store()
    return store.download(bucket, path, destination, maximum_bytes=maximum_bytes)


def list_prefix(
    bucket: str, prefix: str = "", *, limit: int = 100, backend: str | None = None
) -> list[str]:
    """One page of names under `prefix`.

    Kept for the callers that genuinely want a single page. Anything sweeping a
    whole bucket must use `iter_all_keys`, which pages to exhaustion — a sweep
    that stops at the first page treats every later object as an orphan.
    """

    store = store_for_backend(backend) if backend else configured_source_store()
    return list(store.list(bucket, prefix, limit=limit).keys)


def delete_object(bucket: str, path: str, *, backend: str | None = None) -> bool:
    """Remove one object. Returns False when it was already gone."""

    store = store_for_backend(backend) if backend else configured_source_store()
    return store.delete(bucket, path)


def signed_object_url(
    bucket: str,
    path: str,
    *,
    expires_in: int = DEFAULT_DOWNLOAD_EXPIRY_SECONDS,
    backend: str | None = None,
) -> str | None:
    """A short-lived read URL for one private object.

    Returns None when the object is gone — a ready book whose source the
    retention policy removed is an explainable state, not an error.
    """

    store = store_for_backend(backend) if backend else configured_source_store()
    return store.presigned_get(bucket, path, expires_in=expires_in)


def upload_object(
    bucket: str,
    path: str,
    payload: bytes,
    *,
    content_type: str = "application/pdf",
    overwrite: bool = False,
    backend: str | None = None,
) -> None:
    """Store one object. Used to restore a source the bucket has lost."""

    store = store_for_backend(backend) if backend else configured_source_store()
    store.put(bucket, path, payload, content_type=content_type, overwrite=overwrite)
