"""Fenced persistence for canonical video media and timeline metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from video.acquisition import Chapter, MediaMetadata


CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")


class VideoSourceNotFoundError(LookupError):
    pass


class VideoSourceConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquisitionTarget:
    job_id: UUID
    owner_id: UUID
    video_id: UUID
    version_id: UUID
    source_id: UUID
    source_kind: str
    source_status: str
    source_url: str | None
    youtube_video_id: str | None
    original_filename: str | None
    source_storage_backend: str | None
    source_storage_key: str | None
    source_content_hash: str | None
    source_size_bytes: int | None
    source_media_type: str | None
    source_acquisition_provider: str | None
    source_acquisition_version: str | None
    source_provenance: dict[str, Any]
    staging_storage_backend: str | None
    staging_storage_key: str | None
    staging_content_hash: str | None
    staging_size_bytes: int | None


@dataclass(frozen=True)
class PersistedVideoSource:
    source_id: UUID
    status: str
    storage_key: str
    content_hash: str
    size_bytes: int
    replayed: bool


def _locked_target(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    stage: str,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select j.id as job_id, j.owner_id, j.video_id,
               j.target_version_id as version_id,
               j.staging_storage_backend, j.staging_storage_key,
               j.staging_content_hash, j.staging_size_bytes,
               s.id as source_id, s.source_kind, s.status as source_status,
               s.source_url, s.youtube_video_id, s.original_filename,
               s.storage_backend as source_storage_backend,
               s.storage_key as source_storage_key,
               s.content_hash as source_content_hash,
               s.size_bytes as source_size_bytes,
               s.media_type as source_media_type,
               s.acquisition_provider as source_acquisition_provider,
               s.acquisition_version as source_acquisition_version,
               s.provenance_json as source_provenance
        from video.ingestion_jobs as j
        join video.ingestion_versions as version
          on version.id = j.target_version_id
         and version.video_id = j.video_id
         and version.owner_id = j.owner_id
        join video.video_sources as s
          on s.id = version.video_source_id
         and s.video_id = version.video_id
         and s.owner_id = version.owner_id
        where j.id = %s and j.status = 'running' and j.stage = %s
          and j.lease_owner = %s and j.attempt_count = %s
          and j.lease_expires_at >= now()
        for update of j, version, s
        """,
        (UUID(str(job_id)), stage, worker_id, attempt_count),
    ).fetchone()
    if row is None:
        raise VideoSourceNotFoundError(
            "video source is unavailable to this worker attempt"
        )
    return row


def load_acquisition_target(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
) -> AcquisitionTarget:
    """Load the source inputs only for the active, lease-fenced acquire stage."""

    with connection.transaction():
        row = _locked_target(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage="acquire_source",
        )
        return AcquisitionTarget(**dict(row))


def record_acquired_media(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    storage_backend: str,
    storage_key: str,
    content_hash: str,
    size_bytes: int,
    media_type: str,
    acquisition_provider: str,
    acquisition_version: str,
    provenance: dict[str, Any] | None = None,
) -> PersistedVideoSource:
    """Record immutable canonical bytes without declaring the source ready."""

    if storage_backend not in {"filesystem", "supabase", "s3"}:
        raise ValueError("unsupported video storage backend")
    if not CONTENT_HASH.fullmatch(content_hash):
        raise ValueError("content_hash must be a SHA-256 hex digest")
    if size_bytes <= 0:
        raise ValueError("size_bytes must be positive")
    if not media_type.strip() or not acquisition_provider.strip():
        raise ValueError("media type and acquisition provider are required")
    if not acquisition_version.strip():
        raise ValueError("acquisition version is required")

    with connection.transaction():
        row = _locked_target(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage="acquire_source",
        )
        if not storage_key.startswith(f"{row['owner_id']}/"):
            raise ValueError("storage_key must be scoped to the source owner")
        expected = (
            storage_backend,
            storage_key,
            content_hash,
            size_bytes,
            media_type.strip(),
            acquisition_provider.strip(),
            acquisition_version.strip(),
        )
        existing = (
            row["source_storage_backend"],
            row["source_storage_key"],
            row["source_content_hash"],
            row["source_size_bytes"],
            row["source_media_type"],
            row["source_acquisition_provider"],
            row["source_acquisition_version"],
        )
        if row["source_status"] in {"acquiring", "ready"}:
            provenance_matches = all(
                row["source_provenance"].get(key) == value
                for key, value in (provenance or {}).items()
            )
            if existing != expected or not provenance_matches:
                raise VideoSourceConflictError(
                    "canonical source media changed during ingestion"
                )
            return PersistedVideoSource(
                row["source_id"],
                row["source_status"],
                storage_key,
                content_hash,
                size_bytes,
                replayed=True,
            )
        if row["source_status"] != "pending":
            raise VideoSourceConflictError("video source cannot be acquired")

        connection.execute(
            """
            update video.video_sources
            set status = 'acquiring', storage_backend = %s, storage_key = %s,
                content_hash = %s, size_bytes = %s, media_type = %s,
                acquisition_provider = %s, acquisition_version = %s,
                provenance_json = provenance_json || %s
            where id = %s and owner_id = %s
            """,
            (
                storage_backend,
                storage_key,
                content_hash,
                size_bytes,
                media_type.strip(),
                acquisition_provider.strip(),
                acquisition_version.strip(),
                Jsonb(provenance or {}),
                row["source_id"],
                row["owner_id"],
            ),
        )
        return PersistedVideoSource(
            row["source_id"],
            "acquiring",
            storage_key,
            content_hash,
            size_bytes,
            replayed=False,
        )


