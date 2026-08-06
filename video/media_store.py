"""Owner-scoped filesystem objects for large video media.

Database rows store relative keys; callers never receive absolute paths. The
API and video worker must mount the same root when this backend is selected.
"""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
from uuid import UUID, uuid4

from storage.database import parse_owner_id


DEFAULT_MEDIA_ROOT = Path("data/video-media")
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


def configured_media_root() -> Path:
    configured = os.getenv("VIDEO_MEDIA_ROOT", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_MEDIA_ROOT


def _sha256_file(path: Path) -> tuple[str, int]:
    digest, size = sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


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
        if not storage_key or "\\" in storage_key or "\x00" in storage_key:
            raise InvalidStorageKey("invalid video storage key")
        raw_parts = storage_key.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise InvalidStorageKey("invalid video storage key")
        key = PurePosixPath(storage_key)
        if key.is_absolute() or not key.parts or key.parts[0] != owner:
            raise InvalidStorageKey("video storage key is outside its owner")
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

        owner = parse_owner_id(owner_id)
        extension = extension.lower()
        if not NAMESPACE.fullmatch(namespace) or not EXTENSION.fullmatch(extension):
            raise ValueError("invalid canonical media namespace or extension")
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError("source media file not found")
        content_hash, size = _sha256_file(source)
        if size <= 0 or size > maximum_bytes:
            raise MediaTooLarge("video media exceeds the configured limit")
        key = (
            f"{owner}/canonical/{namespace}/sha256/{content_hash[:2]}/"
            f"{content_hash[2:4]}/{content_hash}{extension}"
        )
        writer = self.writer(
            owner_id=owner, storage_key=key, maximum_bytes=maximum_bytes
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
