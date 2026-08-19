"""Owner-scoped PostgreSQL repository for standalone video material."""

from dataclasses import dataclass
import hashlib
import json
import os
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.resources import maximum_resource_bytes
from video.sources import display_filename, parse_youtube_url, upload_extension


DEFAULT_INGESTION_CAP_USD = "0.500000"
DEFAULT_MAXIMUM_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
INGESTION_CONFIG = {
    "format": "video-ingestion-v1",
    "transcript": "youtube-captions-then-openrouter-v1",
    "visual": "model-classified-selective-v1",
}
UNSET = object()


class VideoNotFoundError(LookupError):
    pass


class VideoConflictError(RuntimeError):
    pass


class VideoAlreadyExistsError(VideoConflictError):
    pass


@dataclass(frozen=True)
class VideoCreation:
    video_id: UUID
    source_id: UUID
    version_id: UUID
    job_id: UUID
    source_kind: str
    job_status: str
    created: bool
    upload_storage_key: str | None = None


@dataclass(frozen=True)
class VideoUploadTarget:
    job_id: UUID
    video_id: UUID
    source_id: UUID
    status: str
    storage_key: str
    declared_size_bytes: int
    declared_media_type: str
    staging_size_bytes: int | None
    staging_content_hash: str | None


@dataclass(frozen=True)
class VideoUploadCompletion:
    job_id: UUID
    video_id: UUID
    status: str
    size_bytes: int
    replayed: bool


def maximum_upload_bytes() -> int:
    return int(os.getenv("VIDEO_MAX_UPLOAD_BYTES", DEFAULT_MAXIMUM_UPLOAD_BYTES))


