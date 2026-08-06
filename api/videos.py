"""Authenticated standalone-video material and ingestion-status contracts."""

from datetime import datetime
from decimal import Decimal
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import Field, HttpUrl, model_validator
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from storage.database import connection as database_connection
from study.contracts import ContractModel
from video.jobs import (
    VideoJobConflictError,
    VideoJobNotFoundError,
    request_cancellation as request_video_cancellation,
    retry_job as retry_video_job,
)
from video.readiness import readiness_notes
from video.repository import (
    UNSET,
    VideoAlreadyExistsError,
    VideoConflictError,
    VideoNotFoundError,
    complete_resource_upload,
    complete_video_upload,
    confirm_resource_suggestion,
    create_url_resource,
    create_youtube_video,
    delete_video,
    detach_video_resource,
    dismiss_resource_suggestion,
    initialize_resource_upload,
    initialize_video_upload,
    list_job_events,
    list_resource_suggestions,
    list_standalone_videos,
    list_video_chapters,
    list_video_resources,
    load_standalone_video,
    load_caption_target,
    load_ingestion_job,
    load_resource_upload_target,
    load_video_upload_target,
    maximum_upload_bytes,
    record_caption_upload,
    reingest_video,
    update_video_metadata,
)
from video.playback import playback_url
from video.resources import maximum_resource_bytes
from video.transcripts import parse_webvtt, transcript_coverage
from video.sources import InvalidVideoSource, display_filename
from video.media_store import (
    FilesystemMediaStore,
    MediaConflict,
    MediaSizeMismatch,
    MediaStoreError,
    MediaTooLarge,
)


videos_router = APIRouter(prefix="/api/videos", tags=["videos"])
jobs_router = APIRouter(prefix="/api/video-ingestions", tags=["video-ingestion"])
logger = logging.getLogger("study_partner.api.videos")
VIDEO_NOT_FOUND = HTTPException(status_code=404, detail="video not found")
JOB_NOT_FOUND = HTTPException(status_code=404, detail="video ingestion job not found")

SourceKind = Literal["youtube", "upload"]
Readiness = Literal["processing", "ready", "degraded", "failed"]
JobStatus = Literal[
    "awaiting_upload",
    "queued",
    "running",
    "retry_scheduled",
    "ready",
    "failed",
    "cancelled",
]
ResourceKind = Literal["pdf", "external_link"]
# A caption file is text: the 102-minute lecture this was built for is 708 KB.
MAXIMUM_CAPTION_BYTES = 25 * 1024 * 1024
ResourceRole = Literal["slides", "notes", "reference"]


class CreateYouTubeVideoRequest(ContractModel):
    url: str = Field(min_length=1, max_length=2_000)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)


class InitializeVideoUploadRequest(ContractModel):
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["video/mp4", "video/webm", "video/quicktime"]
    content_length: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)


class UploadReservation(ContractModel):
    method: Literal["put"] = "put"
    upload_url: str
    maximum_bytes: int


class InitializeResourceUploadRequest(ContractModel):
    original_filename: str = Field(min_length=1, max_length=255)
    content_length: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    role: ResourceRole = "slides"
    required: bool = False


class CreateVideoResponse(ContractModel):
    video_id: UUID
    ingestion_job_id: UUID
    source_kind: SourceKind
    readiness_status: Readiness = "processing"
    ingestion_status: JobStatus
    upload: UploadReservation | None = None


class UploadSourceResponse(ContractModel):
    video_id: UUID
    ingestion_job_id: UUID
    ingestion_status: Literal["queued"]
    source_status: Literal["pending"] = "pending"
    received_bytes: int


class PlaybackView(ContractModel):
    kind: Literal["youtube", "local"]
    youtube_video_id: str | None = None
    media_url: str | None = None


class ProgressView(ContractModel):
    completed: int
    total: int | None
    unit: str | None
    percent: float | None


