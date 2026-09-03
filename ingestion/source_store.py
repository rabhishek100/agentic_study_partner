"""Where a book's source PDF lives, without naming a provider to say it.

`storage_objects.py` spoke Supabase Storage's HTTP API directly — its bearer
header, its habit of answering a missing object with 400 and a `not_found`
body, its `/object/sign/` endpoint. Every caller inherited those assumptions,
so "move the PDFs to R2" was not a configuration change.

This is the seam. One interface, three implementations, and a `backend` string
recorded on every row that names an object, so the migration can move one
object at a time and stay reversible:

  * `supabase` — the Storage HTTP API. Still what the local development stack
    and the integration tests use, and the read path for the legacy hosted
    project during migration and rollback.
  * `r2` — Cloudflare R2 over its S3-compatible API. Production.
  * `filesystem` — a directory. For tests that want neither.

The error taxonomy is deliberately unchanged. `IngestionError` with
`SOURCE_MISSING` versus `STORAGE_UNAVAILABLE` is what decides whether the
pipeline retries a job or fails it permanently, and conflating "gone" with
"broken" is what made a missing file look transient and told the reader to try
again forever. Each backend maps its own vocabulary onto that taxonomy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import shutil
from typing import Protocol
from uuid import UUID

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .errors import ErrorCode, IngestionError


REQUEST_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 300.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

# Ten minutes: long enough for a 50 MB upload on a poor connection, short
# enough that a leaked URL is not a durable credential.
DEFAULT_UPLOAD_EXPIRY_SECONDS = 600
DEFAULT_DOWNLOAD_EXPIRY_SECONDS = 900

SOURCE_CONTENT_TYPE = "application/pdf"

# Recorded on the presigned PUT and re-checked on completion. The key already
# encodes both and is reserved server-side, so this is a second binding rather
# than the only one — an object written to the right key by the wrong flow
# still fails the check.
OWNER_METADATA_KEY = "owner-id"
JOB_METADATA_KEY = "job-id"

SUPABASE_BACKEND = "supabase"
R2_BACKEND = "r2"
FILESYSTEM_BACKEND = "filesystem"
KNOWN_BACKENDS = (SUPABASE_BACKEND, R2_BACKEND, FILESYSTEM_BACKEND)


@dataclass(frozen=True)
class ObjectInfo:
    """What a store reports about one stored object."""

    bucket: str
    path: str
    size_bytes: int
    content_type: str | None
    etag: str | None
    version: str | None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadResult:
    """The verified result of streaming an object to local disk."""

    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ListPage:
    """One page of a listing. `cursor` is None when the listing is complete.

    Paginated because Supabase's list caps at a page and R2's caps at 1,000,
    and a cleanup sweep that silently saw only the first page would treat
    every object beyond it as absent.
    """

    keys: tuple[str, ...]
    cursor: str | None


@dataclass(frozen=True)
class PresignedUpload:
    """A single-object, content-type-bound, short-lived write credential."""

    url: str
    method: str
    headers: dict[str, str]
    expires_in: int
    bucket: str
    path: str


class SourceObjectStore(Protocol):
    """The operations the application needs against source PDFs."""

    backend: str

    def head(self, bucket: str, path: str) -> ObjectInfo | None: ...

    def download(
        self, bucket: str, path: str, destination: Path, *, maximum_bytes: int
    ) -> DownloadResult: ...

    def put(
        self,
        bucket: str,
        path: str,
        payload: bytes,
        *,
        content_type: str = SOURCE_CONTENT_TYPE,
        overwrite: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None: ...

    def delete(self, bucket: str, path: str) -> bool: ...

    def list(
        self, bucket: str, prefix: str = "", *, limit: int = 100, cursor: str | None = None
    ) -> ListPage: ...

    def presigned_get(
        self, bucket: str, path: str, *, expires_in: int = DEFAULT_DOWNLOAD_EXPIRY_SECONDS
    ) -> str | None: ...

    def presigned_put(
        self,
        bucket: str,
        path: str,
        *,
        expires_in: int = DEFAULT_UPLOAD_EXPIRY_SECONDS,
        content_type: str = SOURCE_CONTENT_TYPE,
        metadata: dict[str, str] | None = None,
    ) -> PresignedUpload: ...


def iter_all_keys(
    store: SourceObjectStore, bucket: str, prefix: str = "", *, page_size: int = 100
) -> Iterator[str]:
    """Walk a complete listing, page by page.

    The only supported way to enumerate a bucket. A sweep that reads one page
    and stops sees every later object as an orphan, which is the shape of an
    accident that deletes a corpus.
    """

    cursor: str | None = None
    seen = 0
    while True:
        page = store.list(bucket, prefix, limit=page_size, cursor=cursor)
        for key in page.keys:
            seen += 1
            yield key
        if page.cursor is None:
            return
        cursor = page.cursor


# ---------------------------------------------------------------------------
# Supabase Storage
# ---------------------------------------------------------------------------


class SupabaseSourceStore:
    """The Storage HTTP API. Local development, tests, and the legacy read path.

    Configured from `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` by default, and
    from an explicit URL and key when used to read the legacy hosted project
    during migration while the runtime points somewhere else entirely.
    """

    backend = SUPABASE_BACKEND

    def __init__(self, *, url: str | None = None, service_key: str | None = None) -> None:
        self._url = (url or "").strip().rstrip("/") or None
        self._service_key = (service_key or "").strip() or None

    def _base_url(self) -> str:
        configured = self._url or os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if not configured:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail="SUPABASE_URL is not configured"
            )
        return f"{configured}/storage/v1"

    def _key(self) -> str:
        key = self._service_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not key:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail="SUPABASE_SERVICE_ROLE_KEY is not configured",
            )
        return key

    @contextmanager
    def client(self, timeout: float = REQUEST_TIMEOUT_SECONDS) -> Iterator[httpx.Client]:
        """Yield a configured Storage client. The service key never leaves here."""

        key = self._key()
        with httpx.Client(
            base_url=self._base_url(),
            headers={"Authorization": f"Bearer {key}", "apikey": key},
            timeout=timeout,
        ) as client:
            yield client

    @staticmethod
    def _reports_missing(response: httpx.Response) -> bool:
        """Whether Storage is saying the object does not exist.

        It answers a missing object with 400 and a `not_found` body rather than
        a 404, so status alone cannot tell "gone" from "broken". Conflating the
        two makes a permanently missing file look like a transient outage,
        which the pipeline then retries and the interface then tells the reader
        to retry.
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

    def head(self, bucket: str, path: str) -> ObjectInfo | None:
        with self.client() as client:
            try:
                response = client.get(f"/object/info/{bucket}/{path}")
            except httpx.HTTPError as error:
                raise IngestionError(
                    ErrorCode.STORAGE_UNAVAILABLE, detail=f"object info failed: {error!r}"
                ) from error

        if self._reports_missing(response):
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
            metadata={},
        )

    def download(
        self, bucket: str, path: str, destination: Path, *, maximum_bytes: int
    ) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        written = 0
        try:
            with self.client(DOWNLOAD_TIMEOUT_SECONDS) as client:
                with client.stream("GET", f"/object/{bucket}/{path}") as response:
                    if self._reports_missing(response):
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
                            # Enforced while streaming, so an object that grew
                            # past the limit after the API checked it cannot
                            # fill the worker's disk.
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

        return DownloadResult(path=destination, size_bytes=written, sha256=digest.hexdigest())

    def put(
        self,
        bucket: str,
        path: str,
        payload: bytes,
        *,
        content_type: str = SOURCE_CONTENT_TYPE,
        overwrite: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        with self.client() as client:
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

    def delete(self, bucket: str, path: str) -> bool:
        with self.client() as client:
            try:
                response = client.delete(f"/object/{bucket}/{path}")
            except httpx.HTTPError as error:
                raise IngestionError(
                    ErrorCode.STORAGE_UNAVAILABLE,
                    detail=f"delete of {bucket}/{path} failed: {error!r}",
                ) from error
        if self._reports_missing(response):
            return False
        if response.status_code >= 400:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=(
                    f"delete of {bucket}/{path} returned {response.status_code}: "
                    f"{response.text[:200]}"
                ),
            )
        return True

    def list(
        self, bucket: str, prefix: str = "", *, limit: int = 100, cursor: str | None = None
    ) -> ListPage:
        """One level of names under `prefix`.

        Storage returns folders and objects together and offers no recursive
        listing, so callers walk the two levels of `{owner}/{job}/original.pdf`
        themselves. Paged with an offset, which is what this API provides.
        """

        offset = int(cursor) if cursor else 0
        with self.client() as client:
            try:
                response = client.post(
                    f"/object/list/{bucket}",
                    json={"prefix": prefix, "limit": limit, "offset": offset},
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
            entries = [entry["name"] for entry in response.json() if entry.get("name")]
        except (ValueError, TypeError) as error:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail="list response was not JSON"
            ) from error
        # A short page means the listing is done; a full one might not be.
        next_cursor = str(offset + len(entries)) if len(entries) == limit else None
        return ListPage(keys=tuple(entries), cursor=next_cursor)

    def presigned_get(
        self, bucket: str, path: str, *, expires_in: int = DEFAULT_DOWNLOAD_EXPIRY_SECONDS
    ) -> str | None:
        if expires_in <= 0:
            raise ValueError("expires_in must be positive")
        with self.client() as client:
            response = client.post(
                f"/object/sign/{bucket}/{path}", json={"expiresIn": expires_in}
            )
        if self._reports_missing(response):
            return None
        if response.status_code >= 400:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=f"signing failed with status {response.status_code}",
            )
        body = response.json()
        signed = body.get("signedURL") or body.get("signedUrl")
        if not signed:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail="storage returned no signed URL"
            )
        # Storage returns a path already relative to /storage/v1, and
        # `_base_url()` already ends in it — appending it again produced
        # `/storage/v1/storage/v1/...`, which 404s.
        return f"{self._base_url()}{signed}" if signed.startswith("/") else signed

    def presigned_put(
        self,
        bucket: str,
        path: str,
        *,
        expires_in: int = DEFAULT_UPLOAD_EXPIRY_SECONDS,
        content_type: str = SOURCE_CONTENT_TYPE,
        metadata: dict[str, str] | None = None,
    ) -> PresignedUpload:
        """Storage's own upload-signing endpoint, used by the local stack."""

        with self.client() as client:
            response = client.post(f"/object/upload/sign/{bucket}/{path}", json={})
        if response.status_code >= 400:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=f"upload signing returned {response.status_code}",
            )
        signed = response.json().get("url")
        if not signed:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail="storage returned no upload URL"
            )
        url = f"{self._base_url()}{signed}" if signed.startswith("/") else signed
        return PresignedUpload(
            url=url,
            method="PUT",
            headers={"content-type": content_type},
            expires_in=expires_in,
            bucket=bucket,
            path=path,
        )