def publish_media_metadata(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    media: MediaMetadata,
    chapters: tuple[Chapter, ...] | list[Chapter] = (),
    discovered_title: str | None = None,
    discovered_description: str | None = None,
) -> PersistedVideoSource:
    """Atomically publish probed metadata and source-aligned chapters."""

    _validate_media(media)
    normalized_chapters = tuple(chapters)
    _validate_chapters(normalized_chapters, duration_ms=media.duration_ms)
    clean_title = discovered_title.strip() if discovered_title else None
    clean_description = (
        discovered_description.strip() if discovered_description else None
    )

    with connection.transaction():
        row = _locked_target(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage="media_metadata",
        )
        if row["source_status"] not in {"acquiring", "ready"}:
            raise VideoSourceConflictError("canonical media has not been acquired")
        if row["source_size_bytes"] != media.size_bytes:
            raise VideoSourceConflictError("probed media size does not match the source")

        metadata = {
            **asdict(media),
            "title": clean_title,
            "description": clean_description,
        }
        existing_metadata = connection.execute(
            """
            select media_metadata_json from video.video_sources
            where id = %s and owner_id = %s
            """,
            (row["source_id"], row["owner_id"]),
        ).fetchone()["media_metadata_json"]
        existing_chapters = connection.execute(
            """
            select chapter_index, title, start_ms, end_ms
            from video.chapters
            where owner_id = %s and video_source_id = %s
            order by chapter_index
            """,
            (row["owner_id"], row["source_id"]),
        ).fetchall()
        wanted_chapters = [
            {
                "chapter_index": chapter.index,
                "title": chapter.title,
                "start_ms": chapter.start_ms,
                "end_ms": chapter.end_ms,
            }
            for chapter in normalized_chapters
        ]
        if row["source_status"] == "ready":
            if existing_metadata != metadata or existing_chapters != wanted_chapters:
                raise VideoSourceConflictError(
                    "published video metadata changed during replay"
                )
            return PersistedVideoSource(
                row["source_id"],
                "ready",
                row["source_storage_key"],
                row["source_content_hash"],
                row["source_size_bytes"],
                replayed=True,
            )

        connection.execute(
            """
            update video.video_sources
            set status = 'ready', media_metadata_json = %s, acquired_at = now()
            where id = %s and owner_id = %s
            """,
            (Jsonb(metadata), row["source_id"], row["owner_id"]),
        )
        playback = (
            {"kind": "youtube", "youtube_video_id": row["youtube_video_id"]}
            if row["source_kind"] == "youtube"
            else {
                "kind": "local",
                "storage_backend": row["source_storage_backend"],
                "storage_key": row["source_storage_key"],
            }
        )
        connection.execute(
            """
            update video.videos
            set duration_ms = %s, playback_json = %s,
                title = case
                    when %s::text is not null
                     and title = 'YouTube video ' || %s::text
                    then %s::text else title end,
                description = coalesce(description, %s::text)
            where id = %s and owner_id = %s
            """,
            (
                media.duration_ms,
                Jsonb(playback),
                clean_title,
                row["youtube_video_id"],
                clean_title,
                clean_description,
                row["video_id"],
                row["owner_id"],
            ),
        )
        for chapter in normalized_chapters:
            connection.execute(
                """
                insert into video.chapters (
                    owner_id, video_id, video_source_id, chapter_index,
                    chapter_kind, title, start_ms, end_ms, provenance_json
                ) values (%s, %s, %s, %s, 'youtube', %s, %s, %s, %s)
                """,
                (
                    row["owner_id"],
                    row["video_id"],
                    row["source_id"],
                    chapter.index,
                    chapter.title,
                    chapter.start_ms,
                    chapter.end_ms,
                    Jsonb({"provider": "youtube", "kind": "official_chapter"}),
                ),
            )
        return PersistedVideoSource(
            row["source_id"],
            "ready",
            row["source_storage_key"],
            row["source_content_hash"],
            row["source_size_bytes"],
            replayed=False,
        )


def _validate_media(media: MediaMetadata) -> None:
    if (
        media.duration_ms <= 0
        or media.width <= 0
        or media.height <= 0
        or media.size_bytes <= 0
        or not media.video_codec.strip()
    ):
        raise ValueError("media metadata is invalid")


def _validate_chapters(chapters: tuple[Chapter, ...], *, duration_ms: int) -> None:
    previous_end = 0
    for index, chapter in enumerate(chapters):
        if (
            chapter.index != index
            or not chapter.title.strip()
            or chapter.start_ms != previous_end
            or chapter.end_ms <= chapter.start_ms
            or chapter.end_ms > duration_ms
        ):
            raise ValueError("video chapters must be ordered contiguous intervals")
        previous_end = chapter.end_ms
    if chapters and previous_end != duration_ms:
        raise ValueError("video chapters must cover the media timeline")