class JobErrorView(ContractModel):
    code: str
    # The worker writes a sentence written for a reader (video/errors.py keeps
    # them in SAFE_MESSAGES). It was being dropped for a constant, so every
    # failure — an unplayable file, an exhausted retry, a provider outage —
    # read "Video ingestion failed" and told the reader nothing they could act
    # on. The default now only covers a row that carries no message at all.
    message: str = "Video ingestion failed"


class VideoIngestionView(ContractModel):
    job_id: UUID
    video_id: UUID
    target_version_id: UUID
    status: JobStatus
    stage: str | None
    progress: ProgressView
    attempt: int
    max_attempts: int
    retryable: bool
    cancellation_requested: bool
    actual_cost_usd: Decimal
    cost_cap_usd: Decimal
    error: JobErrorView | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None


class VideoSummary(ContractModel):
    video_id: UUID
    title: str
    description: str | None
    source_kind: SourceKind
    duration_ms: int | None
    readiness_status: Readiness
    ready_for_qa: bool
    playback: PlaybackView
    latest_ingestion: VideoIngestionView | None
    # Why a published version is "degraded", measured. Empty when it passed
    # every gate, so an unexplained reservation is never shown.
    readiness_notes: list[str] = Field(default_factory=list)
    # Whether removal would succeed. Offering an action that 409s is worse
    # than not offering it, and the reason it cannot is worth saying once.
    deletable: bool = False
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None


class VideoListResponse(ContractModel):
    videos: list[VideoSummary]


class SourceView(ContractModel):
    source_kind: SourceKind
    status: Literal["pending", "acquiring", "ready", "failed"]
    source_url: str | None
    youtube_video_id: str | None
    original_filename: str | None


class ChapterView(ContractModel):
    chapter_index: int
    # "derived" is an outline the pipeline worked out from the slides,
    # for a source that published none. The reader is told which it is.
    chapter_kind: Literal["youtube", "manual", "derived"]
    title: str
    start_ms: int
    end_ms: int


class ResourceView(ContractModel):
    resource_id: UUID
    resource_kind: ResourceKind
    origin: Literal["upload", "url", "discovered"]
    status: Literal["pending", "processing", "ready", "failed"]
    title: str
    source_url: str | None
    page_count: int | None
    role: ResourceRole
    required: bool


class CaptionUploadResponse(ContractModel):
    caption_id: int
    cue_count: int
    coverage_ratio: float | None
    content_hash: str


class ResourceUploadReservation(ContractModel):
    resource: ResourceView
    method: Literal["put"] = "put"
    upload_url: str
    maximum_bytes: int


class VideoDetail(VideoSummary):
    source: SourceView
    chapters: list[ChapterView]
    resources: list[ResourceView]
    quality_gates: dict


class UpdateVideoRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def validate_patch(self) -> "UpdateVideoRequest":
        if not self.model_fields_set:
            raise ValueError("at least one video field is required")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        return self


class AttachUrlResourceRequest(ContractModel):
    resource_kind: ResourceKind
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    role: ResourceRole = "reference"
    required: bool = False


class ResourceListResponse(ContractModel):
    resources: list[ResourceView]


class ResolveSuggestionRequest(ContractModel):
    role: ResourceRole = "reference"
    required: bool = False


class SuggestionView(ContractModel):
    suggestion_id: UUID
    suggested_kind: ResourceKind
    url: str
    title: str | None
    reason: str | None
    status: Literal["pending", "confirmed", "dismissed"]
    confirmed_resource_id: UUID | None
    created_at: datetime
    resolved_at: datetime | None


class SuggestionListResponse(ContractModel):
    suggestions: list[SuggestionView]


class JobEventView(ContractModel):
    event_id: int
    event_type: str
    status: str | None
    stage: str | None
    message: str | None
    created_at: datetime


class JobEventListResponse(ContractModel):
    events: list[JobEventView]


def _key(value: str | None) -> UUID:
    if not value or not value.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        return UUID(value.strip())
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="Idempotency-Key must be a UUID"
        ) from error


def _progress(row) -> ProgressView:
    total = row.get("progress_total")
    completed = row.get("progress_completed", 0)
    percent = round(100 * completed / total, 2) if total else None
    return ProgressView(
        completed=completed,
        total=total,
        unit=row.get("progress_unit"),
        percent=percent,
    )