# ---------------------------------------------------------------------------
# Cloudflare R2
# ---------------------------------------------------------------------------


class R2SourceStore:
    """Cloudflare R2 over its S3-compatible API. Production.

    A dedicated bucket, never the video or book-figure one. Video's retention
    sweep deletes what no video row names, and source PDFs living in that
    bucket would be precisely that; the check is in `configured_source_store`
    rather than here so it fires at configuration time.
    """

    backend = R2_BACKEND

    def __init__(self, *, client, cache_root: Path | None = None) -> None:
        self._client = client
        self._cache_root = cache_root

    @staticmethod
    def _missing(error: ClientError) -> bool:
        code = str(error.response.get("Error", {}).get("Code", ""))
        status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    @staticmethod
    def _unavailable(error: Exception, action: str) -> IngestionError:
        return IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE, detail=f"{action} failed: {type(error).__name__}"
        )

    def head(self, bucket: str, path: str) -> ObjectInfo | None:
        try:
            response = self._client.head_object(Bucket=bucket, Key=path)
        except ClientError as error:
            if self._missing(error):
                return None
            raise self._unavailable(error, "object head") from error
        except BotoCoreError as error:
            raise self._unavailable(error, "object head") from error
        return ObjectInfo(
            bucket=bucket,
            path=path,
            size_bytes=int(response.get("ContentLength") or 0),
            content_type=response.get("ContentType"),
            etag=(response.get("ETag") or "").strip('"') or None,
            version=response.get("VersionId"),
            metadata={str(k).lower(): str(v) for k, v in (response.get("Metadata") or {}).items()},
        )

    def download(
        self, bucket: str, path: str, destination: Path, *, maximum_bytes: int
    ) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        written = 0
        try:
            response = self._client.get_object(Bucket=bucket, Key=path)
        except ClientError as error:
            if self._missing(error):
                raise IngestionError(
                    ErrorCode.SOURCE_MISSING, detail="source object no longer exists"
                ) from error
            raise self._unavailable(error, "download") from error
        except BotoCoreError as error:
            raise self._unavailable(error, "download") from error

        body = response["Body"]
        try:
            with destination.open("wb") as target:
                while True:
                    chunk = body.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > maximum_bytes:
                        raise IngestionError(
                            ErrorCode.SOURCE_TOO_LARGE,
                            detail=f"source exceeded {maximum_bytes} bytes while downloading",
                        )
                    digest.update(chunk)
                    target.write(chunk)
        except OSError as error:
            destination.unlink(missing_ok=True)
            raise IngestionError(
                ErrorCode.TEMPORARY_DISK_ERROR, detail=f"download write failed: {error!r}"
            ) from error
        except IngestionError:
            destination.unlink(missing_ok=True)
            raise
        finally:
            body.close()

        return DownloadResult(path=destination, size_bytes=written, sha256=digest.hexdigest())

    def put(
        self,
        bucket: str,
        path: str,
        payload: bytes,
        *,
        content_type: str = SOURCE_CONTENT_TYPE,
        overwrite: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if not overwrite and self.head(bucket, path) is not None:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=f"object already exists at {bucket}/{path}",
            )
        try:
            self._client.put_object(
                Bucket=bucket,
                Key=path,
                Body=payload,
                ContentType=content_type,
                Metadata={k: str(v) for k, v in (metadata or {}).items()},
            )
        except (ClientError, BotoCoreError) as error:
            raise self._unavailable(error, "upload") from error

    def delete(self, bucket: str, path: str) -> bool:
        existing = self.head(bucket, path)
        if existing is None:
            return False
        try:
            self._client.delete_object(Bucket=bucket, Key=path)
        except (ClientError, BotoCoreError) as error:
            raise self._unavailable(error, f"delete of {bucket}/{path}") from error
        return True

    def list(
        self, bucket: str, prefix: str = "", *, limit: int = 100, cursor: str | None = None
    ) -> ListPage:
        """A flat, recursive listing — unlike Supabase's, which is one level.

        Callers that want the two-level `{owner}/{job}/` shape derive it from
        the keys rather than from the listing, which is why the source cleanup
        sweep works the same against both backends.
        """

        request: dict[str, object] = {"Bucket": bucket, "MaxKeys": limit}
        if prefix:
            request["Prefix"] = prefix
        if cursor:
            request["ContinuationToken"] = cursor
        try:
            response = self._client.list_objects_v2(**request)
        except (ClientError, BotoCoreError) as error:
            raise self._unavailable(error, "list") from error
        keys = tuple(str(item["Key"]) for item in response.get("Contents", []))
        next_cursor = (
            str(response.get("NextContinuationToken"))
            if response.get("IsTruncated") and response.get("NextContinuationToken")
            else None
        )
        return ListPage(keys=keys, cursor=next_cursor)

    def presigned_get(
        self, bucket: str, path: str, *, expires_in: int = DEFAULT_DOWNLOAD_EXPIRY_SECONDS
    ) -> str | None:
        if expires_in <= 0:
            raise ValueError("expires_in must be positive")
        # A presigned GET is signed offline and would happily point at nothing,
        # so absence is established before one is handed out. A ready book
        # whose source retention removed is an explainable state, not an error.
        if self.head(bucket, path) is None:
            return None
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": path},
                ExpiresIn=expires_in,
            )
        except (ClientError, BotoCoreError) as error:
            raise self._unavailable(error, "signing") from error

    def presigned_put(
        self,
        bucket: str,
        path: str,
        *,
        expires_in: int = DEFAULT_UPLOAD_EXPIRY_SECONDS,
        content_type: str = SOURCE_CONTENT_TYPE,
        metadata: dict[str, str] | None = None,
    ) -> PresignedUpload:
        """A write credential for exactly one object.

        Bound to the bucket, the key, the content type and a short expiry, and
        to the owner/job metadata when given: every one of those is part of the
        signature, so a holder cannot retarget the URL at another key, upload
        something that is not a PDF, or strip the ownership markers. It is
        still a bearer credential, which is why the expiry is minutes.
        """

        if expires_in <= 0:
            raise ValueError("expires_in must be positive")
        params: dict[str, object] = {
            "Bucket": bucket,
            "Key": path,
            "ContentType": content_type,
        }
        headers = {"content-type": content_type}
        if metadata:
            params["Metadata"] = {k: str(v) for k, v in metadata.items()}
            headers.update({f"x-amz-meta-{k}": str(v) for k, v in metadata.items()})
        try:
            url = self._client.generate_presigned_url(
                "put_object", Params=params, ExpiresIn=expires_in
            )
        except (ClientError, BotoCoreError) as error:
            raise self._unavailable(error, "upload signing") from error
        return PresignedUpload(
            url=url,
            method="PUT",
            headers=headers,
            expires_in=expires_in,
            bucket=bucket,
            path=path,
        )


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


