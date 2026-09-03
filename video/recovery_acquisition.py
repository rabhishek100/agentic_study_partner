"""Recover immutable YouTube acquisition artifacts from an S3-compatible bucket.

This is a disaster-recovery input, not a second ingestion pipeline.  It
recreates the ``DownloadedSource`` contract produced by yt-dlp, after which
the ordinary media, transcript, frame, vision, indexing, and publish stages
run unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from video.acquisition import (
    AcquisitionError,
    DownloadedSource,
    chapters_from_metadata,
    probe_media,
    verify_decodable,
)
from video.sources import YOUTUBE_ID, parse_youtube_url


CHUNK_SIZE = 8 * 1024 * 1024
MAXIMUM_MATCH_DELTA = 0.15


@dataclass(frozen=True)
class RecoveryObject:
    key: str
    size_bytes: int
    content_hash: str
    last_modified: Any


@dataclass(frozen=True)
class RecoveredYouTubeArtifacts:
    video_id: str
    title: str
    description: str
    metadata: dict[str, Any]
    video: RecoveryObject
    info: RecoveryObject
    caption: RecoveryObject | None


logger = logging.getLogger("study_partner.video.recovery_acquisition")


def _content_hash(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    value = name.split(".", 1)[0]
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise AcquisitionError("recovery object key is not content-addressed")
    return value


class S3YouTubeRecoveryAcquirer:
    """Resolve and resumably download canonical artifacts for one YouTube ID."""

    def __init__(self, *, client: Any, bucket: str, owner_id: str | UUID) -> None:
        self.client = client
        self.bucket = bucket.strip()
        self.owner_id = str(UUID(str(owner_id)))
        if not self.bucket:
            raise ValueError("recovery bucket is required")
        self._artifacts: dict[str, RecoveredYouTubeArtifacts] | None = None
        self.superseded_metadata: tuple[RecoveryObject, ...] = ()

    @property
    def prefix(self) -> str:
        return f"{self.owner_id}/canonical/"

    def _objects(self, namespace: str) -> tuple[RecoveryObject, ...]:
        prefix = f"{self.prefix}{namespace}/"
        objects: list[RecoveryObject] = []
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            )
            for page in pages:
                for item in page.get("Contents") or ():
                    key = item.get("Key")
                    size = item.get("Size")
                    if not isinstance(key, str) or not isinstance(size, int) or size <= 0:
                        raise AcquisitionError("recovery bucket listing is malformed")
                    objects.append(
                        RecoveryObject(
                            key=key,
                            size_bytes=size,
                            content_hash=_content_hash(key),
                            last_modified=item.get("LastModified"),
                        )
                    )
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError("recovery bucket could not be listed") from error
        return tuple(objects)

    def _read_json(self, artifact: RecoveryObject) -> dict[str, Any]:
        if artifact.size_bytes > 10 * 1024 * 1024:
            raise AcquisitionError("recovery metadata is too large")
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=artifact.key)["Body"]
            payload = json.loads(body.read())
        except Exception as error:
            raise AcquisitionError("recovery metadata could not be read") from error
        if not isinstance(payload, dict):
            raise AcquisitionError("recovery metadata is malformed")
        return payload

    def inventory(self) -> dict[str, RecoveredYouTubeArtifacts]:
        if self._artifacts is not None:
            return self._artifacts
        videos = list(self._objects("videos"))
        infos = self._objects("metadata")
        captions = self._objects("transcripts")
        discovered: dict[str, RecoveredYouTubeArtifacts] = {}

        # Read every metadata object first, then decide which one speaks for
        # each video. A bucket that has been ingested more than once holds two
        # metadata objects for the same lecture, and treating that as fatal
        # meant one duplicated lecture made the other twenty unrecoverable —
        # a whole course held hostage by a re-ingest. A duplicate is a
        # reconcilable fact, so the newest object wins and the rest are named.
        described: list[tuple[RecoveryObject, dict[str, Any], str, float]] = []
        for info in infos:
            metadata = self._read_json(info)
            video_id = metadata.get("id")
            title = metadata.get("title")
            description = metadata.get("description") or ""
            approximate_size = metadata.get("filesize_approx")
            if (
                not isinstance(video_id, str)
                or not YOUTUBE_ID.fullmatch(video_id)
                or not isinstance(title, str)
                or not title.strip()
                or not isinstance(description, str)
                or isinstance(approximate_size, bool)
                or not isinstance(approximate_size, (int, float))
                or approximate_size <= 0
            ):
                raise AcquisitionError("recovery metadata is missing required fields")
            described.append((info, metadata, video_id, float(approximate_size)))

        chosen: dict[str, tuple[RecoveryObject, dict[str, Any], str, float]] = {}
        superseded: list[RecoveryObject] = []
        for entry in described:
            video_id = entry[2]
            existing = chosen.get(video_id)
            if existing is None:
                chosen[video_id] = entry
                continue
            # Newest wins; a bucket with no timestamps falls back to the key,
            # so the choice is deterministic either way.
            incumbent_stamp = existing[0].last_modified
            challenger_stamp = entry[0].last_modified
            if incumbent_stamp is None or challenger_stamp is None:
                replace = entry[0].key > existing[0].key
            else:
                replace = challenger_stamp > incumbent_stamp
            loser, winner = (existing, entry) if replace else (entry, existing)
            chosen[video_id] = winner
            superseded.append(loser[0])
            logger.warning(
                "recovery metadata names video %s more than once; using %s and "
                "ignoring %s",
                video_id,
                winner[0].key,
                loser[0].key,
            )
        self.superseded_metadata = tuple(superseded)

        # Ordered by key so a run is reproducible regardless of listing order.
        for info, metadata, video_id, approximate_size in sorted(
            chosen.values(), key=lambda entry: entry[0].key
        ):
            title = metadata["title"]
            description = metadata.get("description") or ""
            if not videos:
                raise AcquisitionError("recovery bucket has fewer videos than metadata")
            video = min(videos, key=lambda item: abs(item.size_bytes - approximate_size))
            delta = abs(video.size_bytes - approximate_size) / approximate_size
            if delta > MAXIMUM_MATCH_DELTA:
                raise AcquisitionError("recovery video could not be matched to metadata")
            videos.remove(video)
            caption = None
            if captions and info.last_modified is not None:
                caption = min(
                    captions,
                    key=lambda item: abs(
                        (item.last_modified - info.last_modified).total_seconds()
                    ),
                )
                distance = abs((caption.last_modified - info.last_modified).total_seconds())
                if distance > 10:
                    caption = None
                else:
                    captions = [item for item in captions if item != caption]
            discovered[video_id] = RecoveredYouTubeArtifacts(
                video_id=video_id,
                title=title.strip(),
                description=description,
                metadata=metadata,
                video=video,
                info=info,
                caption=caption,
            )
        self._artifacts = discovered
        return discovered

    def __call__(
        self,
        source_url: str,
        destination: Path,
        *,
        expected_video_id: str,
        maximum_bytes: int,
    ) -> DownloadedSource:
        parsed = parse_youtube_url(source_url)
        if parsed.video_id != expected_video_id:
            raise AcquisitionError("source URL does not match the recovery video ID")
        artifacts = self.inventory().get(expected_video_id)
        if artifacts is None:
            raise AcquisitionError("video is absent from the recovery bucket")
        if artifacts.video.size_bytes > maximum_bytes:
            raise AcquisitionError("recovered video exceeds the configured size limit")

        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or not root.is_dir():
            raise AcquisitionError("recovery destination must be a real directory")
        root = root.resolve()
        video_path = self._download(artifacts.video, root / "source.mp4")
        info_path = self._download(artifacts.info, root / "source.info.json")
        caption_paths: tuple[Path, ...] = ()
        if artifacts.caption is not None:
            caption_paths = (
                self._download(artifacts.caption, root / "source.en.vtt"),
            )
        verify_decodable(video_path)
        media = probe_media(video_path)
        chapters = chapters_from_metadata(
            artifacts.metadata, duration_ms=media.duration_ms
        )
        return DownloadedSource(
            video_id=expected_video_id,
            title=artifacts.title,
            description=artifacts.description,
            video_path=video_path,
            info_path=info_path,
            caption_paths=caption_paths,
            media=media,
            chapters=chapters,
        )

    def _download(self, artifact: RecoveryObject, target: Path) -> Path:
        partial = target.with_name(f"{target.name}.part")
        if target.is_file() and not target.is_symlink():
            if self._verified(target, artifact):
                return target
            target.replace(partial)
        if partial.exists() and (partial.is_symlink() or not partial.is_file()):
            raise AcquisitionError("recovery partial path is not a regular file")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > artifact.size_bytes:
            partial.unlink()
            offset = 0
        request = {"Bucket": self.bucket, "Key": artifact.key}
        if offset:
            request["Range"] = f"bytes={offset}-"
        try:
            response = self.client.get_object(**request)
            with partial.open("ab" if offset else "wb") as handle:
                while chunk := response["Body"].read(CHUNK_SIZE):
                    handle.write(chunk)
        except Exception as error:
            raise AcquisitionError("recovery object download was interrupted") from error
        if not self._verified(partial, artifact):
            raise AcquisitionError("recovery object failed size or hash verification")
        partial.replace(target)
        return target

    @staticmethod
    def _verified(path: Path, artifact: RecoveryObject) -> bool:
        if path.stat().st_size != artifact.size_bytes:
            return False
        digest = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest() == artifact.content_hash