def _config_hash() -> str:
    encoded = json.dumps(
        INGESTION_CONFIG, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _existing_creation(
    connection: Connection, owner: UUID, key: UUID
) -> VideoCreation | None:
    row = connection.execute(
        """
        select v.id as video_id, s.id as source_id, s.source_kind,
               j.target_version_id as version_id, j.id as job_id,
               j.status as job_status, j.staging_storage_key
        from video.ingestion_jobs as j
        join video.videos as v
          on v.id = j.video_id and v.owner_id = j.owner_id
        join video.video_sources as s
          on s.video_id = v.id and s.owner_id = v.owner_id and s.is_primary
        where j.owner_id = %s and j.idempotency_key = %s
        """,
        (owner, key),
    ).fetchone()
    if row is None:
        return None
    return VideoCreation(
        video_id=row["video_id"],
        source_id=row["source_id"],
        version_id=row["version_id"],
        job_id=row["job_id"],
        source_kind=row["source_kind"],
        job_status=row["job_status"],
        created=False,
        upload_storage_key=row["staging_storage_key"],
    )


def _create_graph(
    connection: Connection,
    *,
    owner: UUID,
    key: UUID,
    source_kind: str,
    title: str,
    description: str | None,
    source_url: str | None,
    youtube_video_id: str | None,
    original_filename: str | None,
    media_type: str | None,
    declared_size: int | None,
    staging_storage_key: str | None,
    job_id: UUID | None = None,
) -> VideoCreation:
    video_id, source_id, version_id = uuid4(), uuid4(), uuid4()
    job_id = job_id or uuid4()
    playback = (
        {"kind": "youtube", "youtube_video_id": youtube_video_id}
        if source_kind == "youtube"
        else {"kind": "local"}
    )
    connection.execute(
        """
        insert into video.videos (
            id, owner_id, title, description, source_kind, playback_json
        ) values (%s, %s, %s, %s, %s, %s)
        """,
        (video_id, owner, title, description, source_kind, Jsonb(playback)),
    )
    source = connection.execute(
        """
        insert into video.video_sources (
            id, owner_id, video_id, source_kind, status, source_url,
            youtube_video_id, original_filename, media_type,
            media_metadata_json
        ) values (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
        on conflict (owner_id, youtube_video_id)
            where youtube_video_id is not null do nothing
        returning id
        """,
        (
            source_id,
            owner,
            video_id,
            source_kind,
            source_url,
            youtube_video_id,
            original_filename,
            media_type,
            Jsonb({"declared_size_bytes": declared_size}),
        ),
    ).fetchone()
    if source is None:
        raise VideoAlreadyExistsError("video already exists")

    config_hash = _config_hash()
    connection.execute(
        """
        insert into video.ingestion_versions (
            id, owner_id, video_id, video_source_id, version_number,
            config_json, config_hash, cost_cap_usd
        ) values (%s, %s, %s, %s, 1, %s, %s, %s)
        """,
        (
            version_id,
            owner,
            video_id,
            source_id,
            Jsonb(INGESTION_CONFIG),
            config_hash,
            DEFAULT_INGESTION_CAP_USD,
        ),
    )
    job_status = "queued" if source_kind == "youtube" else "awaiting_upload"
    connection.execute(
        """
        insert into video.ingestion_jobs (
            id, owner_id, video_id, target_version_id, idempotency_key,
            status, stage, staging_storage_backend, staging_storage_key,
            declared_size_bytes, declared_media_type,
            cost_cap_usd, provenance_json
        ) values (
            %s, %s, %s, %s, %s, %s, 'acquire_source', %s, %s,
            %s, %s, %s, %s
        )
        """,
        (
            job_id,
            owner,
            video_id,
            version_id,
            key,
            job_status,
            "filesystem" if staging_storage_key else None,
            staging_storage_key,
            declared_size,
            media_type,
            DEFAULT_INGESTION_CAP_USD,
            Jsonb({"declared_size_bytes": declared_size, "media_type": media_type}),
        ),
    )
    connection.execute(
        """
        insert into video.ingestion_job_events (
            owner_id, job_id, event_type, status, stage
        ) values (%s, %s, 'created', %s, 'acquire_source')
        """,
        (owner, job_id, job_status),
    )
    return VideoCreation(
        video_id=video_id,
        source_id=source_id,
        version_id=version_id,
        job_id=job_id,
        source_kind=source_kind,
        job_status=job_status,
        created=True,
        upload_storage_key=staging_storage_key,
    )


def create_youtube_video(
    connection: Connection,
    *,
    owner_id: str | UUID,
    idempotency_key: str | UUID,
    url: str,
    title: str | None = None,
    description: str | None = None,
) -> VideoCreation:
    owner, key = parse_owner_id(owner_id), UUID(str(idempotency_key))
    existing = _existing_creation(connection, owner, key)
    if existing is not None:
        return existing
    source = parse_youtube_url(url)
    clean_title = (title or f"YouTube video {source.video_id}").strip()
    if not clean_title:
        raise ValueError("video title cannot be blank")
    return _create_graph(
        connection,
        owner=owner,
        key=key,
        source_kind="youtube",
        title=clean_title,
        description=description.strip() if description else None,
        source_url=source.canonical_url,
        youtube_video_id=source.video_id,
        original_filename=None,
        media_type=None,
        declared_size=None,
        staging_storage_key=None,
    )


def initialize_video_upload(
    connection: Connection,
    *,
    owner_id: str | UUID,
    idempotency_key: str | UUID,
    original_filename: str,
    media_type: str,
    declared_size_bytes: int,
    title: str | None = None,
    description: str | None = None,
) -> VideoCreation:
    owner, key = parse_owner_id(owner_id), UUID(str(idempotency_key))
    existing = _existing_creation(connection, owner, key)
    if existing is not None:
        return existing
    filename = display_filename(original_filename)
    extension = upload_extension(filename, media_type)
    if declared_size_bytes <= 0 or declared_size_bytes > maximum_upload_bytes():
        raise ValueError("video upload size is outside the supported range")
    job_id = uuid4()
    staging_key = f"{owner}/staging/{job_id}/original{extension}"
    return _create_graph(
        connection,
        owner=owner,
        key=key,
        source_kind="upload",
        title=(title or filename).strip(),
        description=description.strip() if description else None,
        source_url=None,
        youtube_video_id=None,
        original_filename=filename,
        media_type=media_type,
        declared_size=declared_size_bytes,
        staging_storage_key=staging_key,
        job_id=job_id,
    )


# What a replacement version inherits instead of recomputing. Everything from
# the source through visual interpretation is decided by content that has not
# changed, and two of these stages are the only ones that pay a model. The
# resource stage onward always re-runs: that is where a newly attached
# document enters, and what the evidence and answers are rebuilt from.
CARRIED_STAGES = (
    "acquire_source",
    "media_metadata",
    "transcript",
    "frame_selection",
    "ocr",
    "visual_analysis",
    "spatial_regions",
)


def reingest_video(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    idempotency_key: str | UUID,
) -> VideoCreation:
    """Build a replacement version, reusing every compatible earlier stage.

    Attaching slides to a lecture that already finished must not re-download
    the video, re-transcribe it, or pay the visual model again. The new
    version inherits those stages and their derived rows, so the work that
    actually re-runs is reading the new document and rebuilding the retrieval
    layer on top of it.

    The published version stays queryable throughout: the swap happens once,
    atomically, when the replacement passes its own quality gates.
    """

    owner, video, key = (
        parse_owner_id(owner_id),
        UUID(str(video_id)),
        UUID(str(idempotency_key)),
    )
    existing = _existing_creation(connection, owner, key)
    if existing is not None:
        return existing
    with connection.transaction():
        current = connection.execute(
            """
            select v.id, v.source_kind, v.current_ingestion_version_id,
                   s.id as source_id, s.status as source_status
            from video.videos as v
            join video.video_sources as s
              on s.video_id = v.id and s.owner_id = v.owner_id and s.is_primary
            where v.id = %s and v.owner_id = %s
            for update of v
            """,
            (video, owner),
        ).fetchone()
        if current is None:
            raise VideoNotFoundError("video does not exist")
        if current["source_status"] != "ready":
            raise VideoConflictError("video source is not acquired yet")
        if current["current_ingestion_version_id"] is None:
            raise VideoConflictError("video has no published version to rebuild")
        previous = connection.execute(
            """
            -- The job that actually received the bytes, not merely the most
            -- recent one: a failed rebuild carries no staging identity, and
            -- copying its emptiness made the next rebuild re-acquire a source
            -- whose staging object no longer exists.
            select staging_storage_backend, staging_storage_key,
                   staging_content_hash, staging_size_bytes, upload_completed_at,
                   declared_size_bytes, declared_media_type
            from video.ingestion_jobs
            where owner_id = %s and video_id = %s
              and upload_completed_at is not null
            order by created_at desc limit 1
            """,
            (owner, video),
        ).fetchone()
        active = connection.execute(
            """
            select 1 from video.ingestion_jobs
            where owner_id = %s and video_id = %s
              and status in (
                  'awaiting_upload', 'queued', 'running', 'retry_scheduled'
              )
            """,
            (owner, video),
        ).fetchone()
        if active is not None:
            raise VideoConflictError("video ingestion is already in progress")

        source_version = current["current_ingestion_version_id"]
        version_id, job_id = uuid4(), uuid4()
        connection.execute(
            """
            insert into video.ingestion_versions (
                id, owner_id, video_id, video_source_id, version_number,
                config_json, config_hash, cost_cap_usd
            )
            select %s, %s, %s, %s, coalesce(max(version_number), 0) + 1,
                   %s, %s, %s
            from video.ingestion_versions
            where owner_id = %s and video_id = %s
            """,
            (
                version_id,
                owner,
                video,
                current["source_id"],
                Jsonb(INGESTION_CONFIG),
                _config_hash(),
                DEFAULT_INGESTION_CAP_USD,
                owner,
                video,
            ),
        )
        carried = _carry_forward(
            connection,
            owner=owner,
            video=video,
            source_version=source_version,
            target_version=version_id,
        )
        connection.execute(
            """
            insert into video.ingestion_jobs (
                id, owner_id, video_id, target_version_id, idempotency_key,
                status, stage, staging_storage_backend, staging_storage_key,
                staging_content_hash, staging_size_bytes, upload_completed_at,
                declared_size_bytes, declared_media_type, cost_cap_usd,
                provenance_json
            ) values (
                %s, %s, %s, %s, %s, 'queued', 'acquire_source', %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            """,
            (
                job_id,
                owner,
                video,
                version_id,
                key,
                # The acquire stage identifies an uploaded source by the bytes
                # that were staged for it. Without carrying that identity the
                # inherited checkpoint does not match, and the rebuild tries to
                # re-acquire an upload whose staging object is long gone.
                (previous or {}).get("staging_storage_backend"),
                (previous or {}).get("staging_storage_key"),
                (previous or {}).get("staging_content_hash"),
                (previous or {}).get("staging_size_bytes"),
                # Required alongside the staged size and hash: the schema
                # treats the three as one fact about a completed upload.
                (previous or {}).get("upload_completed_at"),
                (previous or {}).get("declared_size_bytes"),
                (previous or {}).get("declared_media_type"),
                DEFAULT_INGESTION_CAP_USD,
                Jsonb({"reingest_of_version": str(source_version), **carried}),
            ),
        )
        connection.execute(
            """
            insert into video.ingestion_job_events (
                owner_id, job_id, event_type, status, stage, message
            ) values (%s, %s, 'created', 'queued', 'acquire_source', %s)
            """,
            (owner, job_id, "Rebuilding with the current linked documents"),
        )
    return VideoCreation(
        video_id=video,
        source_id=current["source_id"],
        version_id=version_id,
        job_id=job_id,
        source_kind=current["source_kind"],
        job_status="queued",
        created=True,
        upload_storage_key=None,
    )


def _carry_forward(
    connection: Connection,
    *,
    owner: UUID,
    video: UUID,
    source_version: UUID,
    target_version: UUID,
) -> dict[str, int]:
    """Copy version-scoped derived rows and their completed checkpoints.

    The rows are metadata pointing at content-addressed objects that already
    exist, so copying them is cheap and keeps every stage's own query — which
    filters by ingestion version — working unchanged.
    """

    frames: dict[int, int] = {}
    for row in connection.execute(
        """
        select * from video.frames
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        order by frame_index
        """,
        (owner, video, source_version),
    ).fetchall():
        frames[row["id"]] = connection.execute(
            """
            insert into video.frames (
                owner_id, video_id, ingestion_version_id, frame_index,
                timestamp_ms, selection_reasons, full_storage_backend,
                full_storage_key, full_content_hash, preview_storage_backend,
                preview_storage_key, preview_content_hash, perceptual_hash,
                width, height, ocr_text, ocr_confidence, ocr_engine,
                ocr_version
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            ) returning id
            """,
            (
                owner,
                video,
                target_version,
                row["frame_index"],
                row["timestamp_ms"],
                row["selection_reasons"],
                row["full_storage_backend"],
                row["full_storage_key"],
                row["full_content_hash"],
                row["preview_storage_backend"],
                row["preview_storage_key"],
                row["preview_content_hash"],
                row["perceptual_hash"],
                row["width"],
                row["height"],
                row["ocr_text"],
                row["ocr_confidence"],
                row["ocr_engine"],
                row["ocr_version"],
            ),
        ).fetchone()["id"]

    observations: dict[int, int] = {}
    for row in connection.execute(
        """
        select * from video.visual_observations
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        order by id
        """,
        (owner, video, source_version),
    ).fetchall():
        observations[row["id"]] = connection.execute(
            """
            insert into video.visual_observations (
                owner_id, video_id, ingestion_version_id, frame_id, status,
                visual_types, summary, visible_text, technical_details_json,
                importance, confidence, model_name, model_revision,
                prompt_version, input_hash, cost_usd
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0
            ) returning id
            """,
            (
                owner,
                video,
                target_version,
                frames[row["frame_id"]],
                row["status"],
                row["visual_types"],
                row["summary"],
                row["visible_text"],
                Jsonb(row["technical_details_json"]),
                row["importance"],
                row["confidence"],
                row["model_name"],
                row["model_revision"],
                row["prompt_version"],
                row["input_hash"],
            ),
        ).fetchone()["id"]

    regions = 0
    for row in connection.execute(
        """
        select * from video.visual_regions
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        order by id
        """,
        (owner, video, source_version),
    ).fetchall():
        connection.execute(
            """
            insert into video.visual_regions (
                owner_id, video_id, ingestion_version_id, frame_id,
                visual_observation_id, region_index, region_type, x, y, width,
                height, summary, crop_storage_backend, crop_storage_key,
                crop_content_hash, model_name, model_revision, prompt_version,
                input_hash, confidence, cost_usd
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, 0
            )
            """,
            (
                owner,
                video,
                target_version,
                frames[row["frame_id"]],
                observations[row["visual_observation_id"]],
                row["region_index"],
                row["region_type"],
                row["x"],
                row["y"],
                row["width"],
                row["height"],
                row["summary"],
                row["crop_storage_backend"],
                row["crop_storage_key"],
                row["crop_content_hash"],
                row["model_name"],
                row["model_revision"],
                row["prompt_version"],
                row["input_hash"],
                row["confidence"],
            ),
        )
        regions += 1

    events = 0
    for row in connection.execute(
        """
        select * from video.visual_events
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        order by id
        """,
        (owner, video, source_version),
    ).fetchall():
        connection.execute(
            """
            insert into video.visual_events (
                owner_id, video_id, ingestion_version_id, start_frame_id,
                end_frame_id, event_type, start_ms, end_ms, summary,
                details_json, model_name, model_revision, prompt_version,
                input_hash, cost_usd
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0
            )
            """,
            (
                owner,
                video,
                target_version,
                frames[row["start_frame_id"]],
                frames[row["end_frame_id"]],
                row["event_type"],
                row["start_ms"],
                row["end_ms"],
                row["summary"],
                Jsonb(row["details_json"]),
                row["model_name"],
                row["model_revision"],
                row["prompt_version"],
                row["input_hash"],
            ),
        )
        events += 1

    checkpoints = connection.execute(
        """
        insert into video.ingestion_stage_checkpoints (
            owner_id, video_id, ingestion_version_id, stage, status,
            dependency_hash, output_manifest_json, provenance_json,
            actual_cost_usd, attempt_count, reused_from_checkpoint_id,
            started_at, completed_at
        )
        select owner_id, video_id, %s, stage, 'complete', dependency_hash,
               output_manifest_json, provenance_json, 0, 0, id, now(), now()
        from video.ingestion_stage_checkpoints
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
          and status = 'complete' and stage = any(%s)
        returning id
        """,
        (
            target_version,
            owner,
            video,
            source_version,
            list(CARRIED_STAGES),
        ),
    ).fetchall()
    return {
        "carried_frames": len(frames),
        "carried_observations": len(observations),
        "carried_regions": regions,
        "carried_events": events,
        "carried_stages": len(checkpoints),
    }


def replace_derived_chapters(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    chapters: Sequence[Any],
) -> int:
    """Replace this video's derived outline, leaving any other kind alone.

    Returns the number written, or -1 when the source published its own
    chapters and nothing was done. A list the source shipped, or one a person
    typed, is not ours to replace with a guess — deriving is what happens when
    there is no such list, not a correction of one.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    source = connection.execute(
        """
        select id from video.video_sources
        where owner_id = %s and video_id = %s and is_primary
        """,
        (owner, video),
    ).fetchone()
    if source is None:
        raise VideoNotFoundError("video has no primary source")

    authored = connection.execute(
        """
        select count(*) as count from video.chapters
        where owner_id = %s and video_id = %s and chapter_kind <> 'derived'
        """,
        (owner, video),
    ).fetchone()["count"]
    if authored:
        return -1

    connection.execute(
        """
        delete from video.chapters
        where owner_id = %s and video_id = %s and chapter_kind = 'derived'
        """,
        (owner, video),
    )
    for chapter in chapters:
        connection.execute(
            """
            insert into video.chapters (
                owner_id, video_id, video_source_id, chapter_index,
                chapter_kind, title, start_ms, end_ms, provenance_json
            ) values (%s, %s, %s, %s, 'derived', %s, %s, %s, %s)
            """,
            (
                owner,
                video,
                source["id"],
                chapter.index,
                chapter.title,
                chapter.start_ms,
                chapter.end_ms,
                Jsonb(chapter.provenance()),
            ),
        )
    return len(chapters)


VIDEO_SELECT = """
    select v.id, v.owner_id, v.title, v.description, v.source_kind, v.duration_ms,
           v.readiness_status, v.playback_json, v.created_at, v.updated_at,
           v.ready_at, v.current_ingestion_version_id,
           s.id as source_id, s.status as source_status, s.source_url,
           s.youtube_video_id, s.original_filename,
           current_version.version_number,
           current_version.quality_gates_json,
           latest_job.id as latest_job_id, latest_job.status as latest_job_status,
           latest_job.stage as latest_job_stage,
           latest_job.progress_completed as latest_job_progress_completed,
           latest_job.progress_total as latest_job_progress_total,
           latest_job.progress_unit as latest_job_progress_unit,
           latest_job.actual_cost_usd as latest_job_actual_cost_usd,
           latest_job.cost_cap_usd as latest_job_cost_cap_usd,
           latest_job.attempt_count as latest_job_attempt_count,
           latest_job.max_attempts as latest_job_max_attempts,
           latest_job.target_version_id as latest_job_target_version_id,
           latest_job.last_error_code as latest_job_last_error_code,
           latest_job.last_error_message as latest_job_last_error_message,
           latest_job.last_error_retryable as latest_job_last_error_retryable,
           latest_job.cancellation_requested_at
               as latest_job_cancellation_requested_at,
           latest_job.created_at as latest_job_created_at,
           latest_job.started_at as latest_job_started_at,
           latest_job.updated_at as latest_job_updated_at,
           latest_job.completed_at as latest_job_completed_at,
           -- Mirrors `delete_video`, so the interface offers removal only
           -- where it would succeed rather than discovering the constraint
           -- through a 409. An acquired source no longer blocks it: the
           -- delete now unlinks the media it leaves unreferenced.
           (
               not exists (
                   select 1 from video.course_lectures as lecture
                   where lecture.owner_id = v.owner_id and lecture.video_id = v.id
               )
               and not exists (
                   select 1 from video.ingestion_jobs as running
                   where running.owner_id = v.owner_id
                     and running.video_id = v.id
                     and running.status = 'running'
               )
           ) as deletable,
           poster.id as poster_frame_id,
           (
               select count(*) from video.chapters as chapter
               where chapter.owner_id = v.owner_id and chapter.video_id = v.id
           ) as chapter_count,
           (
               select count(*)
               from video.video_resources as attachment
               join video.resources as resource
                 on resource.id = attachment.resource_id
                and resource.owner_id = attachment.owner_id
               where attachment.owner_id = v.owner_id
                 and attachment.video_id = v.id
                 and attachment.role = 'slides'
                 and resource.status = 'ready'
           ) as slide_count
    from video.videos as v
    join video.video_sources as s
      on s.video_id = v.id and s.owner_id = v.owner_id and s.is_primary
    left join video.ingestion_versions as current_version
      on current_version.id = v.current_ingestion_version_id
     and current_version.video_id = v.id
     and current_version.owner_id = v.owner_id
    left join lateral (
        select * from video.ingestion_jobs
        where owner_id = v.owner_id and video_id = v.id
        order by created_at desc, id desc limit 1
    ) as latest_job on true
    -- The lecture's face, from frames the published version already stored.
    --
    -- Nearest the midpoint rather than first: the opening seconds of a lecture
    -- are a title card, a black frame, or someone walking to the lectern, and
    -- a library of those is a library of identical rectangles. Restricted to
    -- the current version, so a lecture still processing has no poster rather
    -- than a stale one from the run being replaced.
    left join lateral (
        select frame.id
        from video.frames as frame
        where frame.owner_id = v.owner_id
          and frame.video_id = v.id
          and frame.ingestion_version_id = v.current_ingestion_version_id
        order by abs(frame.timestamp_ms - coalesce(v.duration_ms, 0) / 2),
                 frame.timestamp_ms
        limit 1
    ) as poster on true
"""


def list_standalone_videos(
    connection: Connection, *, owner_id: str | UUID, limit: int = 50
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    return connection.execute(
        VIDEO_SELECT
        + """
        where v.owner_id = %s
          and not exists (
              select 1 from video.course_lectures as lecture
              where lecture.owner_id = v.owner_id and lecture.video_id = v.id
          )
        order by v.updated_at desc, v.id desc limit %s
        """,
        (parse_owner_id(owner_id), min(limit, 100)),
    ).fetchall()


def load_standalone_video(
    connection: Connection, video_id: str | UUID, *, owner_id: str | UUID
) -> dict[str, Any] | None:
    return connection.execute(
        VIDEO_SELECT
        + """
        where v.owner_id = %s and v.id = %s
          and not exists (
              select 1 from video.course_lectures as lecture
              where lecture.owner_id = v.owner_id and lecture.video_id = v.id
          )
        """,
        (parse_owner_id(owner_id), UUID(str(video_id))),
    ).fetchone()


def update_video_metadata(
    connection: Connection,
    video_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str | object = UNSET,
    description: str | None | object = UNSET,
) -> dict[str, Any] | None:
    assignments: list[str] = []
    parameters: list[Any] = []
    if title is not UNSET:
        cleaned = str(title).strip()
        if not cleaned:
            raise ValueError("video title cannot be blank")
        assignments.append("title = %s")
        parameters.append(cleaned)
    if description is not UNSET:
        assignments.append("description = %s")
        parameters.append(description.strip() if description else None)
    if assignments:
        parameters.extend([parse_owner_id(owner_id), UUID(str(video_id))])
        connection.execute(
            f"""
            update video.videos set {", ".join(assignments)}
            where owner_id = %s and id = %s
              and not exists (
                  select 1 from video.course_lectures as lecture
                  where lecture.owner_id = video.videos.owner_id
                    and lecture.video_id = video.videos.id
              )
            """,
            parameters,
        )
    return load_standalone_video(connection, video_id, owner_id=owner_id)


# Every column naming a media object that belongs to one video. Resources are
# deliberately absent: a document is attached to a video rather than owned by
# it, outlives the video, and may be attached to another one.
_VIDEO_MEDIA_KEYS = """
    select storage_key as key from video.video_sources
     where owner_id = %(owner)s and video_id = %(video)s and storage_key is not null
    union select storage_key from video.caption_uploads
     where owner_id = %(owner)s and video_id = %(video)s
    union select storage_key from video.transcript_sources
     where owner_id = %(owner)s and video_id = %(video)s and storage_key is not null
    union select full_storage_key from video.frames
     where owner_id = %(owner)s and video_id = %(video)s
    union select preview_storage_key from video.frames
     where owner_id = %(owner)s and video_id = %(video)s
    union select region.crop_storage_key from video.visual_regions as region
      join video.frames as frame
        on frame.id = region.frame_id and frame.owner_id = region.owner_id
     where region.owner_id = %(owner)s and frame.video_id = %(video)s
    union select staging_storage_key from video.ingestion_jobs
     where owner_id = %(owner)s and video_id = %(video)s
       and staging_storage_key is not null
"""

# The same objects across everything this owner still has, resources included.
# Canonical media is addressed by content hash, so two videos that were given
# the same file share one object on disk — which is not a hypothetical: a
# caption uploaded to two videos is one key with two referents.
OWNER_MEDIA_KEYS = """
    select storage_key as key from video.video_sources
     where owner_id = %(owner)s and storage_key is not null
    union select storage_key from video.caption_uploads where owner_id = %(owner)s
    union select storage_key from video.transcript_sources
     where owner_id = %(owner)s and storage_key is not null
    union select full_storage_key from video.frames where owner_id = %(owner)s
    union select preview_storage_key from video.frames where owner_id = %(owner)s
    union select crop_storage_key from video.visual_regions where owner_id = %(owner)s
    union select staging_storage_key from video.ingestion_jobs
     where owner_id = %(owner)s and staging_storage_key is not null
    union select storage_key from video.resources
     where owner_id = %(owner)s and storage_key is not null
    union select render_storage_key from video.resource_pages
     where owner_id = %(owner)s and render_storage_key is not null
"""


@dataclass(frozen=True)
class VideoDeletion:
    """What a deleted video left behind, and what may be deleted with it."""

    video_id: UUID
    orphaned_keys: tuple[str, ...]
    retained_keys: tuple[str, ...]


def delete_video(
    connection: Connection, video_id: str | UUID, *, owner_id: str | UUID
) -> VideoDeletion | None:
    """Delete a video and report which of its media objects are now unused.

    A failed ingestion that acquired its source before failing used to be
    undeletable, because removing the row would have stranded gigabytes on the
    volume with nothing left pointing at them. Refusing was the safe answer
    while nothing could delete media; it is the wrong one now that the reader
    is left with a permanently stuck card on a volume sized for a handful of
    lectures.

    Media is not unlinked here. The keys are collected before the delete and
    re-checked against everything the owner still has *after* it, inside the
    same transaction, so the caller unlinks only once the rows that named them
    are committed. That order can orphan bytes if the unlink then fails, and a
    later sweep can find those. The other order deletes bytes a surviving row
    still points at, and nothing can find those.

    Returns None when there is no such video, or when it is a course lecture or
    has a job running — a running job holds a lease and writes into rows this
    would delete underneath it.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    parameters = {"owner": owner, "video": video}
    held = {
        row["key"]
        for row in connection.execute(_VIDEO_MEDIA_KEYS, parameters).fetchall()
        if row["key"]
    }
    row = connection.execute(
        """
        delete from video.videos as v
        where v.owner_id = %(owner)s and v.id = %(video)s
          and not exists (
              select 1 from video.course_lectures as lecture
              where lecture.owner_id = v.owner_id and lecture.video_id = v.id
          )
          and not exists (
              select 1 from video.ingestion_jobs as job
              where job.owner_id = v.owner_id and job.video_id = v.id
                and job.status = 'running'
          )
        returning id
        """,
        parameters,
    ).fetchone()
    if row is None:
        return None
    retained = (
        {
            item["key"]
            for item in connection.execute(
                f"select key from ({OWNER_MEDIA_KEYS}) as referenced "
                "where key = any(%(keys)s)",
                parameters | {"keys": sorted(held)},
            ).fetchall()
        }
        if held
        else set()
    )
    return VideoDeletion(
        video_id=video,
        orphaned_keys=tuple(sorted(held - retained)),
        retained_keys=tuple(sorted(retained)),
    )


def list_job_events(
    connection: Connection,
    job_id: str | UUID,
    *,
    owner_id: str | UUID,
    limit: int = 100,
) -> list[dict[str, Any]] | None:
    owner, job = parse_owner_id(owner_id), UUID(str(job_id))
    exists = connection.execute(
        "select 1 from video.ingestion_jobs where owner_id = %s and id = %s",
        (owner, job),
    ).fetchone()
    if exists is None:
        return None
    return connection.execute(
        """
        select id, event_type, status, stage, message, created_at
        from video.ingestion_job_events
        where owner_id = %s and job_id = %s
        order by id limit %s
        """,
        (owner, job, min(max(limit, 1), 500)),
    ).fetchall()


def load_ingestion_job(
    connection: Connection, job_id: str | UUID, *, owner_id: str | UUID
) -> dict[str, Any] | None:
    return connection.execute(
        """
        select id, video_id, target_version_id, status, stage,
               progress_completed, progress_total, progress_unit,
               attempt_count, max_attempts, cancellation_requested_at,
               last_error_code, last_error_retryable, actual_cost_usd,
               cost_cap_usd, created_at, started_at, updated_at, completed_at
        from video.ingestion_jobs
        where owner_id = %s and id = %s
        """,
        (parse_owner_id(owner_id), UUID(str(job_id))),
    ).fetchone()


def load_video_upload_target(
    connection: Connection, job_id: str | UUID, *, owner_id: str | UUID
) -> VideoUploadTarget | None:
    row = connection.execute(
        """
        select j.id as job_id, j.video_id, j.status, j.staging_storage_key,
               j.declared_size_bytes, j.declared_media_type,
               j.staging_size_bytes, j.staging_content_hash,
               s.id as source_id
        from video.ingestion_jobs as j
        join video.video_sources as s
          on s.owner_id = j.owner_id and s.video_id = j.video_id
         and s.is_primary and s.source_kind = 'upload'
        where j.owner_id = %s and j.id = %s
        """,
        (parse_owner_id(owner_id), UUID(str(job_id))),
    ).fetchone()
    if row is None or not row["staging_storage_key"]:
        return None
    return VideoUploadTarget(
        job_id=row["job_id"],
        video_id=row["video_id"],
        source_id=row["source_id"],
        status=row["status"],
        storage_key=row["staging_storage_key"],
        declared_size_bytes=row["declared_size_bytes"],
        declared_media_type=row["declared_media_type"],
        staging_size_bytes=row["staging_size_bytes"],
        staging_content_hash=row["staging_content_hash"],
    )


def complete_video_upload(
    connection: Connection,
    job_id: str | UUID,
    *,
    owner_id: str | UUID,
    storage_key: str,
    content_hash: str,
    size_bytes: int,
    media_type: str,
) -> VideoUploadCompletion | None:
    """Record a fully streamed upload and make its job claimable.

    This verifies transport facts only. The source deliberately stays pending
    until the worker probes the container and promotes it to canonical media.
    """

    owner, job = parse_owner_id(owner_id), UUID(str(job_id))
    row = connection.execute(
        """
        select j.id as job_id, j.video_id, j.status, j.staging_storage_key,
               j.declared_size_bytes, j.declared_media_type,
               j.staging_size_bytes, j.staging_content_hash,
               s.id as source_id
        from video.ingestion_jobs as j
        join video.video_sources as s
          on s.owner_id = j.owner_id and s.video_id = j.video_id
         and s.is_primary and s.source_kind = 'upload'
        where j.owner_id = %s and j.id = %s
        for update of j, s
        """,
        (owner, job),
    ).fetchone()
    if row is None:
        return None
    expected = (
        row["staging_storage_key"],
        row["declared_size_bytes"],
        row["declared_media_type"],
    )
    if (storage_key, size_bytes, media_type) != expected:
        raise VideoConflictError("uploaded video does not match its reservation")
    if row["status"] == "queued":
        if (
            row["staging_size_bytes"] == size_bytes
            and row["staging_content_hash"] == content_hash
        ):
            return VideoUploadCompletion(
                job_id=job,
                video_id=row["video_id"],
                status="queued",
                size_bytes=size_bytes,
                replayed=True,
            )
        raise VideoConflictError("video upload was already completed")
    if row["status"] != "awaiting_upload":
        raise VideoConflictError("video upload is not awaiting bytes")

    connection.execute(
        """
        update video.video_sources
        set storage_backend = 'filesystem', storage_key = %s,
            content_hash = %s, size_bytes = %s, media_type = %s,
            provenance_json = provenance_json || jsonb_build_object(
                'upload_transport', 'fastapi-stream-v1'
            )
        where owner_id = %s and id = %s
        """,
        (storage_key, content_hash, size_bytes, media_type, owner, row["source_id"]),
    )
    connection.execute(
        """
        update video.ingestion_jobs
        set status = 'queued', next_attempt_at = now(),
            staging_size_bytes = %s, staging_content_hash = %s,
            upload_completed_at = now(),
            provenance_json = provenance_json || jsonb_build_object(
                'upload', jsonb_build_object(
                    'size_bytes', %s::bigint, 'content_hash', %s::text,
                    'transport', 'fastapi-stream-v1'
                )
            )
        where owner_id = %s and id = %s
        """,
        (size_bytes, content_hash, size_bytes, content_hash, owner, job),
    )
    connection.execute(
        """
        insert into video.ingestion_job_events (
            owner_id, job_id, event_type, status, stage, message
        ) values (
            %s, %s, 'upload_completed', 'queued', 'acquire_source',
            'Video upload received; awaiting media validation'
        )
        """,
        (owner, job),
    )
    return VideoUploadCompletion(
        job_id=job,
        video_id=row["video_id"],
        status="queued",
        size_bytes=size_bytes,
        replayed=False,
    )


def list_video_chapters(
    connection: Connection, video_id: str | UUID, *, owner_id: str | UUID
) -> list[dict[str, Any]] | None:
    if load_standalone_video(connection, video_id, owner_id=owner_id) is None:
        return None
    return connection.execute(
        """
        select id, chapter_index, chapter_kind, title, start_ms, end_ms
        from video.chapters
        where owner_id = %s and video_id = %s
        order by chapter_index
        """,
        (parse_owner_id(owner_id), UUID(str(video_id))),
    ).fetchall()


def _validate_resource_values(resource_kind: str, role: str) -> None:
    if resource_kind not in {"pdf", "external_link"}:
        raise ValueError("unsupported resource kind")
    if role not in {"slides", "notes", "reference"}:
        raise ValueError("unsupported resource role")


def create_url_resource(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    resource_kind: str,
    title: str,
    source_url: str,
    role: str,
    required: bool = False,
    origin: str = "url",
) -> dict[str, Any]:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    if load_standalone_video(connection, video, owner_id=owner) is None:
        raise VideoNotFoundError("video does not exist")
    _validate_resource_values(resource_kind, role)
    clean_title, clean_url = title.strip(), source_url.strip()
    if not clean_title or not clean_url:
        raise ValueError("resource title and URL are required")
    status = "ready" if resource_kind == "external_link" else "pending"
    resource = connection.execute(
        """
        insert into video.resources (
            owner_id, resource_kind, origin, status, title, source_url
        ) values (%s, %s, %s, %s, %s, %s)
        returning id, resource_kind, origin, status, title, source_url,
                  page_count, created_at, updated_at
        """,
        (owner, resource_kind, origin, status, clean_title, clean_url),
    ).fetchone()
    connection.execute(
        """
        insert into video.video_resources (
            owner_id, video_id, resource_id, role, required
        ) values (%s, %s, %s, %s, %s)
        """,
        (owner, video, resource["id"], role, required),
    )
    resource["role"], resource["required"] = role, required
    return resource


def load_caption_target(
    connection: Connection, *, owner_id: str | UUID, video_id: str | UUID
) -> dict[str, Any] | None:
    """Return the source a caption upload would attach to, if it may attach.

    Captions are accepted until the transcript stage has settled: after that a
    published version already rests on whatever transcript it found, and
    replacing it silently would leave answers citing cues that no longer
    exist. Rebuilding the video is the supported way to change it.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    return connection.execute(
        """
        select s.id as source_id, s.source_kind, v.duration_ms,
               exists (
                   select 1 from video.transcript_sources t
                   where t.video_source_id = s.id and t.owner_id = s.owner_id
                     and t.source_kind = 'openrouter_transcription'
               ) as has_paid_transcript
        from video.videos as v
        join video.video_sources as s
          on s.video_id = v.id and s.owner_id = v.owner_id and s.is_primary
        where v.id = %s and v.owner_id = %s
        """,
        (video, owner),
    ).fetchone()


def record_caption_upload(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    video_source_id: str | UUID,
    original_filename: str | None,
    storage_backend: str,
    storage_key: str,
    content_hash: str,
    size_bytes: int,
    cue_count: int,
) -> dict[str, Any]:
    """Stage one supplied caption file for the transcript stage to consider."""

    owner = parse_owner_id(owner_id)
    row = connection.execute(
        """
        insert into video.caption_uploads (
            owner_id, video_id, video_source_id, original_filename,
            storage_backend, storage_key, content_hash, size_bytes, cue_count
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (video_source_id, content_hash) do update
            set cue_count = excluded.cue_count
        returning id, content_hash, cue_count, created_at
        """,
        (
            owner,
            UUID(str(video_id)),
            UUID(str(video_source_id)),
            original_filename,
            storage_backend,
            storage_key,
            content_hash,
            size_bytes,
            cue_count,
        ),
    ).fetchone()
    return row


def initialize_resource_upload(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    title: str,
    original_filename: str,
    declared_size_bytes: int,
    role: str,
    required: bool = False,
) -> dict[str, Any]:
    """Reserve one uploaded PDF; the worker parses it into pages later."""

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    if load_standalone_video(connection, video, owner_id=owner) is None:
        raise VideoNotFoundError("video does not exist")
    _validate_resource_values("pdf", role)
    filename = display_filename(original_filename)
    clean_title = (title or filename).strip()
    if not clean_title:
        raise ValueError("resource title is required")
    if declared_size_bytes <= 0 or declared_size_bytes > maximum_resource_bytes():
        raise ValueError("resource upload size is outside the supported range")
    resource = connection.execute(
        """
        insert into video.resources (
            owner_id, resource_kind, origin, status, title, original_filename,
            provenance_json
        ) values (%s, 'pdf', 'upload', 'pending', %s, %s, %s)
        returning id, resource_kind, origin, status, title, source_url,
                  page_count, created_at, updated_at
        """,
        (
            owner,
            clean_title,
            filename,
            Jsonb({"declared_size_bytes": declared_size_bytes}),
        ),
    ).fetchone()
    connection.execute(
        """
        insert into video.video_resources (
            owner_id, video_id, resource_id, role, required
        ) values (%s, %s, %s, %s, %s)
        """,
        (owner, video, resource["id"], role, required),
    )
    resource["role"], resource["required"] = role, required
    resource["upload_storage_key"] = (
        f"{owner}/staging/resources/{resource['id']}/original.pdf"
    )
    return resource


def _adopt_existing_document(
    connection: Connection,
    *,
    owner: UUID,
    video: UUID,
    placeholder: UUID,
    content_hash: str,
) -> dict[str, Any] | None:
    existing = connection.execute(
        """
        select id, resource_kind, origin, status, title, source_url,
               page_count, created_at, updated_at
        from video.resources
        where owner_id = %s and content_hash = %s and resource_kind = 'pdf'
        """,
        (owner, content_hash),
    ).fetchone()
    if existing is None:
        return None
    with connection.transaction():
        link = connection.execute(
            """
            delete from video.video_resources
            where owner_id = %s and video_id = %s and resource_id = %s
            returning role, required
            """,
            (owner, video, placeholder),
        ).fetchone()
        connection.execute(
            """
            insert into video.video_resources (
                owner_id, video_id, resource_id, role, required
            ) values (%s, %s, %s, %s, %s)
            on conflict (video_id, resource_id) do nothing
            """,
            (
                owner,
                video,
                existing["id"],
                (link or {}).get("role", "slides"),
                (link or {}).get("required", False),
            ),
        )
        connection.execute(
            "delete from video.resources where id = %s and owner_id = %s",
            (placeholder, owner),
        )
    return existing


def load_resource_upload_target(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    resource_id: str | UUID,
) -> dict[str, Any] | None:
    """Return the reservation an uploaded PDF must match, if it exists."""

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    return connection.execute(
        """
        select r.id, r.status, r.origin, r.provenance_json
        from video.video_resources as link
        join video.resources as r
          on r.id = link.resource_id and r.owner_id = link.owner_id
        where link.owner_id = %s and link.video_id = %s and link.resource_id = %s
          and r.resource_kind = 'pdf' and r.origin = 'upload'
        """,
        (owner, video, UUID(str(resource_id))),
    ).fetchone()


def complete_resource_upload(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    resource_id: str | UUID,
    storage_backend: str,
    storage_key: str,
    content_hash: str,
    size_bytes: int,
) -> dict[str, Any] | None:
    """Attach uploaded bytes; ingestion turns them into pages."""

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    resource = UUID(str(resource_id))
    linked = connection.execute(
        """
        select role, required from video.video_resources
        where owner_id = %s and video_id = %s and resource_id = %s
        """,
        (owner, video, resource),
    ).fetchone()
    if linked is None:
        return None
    try:
        with connection.transaction():
            updated = connection.execute(
                """
                update video.resources
                set storage_backend = %s, storage_key = %s, content_hash = %s,
                    size_bytes = %s, media_type = 'application/pdf',
                    status = 'pending', updated_at = now()
                where id = %s and owner_id = %s and resource_kind = 'pdf'
                returning id, resource_kind, origin, status, title, source_url,
                          page_count, created_at, updated_at
                """,
                (
                    storage_backend,
                    storage_key,
                    content_hash,
                    size_bytes,
                    resource,
                    owner,
                ),
            ).fetchone()
    except UniqueViolation:
        # The owner already has this exact document. That is what the content
        # index is for, so the video points at the copy that exists instead of
        # failing the upload — and the placeholder this upload reserved goes
        # away rather than lingering as a second, empty record of the same PDF.
        updated = _adopt_existing_document(
            connection,
            owner=owner,
            video=video,
            placeholder=resource,
            content_hash=content_hash,
        )
    if updated is None:
        return None
    updated["role"], updated["required"] = linked["role"], linked["required"]
    return updated


def list_video_resources(
    connection: Connection, video_id: str | UUID, *, owner_id: str | UUID
) -> list[dict[str, Any]] | None:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    if load_standalone_video(connection, video, owner_id=owner) is None:
        return None
    return connection.execute(
        """
        select r.id, r.resource_kind, r.origin, r.status, r.title,
               r.source_url, r.page_count, vr.role, vr.required, vr.attached_at
        from video.video_resources as vr
        join video.resources as r
          on r.id = vr.resource_id and r.owner_id = vr.owner_id
        where vr.owner_id = %s and vr.video_id = %s
        order by vr.attached_at, r.id
        """,
        (owner, video),
    ).fetchall()


def detach_video_resource(
    connection: Connection,
    video_id: str | UUID,
    resource_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> bool:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    if load_standalone_video(connection, video, owner_id=owner) is None:
        raise VideoNotFoundError("video does not exist")
    row = connection.execute(
        """
        delete from video.video_resources
        where owner_id = %s and video_id = %s and resource_id = %s
        returning resource_id
        """,
        (owner, video, UUID(str(resource_id))),
    ).fetchone()
    return row is not None


def list_resource_suggestions(
    connection: Connection, video_id: str | UUID, *, owner_id: str | UUID
) -> list[dict[str, Any]] | None:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    if load_standalone_video(connection, video, owner_id=owner) is None:
        return None
    return connection.execute(
        """
        select id, suggested_kind, url, normalized_url, title, reason,
               status, confirmed_resource_id, created_at, resolved_at
        from video.resource_suggestions
        where owner_id = %s and video_id = %s
        order by created_at, id
        """,
        (owner, video),
    ).fetchall()


def confirm_resource_suggestion(
    connection: Connection,
    video_id: str | UUID,
    suggestion_id: str | UUID,
    *,
    owner_id: str | UUID,
    role: str = "reference",
    required: bool = False,
) -> dict[str, Any] | None:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    _validate_resource_values("external_link", role)
    suggestion = connection.execute(
        """
        select s.*
        from video.resource_suggestions as s
        where s.owner_id = %s and s.video_id = %s and s.id = %s
          and not exists (
              select 1 from video.course_lectures as lecture
              where lecture.owner_id = s.owner_id
                and lecture.video_id = s.video_id
          )
        for update
        """,
        (owner, video, UUID(str(suggestion_id))),
    ).fetchone()
    if suggestion is None:
        return None
    if suggestion["status"] == "dismissed":
        raise VideoConflictError("dismissed suggestion cannot be confirmed")
    if suggestion["status"] == "confirmed":
        resources = list_video_resources(connection, video, owner_id=owner) or []
        return next(
            (
                resource
                for resource in resources
                if resource["id"] == suggestion["confirmed_resource_id"]
            ),
            None,
        )
    resource = create_url_resource(
        connection,
        owner_id=owner,
        video_id=video,
        resource_kind=suggestion["suggested_kind"],
        title=suggestion["title"] or suggestion["url"],
        source_url=suggestion["url"],
        role=role,
        required=required,
        origin="discovered",
    )
    connection.execute(
        """
        update video.resource_suggestions
        set status = 'confirmed', confirmed_resource_id = %s,
            resolved_at = now()
        where owner_id = %s and id = %s
        """,
        (resource["id"], owner, suggestion["id"]),
    )
    return resource


def dismiss_resource_suggestion(
    connection: Connection,
    video_id: str | UUID,
    suggestion_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    suggestion = connection.execute(
        """
        select s.* from video.resource_suggestions as s
        where s.owner_id = %s and s.video_id = %s and s.id = %s
          and not exists (
              select 1 from video.course_lectures as lecture
              where lecture.owner_id = s.owner_id
                and lecture.video_id = s.video_id
          )
        for update
        """,
        (owner, video, UUID(str(suggestion_id))),
    ).fetchone()
    if suggestion is None:
        return None
    if suggestion["status"] == "confirmed":
        raise VideoConflictError("confirmed suggestion cannot be dismissed")
    if suggestion["status"] == "pending":
        suggestion = connection.execute(
            """
            update video.resource_suggestions
            set status = 'dismissed', resolved_at = now()
            where owner_id = %s and id = %s
            returning id, suggested_kind, url, normalized_url, title, reason,
                      status, confirmed_resource_id, created_at, resolved_at
            """,
            (owner, suggestion["id"]),
        ).fetchone()
    return suggestion
