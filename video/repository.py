"""Owner-scoped PostgreSQL repository for standalone video material."""

from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
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
            cost_cap_usd, provenance_json
        ) values (
            %s, %s, %s, %s, %s, %s, 'acquire_source', %s, %s, %s, %s
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


VIDEO_SELECT = """
    select v.id, v.title, v.description, v.source_kind, v.duration_ms,
           v.readiness_status, v.playback_json, v.created_at, v.updated_at,
           v.ready_at, v.current_ingestion_version_id,
           s.id as source_id, s.status as source_status, s.source_url,
           s.youtube_video_id, s.original_filename,
           current_version.version_number,
           current_version.quality_gates_json,
           latest_job.id as latest_job_id, latest_job.status as latest_job_status,
           latest_job.stage as latest_job_stage,
           latest_job.progress_completed, latest_job.progress_total,
           latest_job.progress_unit, latest_job.actual_cost_usd,
           latest_job.cost_cap_usd, latest_job.attempt_count,
           latest_job.max_attempts, latest_job.last_error_code,
           latest_job.cancellation_requested_at,
           latest_job.created_at as job_created_at,
           latest_job.started_at as job_started_at,
           latest_job.updated_at as job_updated_at,
           latest_job.completed_at as job_completed_at
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


def delete_unacquired_video(
    connection: Connection, video_id: str | UUID, *, owner_id: str | UUID
) -> bool:
    row = connection.execute(
        """
        delete from video.videos as v
        where v.owner_id = %s and v.id = %s
          and not exists (
              select 1 from video.course_lectures as lecture
              where lecture.owner_id = v.owner_id and lecture.video_id = v.id
          )
          and not exists (
              select 1 from video.video_sources as source
              where source.owner_id = v.owner_id and source.video_id = v.id
                and source.status = 'ready'
          )
          and not exists (
              select 1 from video.ingestion_jobs as job
              where job.owner_id = v.owner_id and job.video_id = v.id
                and job.status = 'running'
          )
        returning id
        """,
        (parse_owner_id(owner_id), UUID(str(video_id))),
    ).fetchone()
    return row is not None


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
