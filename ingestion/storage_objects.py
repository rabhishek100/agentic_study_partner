"""Server-side access to the private Supabase Storage source bucket.

Only the API and worker use these helpers, with the service-role key. The key
and every signed URL stay server-side and are never logged.
"""

from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path

import httpx
from psycopg import Connection

from .errors import ErrorCode, IngestionError


REQUEST_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 300.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ObjectInfo:
    """What Storage reports about one stored object."""

    bucket: str
    path: str
    size_bytes: int
    content_type: str | None
    etag: str | None
    version: str | None


@dataclass(frozen=True)
class DownloadResult:
    """The verified result of streaming an object to local disk."""

    path: Path
    size_bytes: int
    sha256: str


def _base_url() -> str:
    configured = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not configured:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE, detail="SUPABASE_URL is not configured"
        )
    return f"{configured}/storage/v1"


def _service_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail="SUPABASE_SERVICE_ROLE_KEY is not configured",
        )
    return key


@contextmanager
def storage_client(timeout: float = REQUEST_TIMEOUT_SECONDS) -> Iterator[httpx.Client]:
    """Yield a configured Storage client. The service key never leaves here."""

    key = _service_key()
    with httpx.Client(
        base_url=_base_url(),
        headers={"Authorization": f"Bearer {key}", "apikey": key},
        timeout=timeout,
    ) as client:
        yield client


def _reports_missing(response: httpx.Response) -> bool:
    """Whether Storage is saying the object does not exist.

    It answers a missing object with 400 and a `not_found` body rather than a
    404, so status alone cannot tell "gone" from "broken". Conflating the two
    makes a permanently missing file look like a transient outage, which the
    pipeline then retries and the interface then tells the reader to retry.
    """

    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return str(body.get("statusCode")) == "404" or body.get("error") == "not_found"


def object_info(bucket: str, path: str) -> ObjectInfo | None:
    """Return stored object metadata, or None when the object does not exist."""

    with storage_client() as client:
        try:
            response = client.get(f"/object/info/{bucket}/{path}")
        except httpx.HTTPError as error:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail=f"object info failed: {error!r}"
            ) from error

    if _reports_missing(response):
        return None
    if response.status_code >= 500:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail=f"object info returned {response.status_code}",
        )
    if response.status_code >= 400:
        raise IngestionError(
            ErrorCode.SOURCE_MISSING,
            detail=f"object info returned {response.status_code}",
        )

    try:
        document = response.json()
    except ValueError as error:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE, detail="object info was not JSON"
        ) from error

    return ObjectInfo(
        bucket=bucket,
        path=path,
        size_bytes=int(document.get("size") or 0),
        content_type=document.get("content_type"),
        etag=document.get("etag"),
        version=document.get("version"),
    )


def object_uploader(connection: Connection, bucket: str, path: str) -> str | None:
    """Return the user id Storage recorded for an upload, if it recorded one.

    Objects written with the service role have no uploader, so callers treat
    ``None`` as "fall back to the owner-scoped path check".
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
) -> DownloadResult:
    """Stream an object to disk, hashing it and enforcing the byte limit.

    The limit is enforced while streaming, so an object that grew past it after
    the API check cannot fill the worker's disk. A partial file is removed.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    written = 0

    try:
        with storage_client(DOWNLOAD_TIMEOUT_SECONDS) as client:
            with client.stream("GET", f"/object/{bucket}/{path}") as response:
                if response.status_code == 404:
                    raise IngestionError(
                        ErrorCode.SOURCE_MISSING,
                        detail="source object no longer exists",
                    )
                if response.status_code >= 400:
                    code = (
                        ErrorCode.STORAGE_UNAVAILABLE
                        if response.status_code >= 500
                        else ErrorCode.SOURCE_MISSING
                    )
                    raise IngestionError(
                        code, detail=f"download returned {response.status_code}"
                    )
                with destination.open("wb") as target:
                    for chunk in response.iter_bytes(DOWNLOAD_CHUNK_BYTES):
                        written += len(chunk)
                        if written > maximum_bytes:
                            raise IngestionError(
                                ErrorCode.SOURCE_TOO_LARGE,
                                detail=(
                                    f"source exceeded {maximum_bytes} bytes "
                                    "while downloading"
                                ),
                            )
                        digest.update(chunk)
                        target.write(chunk)
    except httpx.HTTPError as error:
        destination.unlink(missing_ok=True)
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE, detail=f"download failed: {error!r}"
        ) from error
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise IngestionError(
            ErrorCode.TEMPORARY_DISK_ERROR, detail=f"download write failed: {error!r}"
        ) from error
    except IngestionError:
        destination.unlink(missing_ok=True)
        raise

    return DownloadResult(
        path=destination, size_bytes=written, sha256=digest.hexdigest()
    )


