"""Authenticated course aggregation and ordered lecture management."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import Field, HttpUrl, model_validator
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from api.videos import VideoSummary, _summary
from storage.database import connection as database_connection
from study.contracts import ContractModel
from video.course_repository import (
    CourseConflictError,
    CourseNotFoundError,
    attach_course_lecture,
    create_course,
    create_youtube_course,
    delete_course,
    detach_course_lecture,
    list_course_lectures,
    list_courses,
    load_course,
    update_course,
    update_course_lecture,
    upgrade_course_quality,
)
from video.playlists import (
    PlaylistDiscoveryError,
    PlaylistUrlError,
    discover_youtube_playlist,
)
from video.sources import InvalidVideoSource


router = APIRouter(prefix="/api/courses", tags=["courses"])
COURSE_NOT_FOUND = HTTPException(status_code=404, detail="course not found")
LECTURE_NOT_FOUND = HTTPException(status_code=404, detail="course lecture not found")


class CreateCourseRequest(ContractModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)


class YouTubeLectureInput(ContractModel):
    url: HttpUrl
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)


class CreateYouTubeCourseRequest(CreateCourseRequest):
    lectures: list[YouTubeLectureInput] = Field(min_length=1, max_length=100)


class CreatePlaylistCourseRequest(ContractModel):
    playlist_url: HttpUrl
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)


class BatchLectureResult(ContractModel):
    video_id: UUID
    ingestion_job_id: UUID | None
    reused: bool


class BatchCourseResponse(ContractModel):
    course_id: UUID
    created: bool
    lectures: list[BatchLectureResult]


class UpgradeCourseQualityRequest(ContractModel):
    ingestion_cost_cap_usd: float = Field(default=4.25, gt=0, lt=5)


class UpgradeCourseQualityResponse(ContractModel):
    course_id: UUID
    ingestion_cost_cap_usd: float
    lectures: list[BatchLectureResult]


class PlaylistCourseResponse(BatchCourseResponse):
    playlist_id: str
    playlist_url: str
    playlist_title: str
    duration_seconds: int
    durations_complete: bool


class CourseSummary(ContractModel):
    course_id: UUID
    title: str
    description: str | None
    preview_youtube_video_id: str | None
    lecture_count: int
    ready_count: int
    degraded_count: int
    processing_count: int
    failed_count: int
    ingestion_cost_cap_usd: float
    actual_ingestion_cost_usd: float
    created_at: datetime
    updated_at: datetime


class CourseLecture(VideoSummary):
    lecture_index: int
    title_override: str | None
    display_title: str


class CourseDetail(CourseSummary):
    lectures: list[CourseLecture]


class CourseListResponse(ContractModel):
    courses: list[CourseSummary]


class UpdateCourseRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def requires_change(self) -> "UpdateCourseRequest":
        if not self.model_fields_set:
            raise ValueError("at least one course field is required")
        return self


class AttachLectureRequest(ContractModel):
    video_id: UUID
    title_override: str | None = Field(default=None, min_length=1, max_length=500)


class UpdateLectureRequest(ContractModel):
    lecture_index: int | None = Field(default=None, ge=0)
    title_override: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def requires_change(self) -> "UpdateLectureRequest":
        if not self.model_fields_set:
            raise ValueError("at least one lecture field is required")
        if "title_override" in self.model_fields_set and self.title_override is not None:
            if not self.title_override.strip():
                raise ValueError("title_override cannot be blank")
        return self


def _key(value: str | None) -> UUID:
    if not value:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    try:
        return UUID(value)
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="Idempotency-Key must be a UUID"
        ) from error


def _course(row) -> CourseSummary:
    return CourseSummary(
        course_id=row["id"],
        title=row["title"],
        description=row["description"],
        preview_youtube_video_id=row.get("preview_youtube_video_id"),
        lecture_count=row["lecture_count"],
        ready_count=row["ready_count"],
        degraded_count=row["degraded_count"],
        processing_count=row["processing_count"],
        failed_count=row["failed_count"],
        ingestion_cost_cap_usd=float(row["ingestion_cost_cap_usd"]),
        actual_ingestion_cost_usd=float(row["actual_ingestion_cost_usd"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _lecture(row) -> CourseLecture:
    summary = _summary(row)
    override = row.get("title_override")
    return CourseLecture(
        **summary.model_dump(),
        lecture_index=row["lecture_index"],
        title_override=override,
        display_title=override or summary.title,
    )


def _detail(connection, course_id: UUID, owner_id: UUID) -> CourseDetail:
    row = load_course(connection, course_id, owner_id=owner_id)
    if row is None:
        raise COURSE_NOT_FOUND
    summary = _course(row)
    return CourseDetail(
        **summary.model_dump(),
        lectures=[
            _lecture(lecture)
            for lecture in list_course_lectures(
                connection, course_id, owner_id=owner_id
            )
        ],
    )


@router.post(
    "",
    response_model=CourseDetail,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": CourseDetail, "description": "Idempotent replay of the existing reservation or job"}},
)
async def create_empty_course(
    request: CreateCourseRequest,
    response: Response,
    owner_id: UUID = Depends(current_owner),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CourseDetail:
    """Create an empty course owned by the signed-in user."""

    def create() -> tuple[CourseDetail, bool]:
        with database_connection() as connection:
            created = create_course(
                connection,
                owner_id=owner_id,
                creation_key=_key(idempotency_key),
                title=request.title,
                description=request.description,
            )
            return _detail(connection, created.course_id, owner_id), created.created

    detail, created = await run_in_threadpool(create)
    if not created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/courses/{detail.course_id}"
    return detail


@router.post(
    "/batch-youtube",
    response_model=BatchCourseResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": BatchCourseResponse, "description": "Idempotent replay of the existing reservation or job"}},
)
async def create_course_from_youtube(
    request: CreateYouTubeCourseRequest,
    response: Response,
    owner_id: UUID = Depends(current_owner),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BatchCourseResponse:
    """Create an ordered course from a batch of YouTube lectures and queue ingestion work."""

    def create():
        with database_connection() as connection:
            return create_youtube_course(
                connection,
                owner_id=owner_id,
                creation_key=_key(idempotency_key),
                title=request.title,
                description=request.description,
                lectures=[lecture.model_dump(mode="json") for lecture in request.lectures],
            )

    try:
        result = await run_in_threadpool(create)
    except (InvalidVideoSource, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not result.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/courses/{result.course_id}"
    return BatchCourseResponse(
        course_id=result.course_id,
        created=result.created,
        lectures=[
            BatchLectureResult(
                video_id=lecture.video_id,
                ingestion_job_id=lecture.ingestion_job_id,
                reused=lecture.reused,
            )
            for lecture in result.lectures
        ],
    )


@router.post(
    "/from-youtube-playlist",
    response_model=PlaylistCourseResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": PlaylistCourseResponse, "description": "Idempotent replay of the existing reservation or job"}},
)
async def create_course_from_playlist(
    request: CreatePlaylistCourseRequest,
    response: Response,
    owner_id: UUID = Depends(current_owner),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PlaylistCourseResponse:
    """Create a course from a YouTube playlist and queue its selected lectures for ingestion."""

    key = _key(idempotency_key)

    def discover_and_create():
        playlist = discover_youtube_playlist(str(request.playlist_url))
        with database_connection() as connection:
            result = create_youtube_course(
                connection,
                owner_id=owner_id,
                creation_key=key,
                title=request.title or playlist.title,
                description=(
                    request.description
                    if request.description is not None
                    else playlist.description
                ),
                lectures=[
                    {
                        "url": lecture.canonical_url,
                        "title": lecture.title,
                        "description": None,
                    }
                    for lecture in playlist.lectures
                ],
                metadata=playlist.snapshot(),
            )
        return playlist, result

    try:
        playlist, result = await run_in_threadpool(discover_and_create)
    except (PlaylistUrlError, InvalidVideoSource, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except PlaylistDiscoveryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if not result.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/courses/{result.course_id}"
    return PlaylistCourseResponse(
        course_id=result.course_id,
        created=result.created,
        playlist_id=playlist.playlist_id,
        playlist_url=playlist.canonical_url,
        playlist_title=playlist.title,
        duration_seconds=playlist.known_duration_seconds,
        durations_complete=playlist.durations_complete,
        lectures=[
            BatchLectureResult(
                video_id=lecture.video_id,
                ingestion_job_id=lecture.ingestion_job_id,
                reused=lecture.reused,
            )
            for lecture in result.lectures
        ],
    )


@router.get("", response_model=CourseListResponse)
async def courses(
    owner_id: UUID = Depends(current_owner), limit: int = 50
) -> CourseListResponse:
    """List the signed-in user’s courses."""

    def load():
        with database_connection(readonly=True) as connection:
            return list_courses(connection, owner_id=owner_id, limit=limit)

    return CourseListResponse(
        courses=[_course(row) for row in await run_in_threadpool(load)]
    )


@router.post(
    "/{course_id}/upgrade-quality", response_model=UpgradeCourseQualityResponse
)
async def upgrade_quality(
    course_id: UUID,
    request: UpgradeCourseQualityRequest,
    owner_id: UUID = Depends(current_owner),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UpgradeCourseQualityResponse:
    """Queue higher-quality ingestion for eligible lectures in an owned course."""

    def queue():
        with database_connection() as connection:
            return upgrade_course_quality(
                connection,
                owner_id=owner_id,
                course_id=course_id,
                idempotency_key=_key(idempotency_key),
                ingestion_cost_cap_usd=request.ingestion_cost_cap_usd,
            )

    try:
        result = await run_in_threadpool(queue)
    except CourseNotFoundError as error:
        raise COURSE_NOT_FOUND from error
    except (CourseConflictError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return UpgradeCourseQualityResponse(
        course_id=result.course_id,
        ingestion_cost_cap_usd=float(result.ingestion_cost_cap_usd),
        lectures=[
            BatchLectureResult(
                video_id=lecture.video_id,
                ingestion_job_id=lecture.ingestion_job_id,
                reused=lecture.reused,
            )
            for lecture in result.lectures
        ],
    )


@router.get("/{course_id}", response_model=CourseDetail)
async def course_detail(
    course_id: UUID, owner_id: UUID = Depends(current_owner)
) -> CourseDetail:
    """Load an owned course and its ordered lectures."""

    def load():
        with database_connection(readonly=True) as connection:
            return _detail(connection, course_id, owner_id)

    return await run_in_threadpool(load)


@router.patch("/{course_id}", response_model=CourseDetail)
async def patch_course(
    course_id: UUID,
    request: UpdateCourseRequest,
    owner_id: UUID = Depends(current_owner),
) -> CourseDetail:
    """Update an owned course’s title or description."""

    def update():
        with database_connection() as connection:
            row = update_course(
                connection,
                course_id,
                owner_id=owner_id,
                title=request.title,
                description=request.description,
                update_description="description" in request.model_fields_set,
            )
            if row is None:
                raise COURSE_NOT_FOUND
            return _detail(connection, course_id, owner_id)

    return await run_in_threadpool(update)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_course(
    course_id: UUID, owner_id: UUID = Depends(current_owner)
) -> Response:
    """Delete an owned course."""

    def remove():
        with database_connection() as connection:
            return delete_course(connection, course_id, owner_id=owner_id)

    if not await run_in_threadpool(remove):
        raise COURSE_NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{course_id}/lectures",
    response_model=CourseDetail,
    status_code=status.HTTP_201_CREATED,
)
async def attach_lecture(
    course_id: UUID,
    request: AttachLectureRequest,
    owner_id: UUID = Depends(current_owner),
) -> CourseDetail:
    """Attach an owned lecture to a course with its position and course-specific metadata."""

    def attach():
        with database_connection() as connection:
            attach_course_lecture(
                connection,
                course_id,
                request.video_id,
                owner_id=owner_id,
                title_override=request.title_override,
            )
            return _detail(connection, course_id, owner_id)

    try:
        return await run_in_threadpool(attach)
    except CourseNotFoundError as error:
        raise COURSE_NOT_FOUND from error
    except CourseConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.patch("/{course_id}/lectures/{video_id}", response_model=CourseDetail)
async def patch_lecture(
    course_id: UUID,
    video_id: UUID,
    request: UpdateLectureRequest,
    owner_id: UUID = Depends(current_owner),
) -> CourseDetail:
    """Update a lecture’s position or metadata within an owned course."""

    def update():
        with database_connection() as connection:
            update_course_lecture(
                connection,
                course_id,
                video_id,
                owner_id=owner_id,
                lecture_index=request.lecture_index,
                title_override=request.title_override,
                update_title="title_override" in request.model_fields_set,
            )
            return _detail(connection, course_id, owner_id)

    try:
        return await run_in_threadpool(update)
    except CourseNotFoundError as error:
        raise LECTURE_NOT_FOUND from error


@router.delete(
    "/{course_id}/lectures/{video_id}",
    response_model=CourseDetail,
)
async def detach_lecture(
    course_id: UUID,
    video_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> CourseDetail:
    """Remove a lecture’s membership from a course and return the updated course."""

    def detach():
        with database_connection() as connection:
            if not detach_course_lecture(
                connection, course_id, video_id, owner_id=owner_id
            ):
                raise LECTURE_NOT_FOUND
            return _detail(connection, course_id, owner_id)

    return await run_in_threadpool(detach)