def _job(row, *, prefix: str = "") -> VideoIngestionView | None:
    def value(name):
        return row.get(prefix + name)

    identifier = value("id")
    if identifier is None:
        return None
    job_row = {
        "progress_completed": value("progress_completed"),
        "progress_total": value("progress_total"),
        "progress_unit": value("progress_unit"),
    }
    error_code = value("last_error_code")
    error_message = value("last_error_message")
    return VideoIngestionView(
        job_id=identifier,
        video_id=value("video_id"),
        target_version_id=value("target_version_id"),
        status=value("status"),
        stage=value("stage"),
        progress=_progress(job_row),
        attempt=value("attempt_count"),
        max_attempts=value("max_attempts"),
        retryable=bool(value("last_error_retryable")),
        cancellation_requested=value("cancellation_requested_at") is not None,
        actual_cost_usd=value("actual_cost_usd"),
        cost_cap_usd=value("cost_cap_usd"),
        error=(
            JobErrorView(
                code=error_code,
                **({"message": error_message} if error_message else {}),
            )
            if error_code
            else None
        ),
        created_at=value("created_at"),
        started_at=value("started_at"),
        updated_at=value("updated_at"),
        completed_at=value("completed_at"),
    )


def _summary(row) -> VideoSummary:
    latest = None
    if row.get("latest_job_id"):
        mapped = dict(row)
        mapped.update(
            {
                "latest_job_video_id": row["id"],
            }
        )
        latest = _job(mapped, prefix="latest_job_")
    return VideoSummary(
        video_id=row["id"],
        title=row["title"],
        description=row["description"],
        source_kind=row["source_kind"],
        duration_ms=row["duration_ms"],
        readiness_status=row["readiness_status"],
        ready_for_qa=row["current_ingestion_version_id"] is not None,
        readiness_notes=readiness_notes(row.get("quality_gates_json")),
        deletable=bool(row.get("deletable")),
        playback=PlaybackView(
            kind="youtube" if row["source_kind"] == "youtube" else "local",
            youtube_video_id=row["youtube_video_id"],
            # An uploaded lecture plays from its canonical object through a
            # signed link; without one the workspace showed an empty frame.
            media_url=(
                playback_url(video_id=row["id"], owner_id=row["owner_id"])
                if row["source_kind"] != "youtube"
                and (row["playback_json"] or {}).get("storage_key")
                else None
            ),
        ),
        latest_ingestion=latest,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        ready_at=row["ready_at"],
    )


def _resource(row) -> ResourceView:
    return ResourceView(
        resource_id=row["id"],
        resource_kind=row["resource_kind"],
        origin=row["origin"],
        status=row["status"],
        title=row["title"],
        source_url=row["source_url"],
        page_count=row["page_count"],
        role=row["role"],
        required=row["required"],
    )