def list_prefix(bucket: str, prefix: str = "", *, limit: int = 100) -> list[str]:
    """List one level of names under ``prefix``.

    Storage returns folders and objects together; folders have no id. Callers
    walk the two levels of ``{owner_id}/{job_id}/original.pdf`` themselves
    rather than asking for a recursive listing, which the API does not offer.
    """

    with storage_client() as client:
        try:
            response = client.post(
                f"/object/list/{bucket}",
                json={"prefix": prefix, "limit": limit},
            )
        except httpx.HTTPError as error:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail=f"list failed: {error!r}"
            ) from error
    if response.status_code >= 400:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail=f"list returned {response.status_code}",
        )
    try:
        return [entry["name"] for entry in response.json() if entry.get("name")]
    except (ValueError, TypeError) as error:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE, detail="list response was not JSON"
        ) from error


def delete_object(bucket: str, path: str) -> bool:
    """Remove one object. Returns False when it was already gone.

    Uses `_reports_missing` for the same reason `object_info` and
    `signed_object_url` do: Storage answers a missing object with 400 and a
    `not_found` body, so a bare status check reads "already gone" as "Storage
    is broken". This function claimed to return False for a deleted object and
    raised instead, which made the retention sweep count one absent file as a
    failed deletion on every pass, forever.
    """

    with storage_client() as client:
        try:
            response = client.delete(f"/object/{bucket}/{path}")
        except httpx.HTTPError as error:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=f"delete of {bucket}/{path} failed: {error!r}",
            ) from error
    if _reports_missing(response):
        return False
    if response.status_code >= 400:
        # The body is the only thing that distinguishes one 400 from another,
        # and a deletion that fails every hour is unfixable without it.
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail=(
                f"delete of {bucket}/{path} returned {response.status_code}: "
                f"{response.text[:200]}"
            ),
        )
    return True


def signed_object_url(bucket: str, path: str, *, expires_in: int = 900) -> str | None:
    """Return a short-lived read URL for one private object.

    The service key signs the URL and never leaves this module; the browser
    receives only a time-limited link. Returns None when the object is gone —
    a ready book whose source was removed by the retention policy is an
    explainable state, not an error.
    """

    if expires_in <= 0:
        raise ValueError("expires_in must be positive")

    with storage_client() as client:
        response = client.post(
            # `_base_url()` already ends in /storage/v1, like every other
            # call in this module.
            f"/object/sign/{bucket}/{path}",
            json={"expiresIn": expires_in},
        )
    if _reports_missing(response):
        return None
    if response.status_code >= 400:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail=f"signing failed with status {response.status_code}",
        )

    signed = response.json().get("signedURL") or response.json().get("signedUrl")
    if not signed:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail="storage returned no signed URL",
        )
    # Storage returns a path already relative to /storage/v1, and
    # `_base_url()` already ends in it — appending it again produced
    # `/storage/v1/storage/v1/...`, which 404s.
    return f"{_base_url()}{signed}" if signed.startswith("/") else signed


def upload_object(
    bucket: str,
    path: str,
    payload: bytes,
    *,
    content_type: str = "application/pdf",
    overwrite: bool = False,
) -> None:
    """Store one object. Used to restore a source the bucket has lost.

    Uploads are normally resumable and come from the browser; this is the
    server-side path for putting a known-good file back where a book already
    expects it.
    """

    with storage_client() as client:
        response = client.post(
            f"/object/{bucket}/{path}",
            content=payload,
            headers={
                "content-type": content_type,
                "x-upsert": "true" if overwrite else "false",
            },
        )
    if response.status_code >= 400:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail=f"upload returned {response.status_code}: {response.text[:200]}",
        )
