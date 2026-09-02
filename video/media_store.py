"""Owner-scoped immutable objects for large video media.

Database rows store relative keys; callers never receive absolute paths. The
filesystem backend serves local development. Production can select an
S3-compatible private bucket (Cloudflare R2) while preserving the same local
``Path`` contract for ffmpeg, OpenCV, Tesseract, and response streaming.
"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import md5, sha256
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Protocol
from uuid import UUID, uuid4

from storage.database import parse_owner_id


logger = logging.getLogger("study_partner.video.media_store")


DEFAULT_MEDIA_ROOT = Path("data/video-media")
DEFAULT_S3_CACHE_ROOT = Path(tempfile.gettempdir()) / "study-partner-r2-cache"
CHUNK_SIZE = 1024 * 1024
NAMESPACE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
EXTENSION = re.compile(r"^\.[a-z0-9]{1,10}$")


class MediaStoreError(RuntimeError):
    pass


class InvalidStorageKey(MediaStoreError):
    pass


class MediaTooLarge(MediaStoreError):
    pass


class MediaSizeMismatch(MediaStoreError):
    pass


class MediaConflict(MediaStoreError):
    pass


@dataclass(frozen=True)
class StoredMedia:
    storage_key: str
    content_hash: str
    size_bytes: int
    created: bool


@dataclass(frozen=True)
class StoredMediaObject:
    storage_key: str
    size_bytes: int
    last_modified: datetime


class MediaWriterContract(Protocol):
    @property
    def size_bytes(self) -> int: ...

    def write(self, chunk: bytes) -> None: ...

    def finish(self, *, expected_size: int) -> StoredMedia: ...

    def abort(self) -> None: ...


class MediaStore(Protocol):
    backend: str

    def writer(
        self,
        *,
        owner_id: str | UUID,
        storage_key: str,
        maximum_bytes: int,
    ) -> MediaWriterContract: ...

    def open_path(self, *, owner_id: str | UUID, storage_key: str) -> Path: ...

    def remove(self, *, owner_id: str | UUID, storage_key: str) -> bool: ...

    def verify_object(
        self,
        *,
        owner_id: str | UUID,
        storage_key: str,
        expected_size: int,
        expected_hash: str,
    ) -> StoredMedia: ...

    def import_file(
        self,
        *,
        owner_id: str | UUID,
        source: Path,
        namespace: str,
        extension: str,
        maximum_bytes: int,
    ) -> StoredMedia: ...


def configured_media_root() -> Path:
    configured = os.getenv("VIDEO_MEDIA_ROOT", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_MEDIA_ROOT


def configured_media_store() -> MediaStore:
    """Build the selected backend without making local setup require R2."""

    backend = os.getenv("VIDEO_MEDIA_BACKEND", "filesystem").strip().lower()
    if backend in {"", "filesystem"}:
        return FilesystemMediaStore()
    if backend in {"r2", "s3"}:
        return S3MediaStore.from_environment()
    raise MediaStoreError("VIDEO_MEDIA_BACKEND must be filesystem, r2, or s3")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest, size = sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _md5_file(path: Path) -> str:
    digest = md5()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _single_part_etag(etag: str) -> str:
    """The ETag when it is an MD5 of the whole object, else an empty string.

    S3 and R2 only promise that for objects stored in one part; a multipart
    upload's ETag is a digest of digests and carries a ``-N`` suffix, which
    tells us it cannot be compared against bytes we have hashed ourselves.
    """

    value = etag.strip('"').lower()
    if not value or "-" in value or len(value) != 32:
        return ""
    try:
        int(value, 16)
    except ValueError:
        return ""
    return value


def _checked_storage_key(*, owner_id: str | UUID, storage_key: str) -> str:
    owner = str(parse_owner_id(owner_id))
    if not storage_key or "\\" in storage_key or "\x00" in storage_key:
        raise InvalidStorageKey("invalid video storage key")
    raw_parts = storage_key.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise InvalidStorageKey("invalid video storage key")
    key = PurePosixPath(storage_key)
    if key.is_absolute() or not key.parts or key.parts[0] != owner:
        raise InvalidStorageKey("video storage key is outside its owner")
    return key.as_posix()


def _canonical_key(
    *, owner_id: str | UUID, namespace: str, extension: str, content_hash: str
) -> str:
    owner = parse_owner_id(owner_id)
    extension = extension.lower()
    if not NAMESPACE.fullmatch(namespace) or not EXTENSION.fullmatch(extension):
        raise ValueError("invalid canonical media namespace or extension")
    return (
        f"{owner}/canonical/{namespace}/sha256/{content_hash[:2]}/"
        f"{content_hash[2:4]}/{content_hash}{extension}"
    )


class FilesystemMediaStore:
    backend = "filesystem"

    def __init__(self, root: Path | None = None) -> None:
        selected = root or configured_media_root()
        selected.mkdir(parents=True, exist_ok=True, mode=0o700)
        if selected.is_symlink() or not selected.is_dir():
            raise MediaStoreError("video media root must be a real directory")
        self.root = selected.resolve()

    def _checked_path(self, *, owner_id: str | UUID, storage_key: str) -> Path:
        owner = str(parse_owner_id(owner_id))
        key = PurePosixPath(
            _checked_storage_key(owner_id=owner_id, storage_key=storage_key)
        )
        candidate = self.root.joinpath(*key.parts)
        try:
            candidate.resolve(strict=False).relative_to(self.root / owner)
        except ValueError as error:
            raise InvalidStorageKey("video storage key escapes its owner") from error
        return candidate

    def _ensure_parent(self, target: Path) -> None:
        current = self.root
        for part in target.relative_to(self.root).parts[:-1]:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise InvalidStorageKey("video storage path contains a symlink")
            else:
                current.mkdir(mode=0o700, exist_ok=True)
                if current.is_symlink() or not current.is_dir():
                    raise InvalidStorageKey("video storage path contains a symlink")

    def writer(
        self,
        *,
        owner_id: str | UUID,
        storage_key: str,
        maximum_bytes: int,
    ) -> "MediaWriter":
        if maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        target = self._checked_path(owner_id=owner_id, storage_key=storage_key)
        self._ensure_parent(target)
        return MediaWriter(
            target=target,
            storage_key=storage_key,
            maximum_bytes=maximum_bytes,
        )

    def open_path(self, *, owner_id: str | UUID, storage_key: str) -> Path:
        path = self._checked_path(owner_id=owner_id, storage_key=storage_key)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("video media object not found")
        return path

    def remove(self, *, owner_id: str | UUID, storage_key: str) -> bool:
        """Unlink one stored object, reporting whether it was there to unlink.

        Deliberately idempotent. The only caller deletes a set of keys *after*
        committing the rows that named them, so a partial failure leaves keys
        that are already gone and keys that are not, and running it again must
        finish the job rather than raise on the first one.

        Canonical objects are content-addressed, so one key can be the bytes of
        several rows. Deciding that a key is unreferenced is the caller's job
        and it is not second-guessed here — but a key still in use must never
        reach this method, because nothing about a hash path reveals who else
        is pointing at it.
        """

        path = self._checked_path(owner_id=owner_id, storage_key=storage_key)
        if path.is_symlink():
            raise InvalidStorageKey("video storage target is not a regular file")
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except IsADirectoryError as error:
            raise InvalidStorageKey("video storage key names a directory") from error
        self._prune(path.parent, owner_id=owner_id)
        return True

    def _prune(self, directory: Path, *, owner_id: str | UUID) -> None:
        """Remove the directories the deleted object leaves empty behind it.

        The sha256 fan-out gives every canonical object two directories of its
        own. Left alone they accumulate on a volume sized for video, where the
        whole point of deleting is to get space back.
        """

        boundary = self.root / str(parse_owner_id(owner_id))
        current = directory
        while current != boundary and boundary in current.parents:
            try:
                current.rmdir()
            except OSError:
                # Not empty, or gone already. Either way there is nothing
                # above it worth trying.
                return
            current = current.parent

    def verify_object(
        self,
        *,
        owner_id: str | UUID,
        storage_key: str,
        expected_size: int,
        expected_hash: str,
    ) -> StoredMedia:
        path = self.open_path(owner_id=owner_id, storage_key=storage_key)
        content_hash, size = _sha256_file(path)
        if size != expected_size or content_hash != expected_hash:
            raise MediaConflict("video media object changed after upload")
        return StoredMedia(storage_key, content_hash, size, created=False)

    def import_file(
        self,
        *,
        owner_id: str | UUID,
        source: Path,
        namespace: str,
        extension: str,
        maximum_bytes: int,
    ) -> StoredMedia:
        """Copy a validated work/staging file into immutable canonical storage."""

        extension = extension.lower()
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError("source media file not found")
        content_hash, size = _sha256_file(source)
        if size <= 0 or size > maximum_bytes:
            raise MediaTooLarge("video media exceeds the configured limit")
        key = _canonical_key(
            owner_id=owner_id,
            namespace=namespace,
            extension=extension,
            content_hash=content_hash,
        )
        writer = self.writer(
            owner_id=owner_id, storage_key=key, maximum_bytes=maximum_bytes
        )
        try:
            with source.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    writer.write(chunk)
            stored = writer.finish(expected_size=size)
        finally:
            writer.abort()
        if stored.content_hash != content_hash:
            raise MediaConflict("video media changed while being promoted")
        return stored


class MediaWriter:
    def __init__(self, *, target: Path, storage_key: str, maximum_bytes: int) -> None:
        self.target = target
        self.storage_key = storage_key
        self.maximum_bytes = maximum_bytes
        self.temporary = target.parent / f".{target.name}.{uuid4().hex}.part"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.temporary, flags, 0o600)
        self._handle = os.fdopen(descriptor, "wb")
        self._digest = sha256()
        self._size = 0
        self._finished = False

    @property
    def size_bytes(self) -> int:
        return self._size

    def write(self, chunk: bytes) -> None:
        if self._finished:
            raise MediaStoreError("video media writer is already closed")
        if not chunk:
            return
        if self._size + len(chunk) > self.maximum_bytes:
            raise MediaTooLarge("video upload exceeds the configured limit")
        self._handle.write(chunk)
        self._digest.update(chunk)
        self._size += len(chunk)

    def finish(self, *, expected_size: int) -> StoredMedia:
        if self._finished:
            raise MediaStoreError("video media writer is already closed")
        try:
            if self._size != expected_size:
                raise MediaSizeMismatch("video upload size does not match reservation")
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            content_hash = self._digest.hexdigest()
            created = self._publish(content_hash)
            self._finished = True
            return StoredMedia(
                storage_key=self.storage_key,
                content_hash=content_hash,
                size_bytes=self._size,
                created=created,
            )
        except Exception:
            self.abort()
            raise

    def _publish(self, content_hash: str) -> bool:
        try:
            os.link(self.temporary, self.target)
            os.chmod(self.target, 0o600)
            created = True
        except FileExistsError:
            if self.target.is_symlink() or not self.target.is_file():
                raise InvalidStorageKey("video storage target is not a regular file")
            existing_hash, existing_size = _sha256_file(self.target)
            if existing_hash != content_hash or existing_size != self._size:
                raise MediaConflict("video upload conflicts with stored bytes")
            created = False
        finally:
            self.temporary.unlink(missing_ok=True)
        directory = os.open(self.target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return created

    def abort(self) -> None:
        if self._finished:
            return
        if not self._handle.closed:
            self._handle.close()
        self.temporary.unlink(missing_ok=True)
        self._finished = True


class S3MediaStore:
    """Private S3-compatible storage with a verified local read-through cache.

    R2 objects keep the same owner-prefixed keys as filesystem objects. The
    local cache is never canonical: an absent cache entry is downloaded, and a
    cache can be deleted at any time without losing source material.
    """

    backend = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        client,
        cache_root: Path | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 media bucket is required")
        self.bucket = bucket.strip()
        self.client = client
        selected = cache_root or DEFAULT_S3_CACHE_ROOT
        selected.mkdir(parents=True, exist_ok=True, mode=0o700)
        if selected.is_symlink() or not selected.is_dir():
            raise MediaStoreError("S3 media cache root must be a real directory")
        self.cache_root = selected.resolve()

    @classmethod
    def from_environment(cls) -> "S3MediaStore":
        bucket = os.getenv("VIDEO_S3_BUCKET", "").strip()
        endpoint = os.getenv("VIDEO_S3_ENDPOINT", "").strip()
        access_key = os.getenv("VIDEO_S3_ACCESS_KEY_ID", "").strip()
        secret_key = os.getenv("VIDEO_S3_SECRET_ACCESS_KEY", "").strip()
        region = os.getenv("VIDEO_S3_REGION", "auto").strip() or "auto"
        missing = [
            name
            for name, value in (
                ("VIDEO_S3_BUCKET", bucket),
                ("VIDEO_S3_ENDPOINT", endpoint),
                ("VIDEO_S3_ACCESS_KEY_ID", access_key),
                ("VIDEO_S3_SECRET_ACCESS_KEY", secret_key),
            )
            if not value
        ]
        if missing:
            raise MediaStoreError(
                "S3 media configuration is incomplete: " + ", ".join(missing)
            )
        try:
            import boto3
        except ImportError as error:  # pragma: no cover - packaging guard
            raise MediaStoreError("boto3 is required for S3 video media") from error
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        configured_cache = os.getenv("VIDEO_MEDIA_CACHE_ROOT", "").strip()
        return cls(
            bucket=bucket,
            client=client,
            cache_root=Path(configured_cache).expanduser() if configured_cache else None,
        )

    def _key(self, *, owner_id: str | UUID, storage_key: str) -> str:
        return _checked_storage_key(owner_id=owner_id, storage_key=storage_key)

    def _cache_path(self, key: str) -> Path:
        target = self.cache_root.joinpath(*PurePosixPath(key).parts)
        try:
            target.resolve(strict=False).relative_to(self.cache_root)
        except ValueError as error:
            raise InvalidStorageKey("video storage key escapes the cache") from error
        return target

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.r2-cache.json")

    @staticmethod
    def _not_found(error: Exception) -> bool:
        response = getattr(error, "response", {}) or {}
        code = str((response.get("Error") or {}).get("Code") or "")
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    def _head(self, key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if self._not_found(error):
                return None
            raise MediaStoreError("S3 media metadata request failed") from error

    def iter_objects(self) -> "tuple[StoredMediaObject, ...]":
        """List the private bucket completely for grace-aware orphan cleanup."""

        objects: list[StoredMediaObject] = []
        token: str | None = None
        while True:
            request = {"Bucket": self.bucket, "MaxKeys": 1000}
            if token is not None:
                request["ContinuationToken"] = token
            try:
                page = self.client.list_objects_v2(**request)
            except Exception as error:
                raise MediaStoreError("S3 media listing failed") from error
            for item in page.get("Contents") or ():
                key = item.get("Key")
                size = item.get("Size")
                modified = item.get("LastModified")
                if (
                    not isinstance(key, str)
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or size < 0
                    or not isinstance(modified, datetime)
                ):
                    raise MediaStoreError("S3 media listing returned malformed data")
                objects.append(StoredMediaObject(key, size, modified))
            if not page.get("IsTruncated"):
                break
            raw_token = page.get("NextContinuationToken")
            if not isinstance(raw_token, str) or not raw_token or raw_token == token:
                raise MediaStoreError("S3 media listing pagination is malformed")
            token = raw_token
        return tuple(objects)

    def writer(
        self,
        *,
        owner_id: str | UUID,
        storage_key: str,
        maximum_bytes: int,
    ) -> "S3MediaWriter":
        if maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        key = self._key(owner_id=owner_id, storage_key=storage_key)
        return S3MediaWriter(store=self, storage_key=key, maximum_bytes=maximum_bytes)

    def open_path(self, *, owner_id: str | UUID, storage_key: str) -> Path:
        key = self._key(owner_id=owner_id, storage_key=storage_key)
        head = self._head(key)
        if head is None:
            raise FileNotFoundError("video media object not found")
        size = int(head.get("ContentLength") or 0)
        etag = str(head.get("ETag") or "").strip('"')
        remote_hash = str((head.get("Metadata") or {}).get("sha256") or "")
        target = self._cache_path(key)
        marker = self._metadata_path(target)
        if target.is_file() and not target.is_symlink() and marker.is_file():
            try:
                cached = json.loads(marker.read_text())
            except (OSError, ValueError):
                cached = {}
            # Compared against what the remote asserts, which for an object
            # carrying no sha256 metadata is nothing. Storing our own computed
            # digest under this name made every such read a cache miss and a
            # fresh download; the ETag is what actually changes with content.
            if (
                target.stat().st_size == size
                and cached.get("etag") == etag
                and cached.get("remote_sha256") == remote_hash
                and cached.get("size_bytes") == size
            ):
                return target

        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.part"
        try:
            self.client.download_file(self.bucket, key, str(temporary))
            digest, downloaded_size = _sha256_file(temporary)
            if downloaded_size != size:
                raise MediaConflict("S3 media object failed download verification")
            if remote_hash:
                if digest != remote_hash:
                    raise MediaConflict("S3 media object failed download verification")
            else:
                # No sha256 metadata: objects written before this store, or by
                # anything else, carry none. Size alone would accept a
                # different object of the same length, so fall back to the
                # ETag, which is the whole object's MD5 whenever it was stored
                # in one part.
                content_md5 = _single_part_etag(etag)
                if content_md5:
                    if _md5_file(temporary) != content_md5:
                        raise MediaConflict(
                            "S3 media object failed download verification"
                        )
                else:
                    logger.warning(
                        "S3 object %s has no sha256 metadata and no single-part "
                        "ETag; it is verified by size alone",
                        key,
                    )
            os.replace(temporary, target)
            marker_tmp = marker.parent / f".{marker.name}.{uuid4().hex}.part"
            marker_tmp.write_text(
                json.dumps(
                    {
                        "etag": etag,
                        "sha256": digest,
                        "remote_sha256": remote_hash,
                        "size_bytes": size,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            os.replace(marker_tmp, marker)
            return target
        except MediaStoreError:
            raise
        except Exception as error:
            raise MediaStoreError("S3 media download failed") from error
        finally:
            temporary.unlink(missing_ok=True)

    def remove(self, *, owner_id: str | UUID, storage_key: str) -> bool:
        key = self._key(owner_id=owner_id, storage_key=storage_key)
        if self._head(key) is None:
            return False
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            raise MediaStoreError("S3 media deletion failed") from error
        target = self._cache_path(key)
        target.unlink(missing_ok=True)
        self._metadata_path(target).unlink(missing_ok=True)
        return True

    def verify_object(
        self,
        *,
        owner_id: str | UUID,
        storage_key: str,
        expected_size: int,
        expected_hash: str,
    ) -> StoredMedia:
        key = self._key(owner_id=owner_id, storage_key=storage_key)
        head = self._head(key)
        if head is None:
            raise FileNotFoundError("video media object not found")
        remote_size = int(head.get("ContentLength") or 0)
        remote_hash = str((head.get("Metadata") or {}).get("sha256") or "")
        if remote_size != expected_size or (remote_hash and remote_hash != expected_hash):
            raise MediaConflict("S3 media object changed after upload")
        path = self.open_path(owner_id=owner_id, storage_key=key)
        content_hash, size = _sha256_file(path)
        if size != expected_size or content_hash != expected_hash:
            raise MediaConflict("S3 media object changed after upload")
        return StoredMedia(key, content_hash, size, created=False)

    def import_file(
        self,
        *,
        owner_id: str | UUID,
        source: Path,
        namespace: str,
        extension: str,
        maximum_bytes: int,
    ) -> StoredMedia:
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError("source media file not found")
        content_hash, size = _sha256_file(source)
        if size <= 0 or size > maximum_bytes:
            raise MediaTooLarge("video media exceeds the configured limit")
        key = _canonical_key(
            owner_id=owner_id,
            namespace=namespace,
            extension=extension,
            content_hash=content_hash,
        )
        return self._publish_file(
            key=key, source=source, content_hash=content_hash, size=size
        )

    def _publish_file(
        self, *, key: str, source: Path, content_hash: str, size: int
    ) -> StoredMedia:
        existing = self._head(key)
        if existing is not None:
            existing_hash = str(
                (existing.get("Metadata") or {}).get("sha256") or ""
            )
            existing_size = int(existing.get("ContentLength") or 0)
            if existing_size != size or (existing_hash and existing_hash != content_hash):
                raise MediaConflict("S3 upload conflicts with stored bytes")
            return StoredMedia(key, content_hash, size, created=False)
        try:
            self.client.upload_file(
                str(source),
                self.bucket,
                key,
                ExtraArgs={
                    "Metadata": {"sha256": content_hash},
                    "ContentType": "application/octet-stream",
                },
            )
        except Exception as error:
            raise MediaStoreError("S3 media upload failed") from error
        stored = self._head(key)
        if stored is None:
            raise MediaConflict("S3 upload completed without a stored object")
        stored_hash = str((stored.get("Metadata") or {}).get("sha256") or "")
        if int(stored.get("ContentLength") or 0) != size or stored_hash != content_hash:
            raise MediaConflict("S3 upload failed verification")
        return StoredMedia(key, content_hash, size, created=True)


class S3MediaWriter:
    """Bounded stream writer that promotes one verified temporary file to S3."""

    def __init__(
        self, *, store: S3MediaStore, storage_key: str, maximum_bytes: int
    ) -> None:
        self.store = store
        self.storage_key = storage_key
        self.maximum_bytes = maximum_bytes
        uploads = store.cache_root / ".uploads"
        uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix="video-", suffix=".part", dir=uploads)
        os.chmod(name, 0o600)
        self.temporary = Path(name)
        self._handle = os.fdopen(descriptor, "wb")
        self._digest = sha256()
        self._size = 0
        self._finished = False

    @property
    def size_bytes(self) -> int:
        return self._size

    def write(self, chunk: bytes) -> None:
        if self._finished:
            raise MediaStoreError("video media writer is already closed")
        if not chunk:
            return
        if self._size + len(chunk) > self.maximum_bytes:
            raise MediaTooLarge("video upload exceeds the configured limit")
        self._handle.write(chunk)
        self._digest.update(chunk)
        self._size += len(chunk)

    def finish(self, *, expected_size: int) -> StoredMedia:
        if self._finished:
            raise MediaStoreError("video media writer is already closed")
        try:
            if self._size != expected_size:
                raise MediaSizeMismatch("video upload size does not match reservation")
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            result = self.store._publish_file(
                key=self.storage_key,
                source=self.temporary,
                content_hash=self._digest.hexdigest(),
                size=self._size,
            )
            self._finished = True
            return result
        except Exception:
            self.abort()
            raise
        finally:
            self.temporary.unlink(missing_ok=True)

    def abort(self) -> None:
        if self._finished:
            return
        if not self._handle.closed:
            self._handle.close()
        self.temporary.unlink(missing_ok=True)
        self._finished = True