@videos_router.post("/youtube", status_code=status.HTTP_201_CREATED)
async def create_youtube(
    request: CreateYouTubeVideoRequest,
    response: Response,
    owner_id: Annotated[UUID, Depends(current_owner)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreateVideoResponse:
    def create():
        with database_connection() as database:
            return create_youtube_video(
                database,
                owner_id=owner_id,
                idempotency_key=_key(idempotency_key),
                url=request.url,
                title=request.title,
                description=request.description,
            )

    try:
        created = await run_in_threadpool(create)
    except VideoAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="video already exists") from error
    except (InvalidVideoSource, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not created.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/videos/{created.video_id}"
    return CreateVideoResponse(
        video_id=created.video_id,
        ingestion_job_id=created.job_id,
        source_kind="youtube",
        ingestion_status=created.job_status,
    )


@videos_router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def initialize_upload(
    request: InitializeVideoUploadRequest,
    response: Response,
    owner_id: Annotated[UUID, Depends(current_owner)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreateVideoResponse:
    def create():
        with database_connection() as database:
            return initialize_video_upload(
                database,
                owner_id=owner_id,
                idempotency_key=_key(idempotency_key),
                original_filename=request.original_filename,
                media_type=request.content_type,
                declared_size_bytes=request.content_length,
                title=request.title,
                description=request.description,
            )

    try:
        created = await run_in_threadpool(create)
    except (InvalidVideoSource, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not created.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/videos/{created.video_id}"
    return CreateVideoResponse(
        video_id=created.video_id,
        ingestion_job_id=created.job_id,
        source_kind="upload",
        ingestion_status=created.job_status,
        upload=UploadReservation(
            upload_url=f"/api/video-ingestions/{created.job_id}/source",
            maximum_bytes=maximum_upload_bytes(),
        ),
    )


@videos_router.get("")
async def list_videos(
    owner_id: Annotated[UUID, Depends(current_owner)], limit: int = 50
) -> VideoListResponse:
    def load():
        with database_connection(readonly=True) as database:
            return list_standalone_videos(database, owner_id=owner_id, limit=limit)
    return VideoListResponse(videos=[_summary(row) for row in await run_in_threadpool(load)])


def _load_detail(owner_id: UUID, video_id: UUID) -> VideoDetail:
    with database_connection(readonly=True) as database:
        row = load_standalone_video(database, video_id, owner_id=owner_id)
        if row is None:
            raise VIDEO_NOT_FOUND
        chapters = list_video_chapters(database, video_id, owner_id=owner_id) or []
        resources = list_video_resources(database, video_id, owner_id=owner_id) or []
    summary = _summary(row)
    return VideoDetail(
        **summary.model_dump(),
        source=SourceView(
            source_kind=row["source_kind"], status=row["source_status"],
            source_url=row["source_url"], youtube_video_id=row["youtube_video_id"],
            original_filename=row["original_filename"],
        ),
        chapters=[
            ChapterView(
                chapter_index=chapter["chapter_index"],
                chapter_kind=chapter["chapter_kind"],
                title=chapter["title"],
                start_ms=chapter["start_ms"],
                end_ms=chapter["end_ms"],
            )
            for chapter in chapters
        ],
        resources=[_resource(resource) for resource in resources],
        quality_gates=row.get("quality_gates_json") or {},
    )


@videos_router.get("/{video_id}")
async def get_video(video_id: UUID, owner_id: UUID = Depends(current_owner)) -> VideoDetail:
    return await run_in_threadpool(_load_detail, owner_id, video_id)


@videos_router.patch("/{video_id}")
async def patch_video(
    video_id: UUID, request: UpdateVideoRequest,
    owner_id: UUID = Depends(current_owner),
) -> VideoDetail:
    def update():
        with database_connection() as database:
            return update_video_metadata(
                database, video_id, owner_id=owner_id,
                title=request.title if "title" in request.model_fields_set else UNSET,
                description=(request.description if "description" in request.model_fields_set else UNSET),
            )
    if await run_in_threadpool(update) is None:
        raise VIDEO_NOT_FOUND
    return await run_in_threadpool(_load_detail, owner_id, video_id)


@videos_router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_video(video_id: UUID, owner_id: UUID = Depends(current_owner)) -> Response:
    """Delete a lecture, then unlink the media nothing else references.

    The rows go first and commit; the bytes go afterwards. Unlinking first
    would delete an object a surviving row still points at if the transaction
    then rolled back, and canonical media is content-addressed, so that object
    can belong to another lecture. Bytes stranded by the reverse failure are
    recoverable — they are still on the volume, with a key nothing names.
    """

    def remove():
        with database_connection() as database:
            if load_standalone_video(database, video_id, owner_id=owner_id) is None:
                return "missing"
            return delete_video(database, video_id, owner_id=owner_id)

    deletion = await run_in_threadpool(remove)
    if deletion == "missing":
        raise VIDEO_NOT_FOUND
    if deletion is None:
        raise HTTPException(
            status_code=409, detail="video cannot be deleted while it is being processed"
        )
    if deletion.orphaned_keys:
        await run_in_threadpool(_unlink_media, owner_id, deletion.orphaned_keys)
    return Response(status_code=204)


def _unlink_media(owner_id: UUID, storage_keys: tuple[str, ...]) -> None:
    """Best effort, and logged rather than raised.

    The lecture is already gone as far as the reader is concerned, so failing
    the request here would report a deletion that did happen as an error. What
    survives a failure is an object with no row naming it, which is a cost on
    the volume rather than a loss of anything.
    """

    try:
        store = FilesystemMediaStore()
    except MediaStoreError:
        logger.warning("No media root configured; %s objects left on disk", len(storage_keys))
        return
    for storage_key in storage_keys:
        try:
            store.remove(owner_id=owner_id, storage_key=storage_key)
        except (MediaStoreError, OSError):
            logger.exception("Could not remove video media object")


@videos_router.put("/{video_id}/captions")
async def upload_captions(
    video_id: UUID,
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> CaptionUploadResponse:
    """Attach a WebVTT transcript to a video, so ingestion need not buy one.

    Parsed on arrival rather than during ingestion: a malformed file should be
    a rejected upload the reader can fix immediately, not a stage that fails
    twenty minutes later.
    """

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type not in {"text/vtt", "text/plain", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="captions must be WebVTT")
    raw_name = request.headers.get("x-caption-filename")
    filename = display_filename(raw_name) if raw_name else None

    def load_target():
        with database_connection(readonly=True) as database:
            return load_caption_target(
                database, owner_id=owner_id, video_id=video_id
            )

    target = await run_in_threadpool(load_target)
    if target is None:
        raise VIDEO_NOT_FOUND
    if target["has_paid_transcript"]:
        raise HTTPException(
            status_code=409,
            detail="this video already has a transcript; rebuild it to replace one",
        )

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAXIMUM_CAPTION_BYTES:
            raise HTTPException(status_code=413, detail="caption file is too large")
    if not payload:
        raise HTTPException(status_code=422, detail="caption file is empty")
    try:
        cues = parse_webvtt(bytes(payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    def store() -> dict:
        media_store = FilesystemMediaStore()
        with NamedTemporaryFile(suffix=".vtt", delete=False) as handle:
            handle.write(bytes(payload))
            staged = Path(handle.name)
        try:
            stored = media_store.import_file(
                owner_id=owner_id,
                source=staged,
                namespace="captions",
                extension=".vtt",
                maximum_bytes=MAXIMUM_CAPTION_BYTES,
            )
        finally:
            staged.unlink(missing_ok=True)
        with database_connection() as database:
            return record_caption_upload(
                database,
                owner_id=owner_id,
                video_id=video_id,
                video_source_id=target["source_id"],
                original_filename=filename,
                storage_backend=media_store.backend,
                storage_key=stored.storage_key,
                content_hash=stored.content_hash,
                size_bytes=stored.size_bytes,
                cue_count=len(cues),
            )

    try:
        recorded = await run_in_threadpool(store)
    except MediaStoreError as error:
        logger.exception("Caption upload storage failed")
        raise HTTPException(
            status_code=503, detail="video media storage unavailable"
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CaptionUploadResponse(
        caption_id=int(recorded["id"]),
        cue_count=len(cues),
        coverage_ratio=(
            transcript_coverage(cues, duration_ms=int(target["duration_ms"]))
            if target["duration_ms"]
            else None
        ),
        content_hash=recorded["content_hash"],
    )


@videos_router.post("/{video_id}/reingest", status_code=status.HTTP_202_ACCEPTED)
async def reingest(
    video_id: UUID,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    owner_id: UUID = Depends(current_owner),
) -> CreateVideoResponse:
    """Rebuild this video's answers from its current set of documents.

    Everything already decided by unchanged content — the download, the
    transcript, the frames, the paid visual analysis — is inherited by the
    replacement version. The published version keeps answering questions until
    the replacement passes its own gates.
    """

    key = _key(idempotency_key)

    def start():
        with database_connection() as database:
            return reingest_video(
                database, owner_id=owner_id, video_id=video_id, idempotency_key=key
            )

    try:
        created = await run_in_threadpool(start)
    except VideoNotFoundError as error:
        raise VIDEO_NOT_FOUND from error
    except VideoConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not created.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/video-ingestions/{created.job_id}"
    return CreateVideoResponse(
        video_id=created.video_id,
        ingestion_job_id=created.job_id,
        source_kind=created.source_kind,
        ingestion_status=created.job_status,
    )


@videos_router.get("/{video_id}/resources")
async def resources(video_id: UUID, owner_id: UUID = Depends(current_owner)) -> ResourceListResponse:
    def load():
        with database_connection(readonly=True) as database:
            return list_video_resources(database, video_id, owner_id=owner_id)
    rows = await run_in_threadpool(load)
    if rows is None:
        raise VIDEO_NOT_FOUND
    return ResourceListResponse(resources=[_resource(row) for row in rows])


@videos_router.post("/{video_id}/resources/links", status_code=201)
async def attach_resource(
    video_id: UUID, request: AttachUrlResourceRequest,
    owner_id: UUID = Depends(current_owner),
) -> ResourceView:
    def create():
        with database_connection() as database:
            return create_url_resource(
                database, owner_id=owner_id, video_id=video_id,
                resource_kind=request.resource_kind, title=request.title,
                source_url=str(request.url), role=request.role, required=request.required,
            )
    try:
        return _resource(await run_in_threadpool(create))
    except VideoNotFoundError as error:
        raise VIDEO_NOT_FOUND from error


@videos_router.post("/{video_id}/resources/uploads", status_code=201)
async def initialize_resource_upload_endpoint(
    video_id: UUID,
    request: InitializeResourceUploadRequest,
    owner_id: UUID = Depends(current_owner),
) -> ResourceUploadReservation:
    def create():
        with database_connection() as database:
            return initialize_resource_upload(
                database,
                owner_id=owner_id,
                video_id=video_id,
                title=request.title or request.original_filename,
                original_filename=request.original_filename,
                declared_size_bytes=request.content_length,
                role=request.role,
                required=request.required,
            )

    try:
        created = await run_in_threadpool(create)
    except VideoNotFoundError as error:
        raise VIDEO_NOT_FOUND from error
    except (InvalidVideoSource, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ResourceUploadReservation(
        resource=_resource(created),
        upload_url=(
            f"/api/videos/{video_id}/resources/{created['id']}/content"
        ),
        maximum_bytes=maximum_resource_bytes(),
    )


@videos_router.put("/{video_id}/resources/{resource_id}/content")
async def upload_resource_content(
    video_id: UUID,
    resource_id: UUID,
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> ResourceView:
    """Stream an uploaded PDF straight to storage without buffering it."""

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type != "application/pdf":
        raise HTTPException(status_code=415, detail="resource must be a PDF")
    limit = maximum_resource_bytes()

    def load_target():
        with database_connection(readonly=True) as database:
            return load_resource_upload_target(
                database,
                owner_id=owner_id,
                video_id=video_id,
                resource_id=resource_id,
            )

    target = await run_in_threadpool(load_target)
    if target is None:
        raise HTTPException(status_code=404, detail="video resource not found")
    declared = int((target["provenance_json"] or {}).get("declared_size_bytes") or 0)
    if declared <= 0:
        raise HTTPException(status_code=409, detail="resource has no upload reservation")
    raw_length = request.headers.get("content-length")
    if raw_length is not None and int(raw_length) != declared:
        raise HTTPException(
            status_code=422, detail="Content-Length does not match the reservation"
        )

    storage_key = f"{owner_id}/staging/resources/{resource_id}/original.pdf"
    try:
        store = FilesystemMediaStore()
        writer = store.writer(
            owner_id=owner_id, storage_key=storage_key, maximum_bytes=limit
        )
        try:
            async for chunk in request.stream():
                await run_in_threadpool(writer.write, chunk)
            stored = await run_in_threadpool(writer.finish, expected_size=declared)
        finally:
            await run_in_threadpool(writer.abort)
    except MediaTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except MediaSizeMismatch as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except MediaConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MediaStoreError as error:
        logger.exception("Resource upload storage failed")
        raise HTTPException(
            status_code=503, detail="video media storage unavailable"
        ) from error

    def complete():
        with database_connection() as database:
            return complete_resource_upload(
                database,
                owner_id=owner_id,
                video_id=video_id,
                resource_id=resource_id,
                storage_backend=store.backend,
                storage_key=stored.storage_key,
                content_hash=stored.content_hash,
                size_bytes=stored.size_bytes,
            )

    completed = await run_in_threadpool(complete)
    if completed is None:
        raise HTTPException(status_code=404, detail="video resource not found")
    return _resource(completed)


@videos_router.delete("/{video_id}/resources/{resource_id}", status_code=204)
async def detach_resource(
    video_id: UUID, resource_id: UUID, owner_id: UUID = Depends(current_owner)
) -> Response:
    def detach():
        with database_connection() as database:
            return detach_video_resource(
                database, video_id, resource_id, owner_id=owner_id
            )
    try:
        removed = await run_in_threadpool(detach)
    except VideoNotFoundError as error:
        raise VIDEO_NOT_FOUND from error
    if not removed:
        raise HTTPException(status_code=404, detail="video resource not found")
    return Response(status_code=204)


@videos_router.get("/{video_id}/resource-suggestions")
async def suggestions(
    video_id: UUID, owner_id: UUID = Depends(current_owner)
) -> SuggestionListResponse:
    def load():
        with database_connection(readonly=True) as database:
            return list_resource_suggestions(database, video_id, owner_id=owner_id)
    rows = await run_in_threadpool(load)
    if rows is None:
        raise VIDEO_NOT_FOUND
    return SuggestionListResponse(suggestions=[
        SuggestionView(
            suggestion_id=row["id"], suggested_kind=row["suggested_kind"],
            url=row["url"], title=row["title"], reason=row["reason"],
            status=row["status"], confirmed_resource_id=row["confirmed_resource_id"],
            created_at=row["created_at"], resolved_at=row["resolved_at"],
        ) for row in rows
    ])


async def _resolve_suggestion(video_id, suggestion_id, request, owner_id, confirm):
    def resolve():
        with database_connection() as database:
            if confirm:
                return confirm_resource_suggestion(
                    database, video_id, suggestion_id, owner_id=owner_id,
                    role=request.role, required=request.required,
                )
            return dismiss_resource_suggestion(
                database, video_id, suggestion_id, owner_id=owner_id
            )
    try:
        result = await run_in_threadpool(resolve)
    except VideoConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="resource suggestion not found")
    return result


@videos_router.post("/{video_id}/resource-suggestions/{suggestion_id}/confirm")
async def confirm_suggestion(
    video_id: UUID, suggestion_id: UUID, request: ResolveSuggestionRequest,
    owner_id: UUID = Depends(current_owner),
) -> ResourceView:
    return _resource(await _resolve_suggestion(
        video_id, suggestion_id, request, owner_id, True
    ))


@videos_router.post("/{video_id}/resource-suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    video_id: UUID, suggestion_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> SuggestionView:
    row = await _resolve_suggestion(
        video_id, suggestion_id, ResolveSuggestionRequest(), owner_id, False
    )
    return SuggestionView(
        suggestion_id=row["id"], suggested_kind=row["suggested_kind"],
        url=row["url"], title=row["title"], reason=row["reason"],
        status=row["status"], confirmed_resource_id=row["confirmed_resource_id"],
        created_at=row["created_at"], resolved_at=row["resolved_at"],
    )


@jobs_router.get("/{job_id}")
async def get_video_ingestion(
    job_id: UUID, owner_id: UUID = Depends(current_owner)
) -> VideoIngestionView:
    def load():
        with database_connection(readonly=True) as database:
            return load_ingestion_job(database, job_id, owner_id=owner_id)
    row = await run_in_threadpool(load)
    if row is None:
        raise JOB_NOT_FOUND
    return _job(row)


@jobs_router.put("/{job_id}/source", status_code=status.HTTP_202_ACCEPTED)
async def upload_video_source(
    job_id: UUID,
    request: Request,
    response: Response,
    owner_id: UUID = Depends(current_owner),
) -> UploadSourceResponse:
    """Stream reserved upload bytes without buffering a lecture in memory."""

    def load_target():
        with database_connection(readonly=True) as database:
            return load_video_upload_target(database, job_id, owner_id=owner_id)

    target = await run_in_threadpool(load_target)
    if target is None:
        raise JOB_NOT_FOUND
    if target.status not in {"awaiting_upload", "queued"}:
        raise HTTPException(status_code=409, detail="video upload is not awaiting bytes")

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type != target.declared_media_type:
        raise HTTPException(
            status_code=415, detail="Content-Type does not match upload reservation"
        )
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from error
        if content_length > maximum_upload_bytes():
            raise HTTPException(status_code=413, detail="video upload is too large")
        if content_length != target.declared_size_bytes:
            raise HTTPException(
                status_code=422,
                detail="Content-Length does not match upload reservation",
            )

    try:
        store = FilesystemMediaStore()
        writer = store.writer(
            owner_id=owner_id,
            storage_key=target.storage_key,
            maximum_bytes=min(maximum_upload_bytes(), target.declared_size_bytes),
        )
        try:
            async for chunk in request.stream():
                await run_in_threadpool(writer.write, chunk)
            stored = await run_in_threadpool(
                writer.finish, expected_size=target.declared_size_bytes
            )
        finally:
            await run_in_threadpool(writer.abort)
    except MediaTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except MediaSizeMismatch as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except MediaConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MediaStoreError as error:
        logger.exception("Video upload storage failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=503, detail="video media storage unavailable") from error

    def complete():
        with database_connection() as database:
            return complete_video_upload(
                database,
                job_id,
                owner_id=owner_id,
                storage_key=stored.storage_key,
                content_hash=stored.content_hash,
                size_bytes=stored.size_bytes,
                media_type=media_type,
            )

    try:
        completed = await run_in_threadpool(complete)
    except VideoConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if completed is None:
        raise JOB_NOT_FOUND
    if completed.replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/video-ingestions/{job_id}"
    return UploadSourceResponse(
        video_id=completed.video_id,
        ingestion_job_id=completed.job_id,
        ingestion_status="queued",
        received_bytes=completed.size_bytes,
    )


@jobs_router.get("/{job_id}/events")
async def video_ingestion_events(
    job_id: UUID, owner_id: UUID = Depends(current_owner)
) -> JobEventListResponse:
    def load():
        with database_connection(readonly=True) as database:
            return list_job_events(database, job_id, owner_id=owner_id)
    rows = await run_in_threadpool(load)
    if rows is None:
        raise JOB_NOT_FOUND
    return JobEventListResponse(events=[
        JobEventView(
            event_id=row["id"], event_type=row["event_type"], status=row["status"],
            stage=row["stage"], message=row["message"], created_at=row["created_at"],
        ) for row in rows
    ])


@jobs_router.post("/{job_id}/cancel")
async def cancel_video_ingestion(
    job_id: UUID, owner_id: UUID = Depends(current_owner)
) -> VideoIngestionView:
    def cancel():
        with database_connection() as database:
            request_video_cancellation(
                database, owner_id=owner_id, job_id=job_id
            )
            return load_ingestion_job(database, job_id, owner_id=owner_id)

    try:
        return _job(await run_in_threadpool(cancel))
    except VideoJobNotFoundError as error:
        raise JOB_NOT_FOUND from error
    except VideoJobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@jobs_router.post("/{job_id}/retry")
async def retry_video_ingestion(
    job_id: UUID, owner_id: UUID = Depends(current_owner)
) -> VideoIngestionView:
    def retry():
        with database_connection() as database:
            retry_video_job(database, owner_id=owner_id, job_id=job_id)
            return load_ingestion_job(database, job_id, owner_id=owner_id)

    try:
        return _job(await run_in_threadpool(retry))
    except VideoJobNotFoundError as error:
        raise JOB_NOT_FOUND from error
    except VideoJobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
