"""Executable acquire-source stage for URL and uploaded video material."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from psycopg import Connection

from video.acquisition import (
    DEFAULT_MAXIMUM_BYTES,
    ENGLISH_SUBTITLE_LANGUAGES,
    MAXIMUM_HEIGHT,
    VIDEO_FORMAT,
    DownloadedSource,
    MediaMetadata,
    acquire_youtube,
    probe_media,
)
from video.jobs import (
    VideoIngestionJob,
    advance_stage,
    begin_stage_checkpoint,
    complete_stage_checkpoint,
    finish_running_cancellation,
    get_job,
    release_claim,
)
from video.media_store import FilesystemMediaStore, StoredMedia
from video.source_store import load_acquisition_target, record_acquired_media
from video.states import Stage


STAGE_VERSION = "video-acquire-source-v1"
UPLOAD_ACQUISITION_VERSION = "fastapi-stream-v1"
MAXIMUM_ARTIFACT_BYTES = 10 * 1024 * 1024

YouTubeAcquirer = Callable[..., DownloadedSource]
MediaProbe = Callable[..., MediaMetadata]


@dataclass(frozen=True)
class AcquisitionDependencies:
    media_store: FilesystemMediaStore
    youtube_acquirer: YouTubeAcquirer = acquire_youtube
    media_probe: MediaProbe = probe_media
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES
    youtube_acquisition_version: str = "yt-dlp"


def acquisition_dependency_hash(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    dependencies: AcquisitionDependencies,
) -> str:
    target = load_acquisition_target(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
    )
    version = connection.execute(
        """
        select config_hash from video.ingestion_versions
        where id = %s and owner_id = %s and video_id = %s
        """,
        (job.target_version_id, job.owner_id, job.video_id),
    ).fetchone()
    if version is None:
        raise RuntimeError("video ingestion version is unavailable")
    payload = {
        "stage_version": STAGE_VERSION,
        "version_config_hash": version["config_hash"],
        "source_kind": target.source_kind,
        "youtube_video_id": target.youtube_video_id,
        "source_url": target.source_url,
        "staging_content_hash": target.staging_content_hash,
        "staging_size_bytes": target.staging_size_bytes,
        "maximum_bytes": dependencies.maximum_bytes,
        "maximum_height": MAXIMUM_HEIGHT,
        "format": VIDEO_FORMAT,
        "subtitle_languages": ENGLISH_SUBTITLE_LANGUAGES,
        "youtube_acquisition_version": dependencies.youtube_acquisition_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def run_acquire_source(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
    work_dir: Path,
    dependencies: AcquisitionDependencies,
) -> VideoIngestionJob:
    """Run one claimed acquire stage and queue the metadata stage."""

    if job.stage is not Stage.ACQUIRE_SOURCE:
        raise ValueError("video job is not at the acquire-source stage")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        cancelled = _finish_if_cancelled(connection, job=job, worker_id=worker_id)
        if cancelled is not None:
            return cancelled
        target = load_acquisition_target(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )
        dependency_hash = acquisition_dependency_hash(
            connection,
            job=job,
            worker_id=worker_id,
            dependencies=dependencies,
        )
        checkpoint = begin_stage_checkpoint(
            connection,
            job_id=job.id,
            worker_id=worker_id,
            attempt_count=job.attempt_count,
            stage=Stage.ACQUIRE_SOURCE,
            dependency_hash=dependency_hash,
        )
        if checkpoint.reused:
            _verify_manifest(
                dependencies.media_store,
                owner_id=job.owner_id,
                manifest=checkpoint.output_manifest,
            )
            cancelled = _finish_if_cancelled(
                connection, job=job, worker_id=worker_id
            )
            if cancelled is not None:
                return cancelled
            with connection.transaction():
                advance_stage(
                    connection,
                    job_id=job.id,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                    next_stage=Stage.MEDIA_METADATA,
                )
                return release_claim(
                    connection,
                    job_id=job.id,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                )

        if target.source_kind == "youtube":
            acquired = dependencies.youtube_acquirer(
                target.source_url,
                work_dir,
                expected_video_id=target.youtube_video_id,
                maximum_bytes=dependencies.maximum_bytes,
            )
            provider = "yt-dlp"
            acquisition_version = dependencies.youtube_acquisition_version
            media_type = "video/mp4"
            media_path = acquired.video_path
            info_path = acquired.info_path
            captions = acquired.caption_paths
            media = acquired.media
            title, description = acquired.title, acquired.description
            chapters = acquired.chapters
        elif target.source_kind == "upload":
            if (
                target.staging_storage_key is None
                or target.staging_content_hash is None
                or target.staging_size_bytes is None
            ):
                raise RuntimeError("uploaded video has no completed staging object")
            dependencies.media_store.verify_object(
                owner_id=job.owner_id,
                storage_key=target.staging_storage_key,
                expected_size=target.staging_size_bytes,
                expected_hash=target.staging_content_hash,
            )
            media_path = dependencies.media_store.open_path(
                owner_id=job.owner_id,
                storage_key=target.staging_storage_key,
            )
            media = dependencies.media_probe(media_path)
            provider = "upload"
            acquisition_version = UPLOAD_ACQUISITION_VERSION
            media_type = _upload_media_type(connection, job=job)
            info_path, captions = None, ()
            title, description, chapters = None, None, ()
        else:
            raise RuntimeError("unsupported video source kind")

        cancelled = _finish_if_cancelled(connection, job=job, worker_id=worker_id)
        if cancelled is not None:
            return cancelled
        extension = media_path.suffix.lower()
        canonical_video = dependencies.media_store.import_file(
            owner_id=job.owner_id,
            source=media_path,
            namespace="videos",
            extension=extension,
            maximum_bytes=dependencies.maximum_bytes,
        )
        info = (
            dependencies.media_store.import_file(
                owner_id=job.owner_id,
                source=info_path,
                namespace="metadata",
                extension=".json",
                maximum_bytes=MAXIMUM_ARTIFACT_BYTES,
            )
            if info_path is not None
            else None
        )
        caption_objects = [
            dependencies.media_store.import_file(
                owner_id=job.owner_id,
                source=path,
                namespace="transcripts",
                extension=".vtt",
                maximum_bytes=MAXIMUM_ARTIFACT_BYTES,
            )
            for path in captions
        ]
        manifest = _manifest(
            canonical_video=canonical_video,
            info=info,
            captions=caption_objects,
            media=media,
            title=title,
            description=description,
            chapters=chapters,
            provider=provider,
            acquisition_version=acquisition_version,
            media_type=media_type,
        )
        cancelled = _finish_if_cancelled(connection, job=job, worker_id=worker_id)
        if cancelled is not None:
            return cancelled
        with connection.transaction():
            record_acquired_media(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                storage_backend=dependencies.media_store.backend,
                storage_key=canonical_video.storage_key,
                content_hash=canonical_video.content_hash,
                size_bytes=canonical_video.size_bytes,
                media_type=media_type,
                acquisition_provider=provider,
                acquisition_version=acquisition_version,
                provenance={
                    "stage_version": STAGE_VERSION,
                    "metadata": _stored_manifest(info) if info else None,
                    "captions": [_stored_manifest(item) for item in caption_objects],
                },
            )
            complete_stage_checkpoint(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                stage=Stage.ACQUIRE_SOURCE,
                dependency_hash=dependency_hash,
                output_manifest=manifest,
            )
            advance_stage(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
                next_stage=Stage.MEDIA_METADATA,
            )
            return release_claim(
                connection,
                job_id=job.id,
                worker_id=worker_id,
                attempt_count=job.attempt_count,
            )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _finish_if_cancelled(
    connection: Connection,
    *,
    job: VideoIngestionJob,
    worker_id: str,
) -> VideoIngestionJob | None:
    current = get_job(connection, owner_id=job.owner_id, job_id=job.id)
    if not current.cancellation_requested:
        return None
    return finish_running_cancellation(
        connection,
        job_id=job.id,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
    )


def _upload_media_type(connection: Connection, *, job: VideoIngestionJob) -> str:
    row = connection.execute(
        """
        select declared_media_type from video.ingestion_jobs
        where id = %s and owner_id = %s
        """,
        (job.id, job.owner_id),
    ).fetchone()
    if row is None or not row["declared_media_type"]:
        raise RuntimeError("uploaded video media type is unavailable")
    return str(row["declared_media_type"])


def _stored_manifest(value: StoredMedia) -> dict[str, Any]:
    return {
        "storage_backend": "filesystem",
        "storage_key": value.storage_key,
        "content_hash": value.content_hash,
        "size_bytes": value.size_bytes,
    }


def _manifest(
    *,
    canonical_video: StoredMedia,
    info: StoredMedia | None,
    captions: list[StoredMedia],
    media: MediaMetadata,
    title: str | None,
    description: str | None,
    chapters: tuple[Any, ...],
    provider: str,
    acquisition_version: str,
    media_type: str,
) -> dict[str, Any]:
    return {
        "stage_version": STAGE_VERSION,
        "source": {**_stored_manifest(canonical_video), "media_type": media_type},
        "metadata": _stored_manifest(info) if info else None,
        "captions": [_stored_manifest(item) for item in captions],
        "media": asdict(media),
        "title": title,
        "description": description,
        "chapters": [asdict(chapter) for chapter in chapters],
        "acquisition": {
            "provider": provider,
            "version": acquisition_version,
        },
    }


def _verify_manifest(
    store: FilesystemMediaStore,
    *,
    owner_id: Any,
    manifest: dict[str, Any],
) -> None:
    objects = [manifest.get("source"), manifest.get("metadata")]
    objects.extend(manifest.get("captions") or [])
    for value in objects:
        if value is None:
            continue
        if value.get("storage_backend") != store.backend:
            raise RuntimeError("video checkpoint uses an unavailable storage backend")
        store.verify_object(
            owner_id=owner_id,
            storage_key=value["storage_key"],
            expected_size=int(value["size_bytes"]),
            expected_hash=value["content_hash"],
        )