class FilesystemSourceStore:
    """A directory. For tests and offline work; never production.

    It cannot presign, because there is nothing to presign against — callers
    that need a URL must be configured with a real backend, and saying so
    plainly beats returning a `file://` URL a browser will not fetch.
    """

    backend = FILESYSTEM_BACKEND

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, bucket: str, path: str) -> Path:
        candidate = (self._root / bucket / path).resolve()
        base = (self._root / bucket).resolve()
        if not str(candidate).startswith(str(base)):
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE, detail="object path escapes its bucket"
            )
        return candidate

    def head(self, bucket: str, path: str) -> ObjectInfo | None:
        target = self._path(bucket, path)
        if not target.is_file():
            return None
        return ObjectInfo(
            bucket=bucket,
            path=path,
            size_bytes=target.stat().st_size,
            content_type=SOURCE_CONTENT_TYPE,
            etag=None,
            version=None,
            metadata={},
        )

    def download(
        self, bucket: str, path: str, destination: Path, *, maximum_bytes: int
    ) -> DownloadResult:
        source = self._path(bucket, path)
        if not source.is_file():
            raise IngestionError(
                ErrorCode.SOURCE_MISSING, detail="source object no longer exists"
            )
        if source.stat().st_size > maximum_bytes:
            raise IngestionError(
                ErrorCode.SOURCE_TOO_LARGE,
                detail=f"source exceeded {maximum_bytes} bytes",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        digest = sha256()
        with destination.open("rb") as handle:
            for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_BYTES), b""):
                digest.update(chunk)
        return DownloadResult(
            path=destination,
            size_bytes=destination.stat().st_size,
            sha256=digest.hexdigest(),
        )

    def put(
        self,
        bucket: str,
        path: str,
        payload: bytes,
        *,
        content_type: str = SOURCE_CONTENT_TYPE,
        overwrite: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        target = self._path(bucket, path)
        if target.exists() and not overwrite:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=f"object already exists at {bucket}/{path}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def delete(self, bucket: str, path: str) -> bool:
        target = self._path(bucket, path)
        if not target.is_file():
            return False
        target.unlink()
        return True

    def list(
        self, bucket: str, prefix: str = "", *, limit: int = 100, cursor: str | None = None
    ) -> ListPage:
        base = (self._root / bucket).resolve()
        if not base.is_dir():
            return ListPage(keys=(), cursor=None)
        everything = sorted(
            str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()
        )
        matching = [k for k in everything if k.startswith(prefix)]
        offset = int(cursor) if cursor else 0
        window = matching[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(matching) else None
        return ListPage(keys=tuple(window), cursor=next_cursor)

    def presigned_get(
        self, bucket: str, path: str, *, expires_in: int = DEFAULT_DOWNLOAD_EXPIRY_SECONDS
    ) -> str | None:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail="the filesystem source store cannot sign URLs",
        )

    def presigned_put(
        self,
        bucket: str,
        path: str,
        *,
        expires_in: int = DEFAULT_UPLOAD_EXPIRY_SECONDS,
        content_type: str = SOURCE_CONTENT_TYPE,
        metadata: dict[str, str] | None = None,
    ) -> PresignedUpload:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail="the filesystem source store cannot sign URLs",
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _r2_setting(name: str, fallback: str = "") -> str:
    """Source settings first, then video's connection details.

    The buckets sit in one account behind one endpoint, so only the bucket has
    to be restated. Same inheritance `storage/book_images.py` uses, and the
    bucket is exactly the part that must never be inherited.
    """

    value = os.getenv(f"SOURCE_S3_{name}", "").strip()
    return value or os.getenv(f"VIDEO_S3_{name}", fallback).strip()


def r2_client():
    endpoint = _r2_setting("ENDPOINT")
    if not endpoint:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail="SOURCE_S3_ENDPOINT or VIDEO_S3_ENDPOINT is required",
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=_r2_setting("REGION", "auto") or "auto",
        aws_access_key_id=_r2_setting("ACCESS_KEY_ID"),
        aws_secret_access_key=_r2_setting("SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def source_bucket() -> str:
    """The configured bucket, refusing to share one with video or figures.

    Video's retention sweep enumerates its whole bucket and deletes what no
    video row names. Source PDFs in that bucket are exactly that, and the
    authoritativeness guard is a safety net rather than a reason to rely on it.
    """

    bucket = os.getenv("INGESTION_SOURCE_BUCKET", "").strip() or "book-sources"
    backend = configured_backend()
    if backend != R2_BACKEND:
        return bucket
    for other, label in (
        (os.getenv("VIDEO_S3_BUCKET", "").strip(), "VIDEO_S3_BUCKET"),
        (os.getenv("BOOK_IMAGE_S3_BUCKET", "").strip(), "BOOK_IMAGE_S3_BUCKET"),
    ):
        if other and bucket == other:
            raise IngestionError(
                ErrorCode.STORAGE_UNAVAILABLE,
                detail=(
                    f"INGESTION_SOURCE_BUCKET must differ from {label}: that "
                    "bucket's retention sweep deletes what its own rows do not name"
                ),
            )
    return bucket


def configured_backend() -> str:
    backend = os.getenv("SOURCE_STORAGE_BACKEND", "").strip().lower() or SUPABASE_BACKEND
    if backend not in KNOWN_BACKENDS:
        raise IngestionError(
            ErrorCode.STORAGE_UNAVAILABLE,
            detail=f"SOURCE_STORAGE_BACKEND must be one of {', '.join(KNOWN_BACKENDS)}",
        )
    return backend


def build_store(backend: str) -> SourceObjectStore:
    """One store, named explicitly. Used by migration, which needs two at once."""

    if backend == SUPABASE_BACKEND:
        return SupabaseSourceStore()
    if backend == R2_BACKEND:
        return R2SourceStore(client=r2_client())
    if backend == FILESYSTEM_BACKEND:
        root = os.getenv("SOURCE_STORAGE_ROOT", "").strip() or "data/book-sources"
        return FilesystemSourceStore(Path(root))
    raise IngestionError(
        ErrorCode.STORAGE_UNAVAILABLE, detail=f"unknown source storage backend: {backend!r}"
    )


def configured_source_store() -> SourceObjectStore:
    """The backend new rows use. Existing rows are read via the backend they name."""

    return build_store(configured_backend())


def store_for_backend(backend: str | None) -> SourceObjectStore:
    """Read an existing row through whichever backend that row records.

    This is what makes the migration row-by-row: while it runs, some books
    name `supabase` and some name `r2`, and both must open.
    """

    return build_store((backend or SUPABASE_BACKEND).strip().lower())


def owner_metadata(owner_id: str | UUID, job_id: str | UUID) -> dict[str, str]:
    return {OWNER_METADATA_KEY: str(owner_id), JOB_METADATA_KEY: str(job_id)}
