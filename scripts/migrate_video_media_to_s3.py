"""Copy a filesystem video-media tree into the configured S3/R2 bucket.

The command is deliberately resumable: content-addressed objects already in
R2 are verified and skipped, missing objects are uploaded only with ``--apply``,
and conflicting bytes stop the run.  It never deletes the source tree.

    uv run python -m scripts.migrate_video_media_to_s3
    uv run python -m scripts.migrate_video_media_to_s3 --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()

from video.media_store import (  # noqa: E402
    MediaConflict,
    S3MediaStore,
    _sha256_file,
    configured_media_root,
)


@dataclass(frozen=True)
class MigrationSummary:
    files: int = 0
    bytes: int = 0
    uploaded: int = 0
    verified: int = 0


def _source_files(root: Path) -> tuple[Path, ...]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("video media source root must be a real directory")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"video media source contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            # In-progress uploads and cache markers are disposable, not
            # canonical media objects.
            continue
        files.append(path)
    return tuple(sorted(files))


def migrate_media_tree(
    *, source_root: Path, target: S3MediaStore, apply: bool
) -> MigrationSummary:
    files = total_bytes = uploaded = verified = 0
    for source in _source_files(source_root):
        relative = source.relative_to(source_root)
        key = relative.as_posix()
        try:
            owner = UUID(relative.parts[0])
        except (IndexError, ValueError) as error:
            raise ValueError(f"video media object is not owner scoped: {key}") from error
        target._key(owner_id=owner, storage_key=key)

        digest, size = _sha256_file(source)
        files += 1
        total_bytes += size
        existing = target._head(key)
        if existing is not None:
            remote_size = int(existing.get("ContentLength") or 0)
            remote_hash = str((existing.get("Metadata") or {}).get("sha256") or "")
            if remote_size != size or (remote_hash and remote_hash != digest):
                raise MediaConflict(f"R2 object conflicts with source bytes: {key}")
            if not remote_hash and apply:
                # Legacy objects without the integrity metadata cannot be
                # trusted as a completed checkpoint. Re-uploading is safe and
                # gives future reads a hash to verify.
                target.client.upload_file(
                    str(source),
                    target.bucket,
                    key,
                    ExtraArgs={
                        "Metadata": {"sha256": digest},
                        "ContentType": "application/octet-stream",
                    },
                )
                uploaded += 1
            else:
                verified += 1
            print(f"verify {size:>12,}  {key}")
            continue

        if not apply:
            print(f"upload {size:>12,}  {key}  (dry run)")
            continue
        target._publish_file(key=key, source=source, content_hash=digest, size=size)
        uploaded += 1
        print(f"upload {size:>12,}  {key}")

    if apply:
        for source in _source_files(source_root):
            key = source.relative_to(source_root).as_posix()
            digest, size = _sha256_file(source)
            head = target._head(key)
            if head is None:
                raise MediaConflict(f"R2 object missing after migration: {key}")
            remote_hash = str((head.get("Metadata") or {}).get("sha256") or "")
            if int(head.get("ContentLength") or 0) != size or remote_hash != digest:
                raise MediaConflict(f"R2 verification failed after migration: {key}")

    return MigrationSummary(files, total_bytes, uploaded, verified)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=configured_media_root(),
        help="Filesystem media root (defaults to VIDEO_MEDIA_ROOT).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload missing objects. Without this flag the command is read-only.",
    )
    arguments = parser.parse_args()
    summary = migrate_media_tree(
        source_root=arguments.source_root,
        target=S3MediaStore.from_environment(),
        apply=arguments.apply,
    )
    mode = "applied" if arguments.apply else "dry-run"
    print(
        f"{mode}: {summary.files} files, {summary.bytes:,} bytes, "
        f"{summary.uploaded} uploaded, {summary.verified} already verified"
    )


if __name__ == "__main__":
    main()
