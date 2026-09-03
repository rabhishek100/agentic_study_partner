"""Authenticated ingestion lifecycle endpoints.

The API never parses a PDF. It reserves an owner-scoped upload path, verifies
what actually landed in Storage, and hands the job to the worker through
Postgres, so no request is ever held open for the length of an ingestion.
"""

import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import Field, field_validator
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from ingestion.config import load_limits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.jobs import (
    IngestionJob,
    JobConflictError,
    JobNotFoundError,
    confirm_outline_review,
    create_job,
    get_job,
    list_jobs,
    mark_upload_complete,
    outline_review,
    request_cancellation,
    retry_job,
)
from ingestion.outlines import MAXIMUM_PROPOSED_ENTRIES, normalize_title
from ingestion.preflight import validate_table_of_contents
from ingestion.progress import estimate
from ingestion.states import Status
from ingestion.storage_objects import object_info, object_uploader
from storage.database import connection as database_connection
from study.contracts import ContractModel


logger = logging.getLogger("study_partner.ingestion")

router = APIRouter(prefix="/api/ingestions", tags=["ingestion"])

JOB_NOT_FOUND = HTTPException(status_code=404, detail="ingestion job not found")

# Failures the caller can act on, mapped to the status code that says why.
ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.SOURCE_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    ErrorCode.UNSUPPORTED_CONTENT_TYPE: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ErrorCode.QUOTA_EXCEEDED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.SOURCE_MISSING: status.HTTP_409_CONFLICT,
    ErrorCode.SOURCE_CHANGED: status.HTTP_409_CONFLICT,
    ErrorCode.ATTEMPTS_EXHAUSTED: status.HTTP_409_CONFLICT,
    ErrorCode.STORAGE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


class CreateIngestionRequest(ContractModel):
    original_filename: str
    content_type: str | None = None
    content_length: int | None = None
    document_type: Literal["book", "paper"] = "book"


class CreateIngestionResponse(ContractModel):
    job_id: UUID
    status: Status
    storage_bucket: str
    storage_path: str
    maximum_bytes: int
    upload_method: Literal["tus"] = "tus"


class IngestionLimitsResponse(ContractModel):
    """What the browser must know before it offers to upload anything.

    Served rather than compiled in, so the limit lives in one place. The
    browser previously hardcoded its own copy, which drifted from the real
    ceiling and let a too-large file reach Storage before anything said no.
    """

    maximum_bytes: int
    maximum_pages: int
    allowed_content_types: list[str]


class JobProgress(ContractModel):
    completed: int
    total: int | None
    unit: str | None
    percent: float | None


class StageView(ContractModel):
    """One stage's place in the pipeline, for a timeline display."""

    stage: str
    label: str
    state: Literal["done", "active", "pending"]
    expected_seconds: float
    elapsed_seconds: float | None


class JobTiming(ContractModel):
    """Elapsed and estimated time.

    Estimates come from a measured production run, not from the parser, which
    reports nothing while it works. ``estimated_remaining_seconds`` is null
    once a stage has run well past its expected duration, because a countdown
    that has already reached zero tells the reader less than saying so, and
    null again while the job waits on a person, whose return time is not
    something this service can estimate.

    ``elapsed_seconds`` is time the pipeline worked. The wait for a reviewer
    is reported beside it, not inside it.
    """

    percent: float
    elapsed_seconds: float
    estimated_total_seconds: float
    estimated_remaining_seconds: float | None
    overrunning: bool
    stages: list[StageView]
    awaiting_input: bool = False
    awaiting_input_seconds: float = 0.0


class JobError(ContractModel):
    code: str
    message: str


class JobResponse(ContractModel):
    job_id: UUID
    status: Status
    stage: str | None
    original_filename: str
    progress: JobProgress
    attempt: int
    max_attempts: int
    retryable: bool
    page_count: int | None
    book_id: int | None
    document_type: str = "book"
    error: JobError | None
    cancellation_requested: bool
    # Whether a browser can act on this job. A job whose source was imported
    # from an operator's filesystem is real and worth showing, but nothing in
    # the tab can advance it and the shared worker will never claim it, so the
    # upload panel must not adopt it and sit on a spinner forever.
    driveable: bool = True
    timing: JobTiming
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None


class JobListResponse(ContractModel):
    jobs: list[JobResponse]


class OutlineEntryView(ContractModel):
    level: int = Field(ge=1, le=20)
    title: str = Field(min_length=1, max_length=500)
    page: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = normalize_title(value)
        if not cleaned:
            raise ValueError("outline title cannot be blank")
        return cleaned


class OutlineReviewResponse(ContractModel):
    job_id: UUID
    status: Status
    page_count: int
    outline_source: str
    proposer_version: str
    reasons: list[str]
    warnings: list[str]
    entries: list[OutlineEntryView]


class ConfirmOutlineRequest(ContractModel):
    entries: list[OutlineEntryView] = Field(
        min_length=1,
        max_length=MAXIMUM_PROPOSED_ENTRIES,
    )


def _represent(job: IngestionJob) -> JobResponse:
    """Project a job row into its public representation.

    Only the stable error code and its safe message cross this boundary: no
    stack traces, SQL, provider payloads, local paths, or credentials.
    """

    error = None
    if job.last_error_code:
        error = JobError(
            code=job.last_error_code,
            message=job.last_error_message or "",
        )
    progress = estimate(
        status=job.status,
        stage=job.stage,
        page_count=job.page_count,
        started_at=job.started_at,
        stage_started_at=job.stage_started_at,
        completed_at=job.completed_at,
        progress_completed=job.progress_completed,
        progress_total=job.progress_total,
        awaiting_input_seconds=job.awaiting_input_seconds,
    )
    timing = JobTiming(
        percent=progress.percent,
        elapsed_seconds=progress.elapsed_seconds,
        estimated_total_seconds=progress.estimated_total_seconds,
        estimated_remaining_seconds=progress.estimated_remaining_seconds,
        overrunning=progress.overrunning,
        awaiting_input=progress.awaiting_input,
        awaiting_input_seconds=progress.awaiting_input_seconds,
        stages=[
            StageView(
                stage=view.stage,
                label=view.label,
                state=view.state,
                expected_seconds=view.expected_seconds,
                elapsed_seconds=view.elapsed_seconds,
            )
            for view in progress.stages
        ],
    )
    return JobResponse(
        job_id=job.id,
        status=job.status,
        stage=str(job.stage) if job.stage else None,
        original_filename=job.original_filename,
        progress=JobProgress(
            completed=job.progress_completed,
            total=job.progress_total,
            unit=job.progress_unit,
            percent=job.progress_percent,
        ),
        attempt=job.attempt_count,
        max_attempts=job.max_attempts,
        retryable=bool(job.last_error_retryable) and job.status is Status.FAILED,
        page_count=job.page_count,
        book_id=job.book_id,
        document_type=getattr(job, "document_type", "book") or "book",
        error=error,
        cancellation_requested=job.cancellation_requested,
        driveable=not job.locally_sourced,
        timing=timing,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _as_http(error: IngestionError) -> HTTPException:
    logger.warning(
        "ingestion request rejected: code=%s detail=%s", error.code, error.detail
    )
    return HTTPException(
        status_code=ERROR_STATUS.get(error.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        detail={"code": str(error.code), "message": error.safe_message},
    )


def _idempotency_key(value: str | None) -> UUID:
    if not value or not value.strip():
        raise HTTPException(
            status_code=400, detail="Idempotency-Key header is required"
        )
    try:
        return UUID(value.strip())
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="Idempotency-Key must be a UUID"
        ) from error


def _represent_outline_review(job: IngestionJob) -> OutlineReviewResponse:
    review = outline_review(job)
    if review is None:
        raise HTTPException(
            status_code=409,
            detail="ingestion job has no outline proposal",
        )
    if job.page_count is None:
        raise HTTPException(
            status_code=409,
            detail="outline proposal has no source page count",
        )
    try:
        entries = [
            OutlineEntryView.model_validate(entry)
            for entry in review.get("entries", [])
        ]
        return OutlineReviewResponse(
            job_id=job.id,
            status=job.status,
            page_count=job.page_count,
            outline_source=str(review["outline_source"]),
            proposer_version=str(review["proposer_version"]),
            reasons=[str(reason) for reason in review.get("reasons", [])],
            warnings=[str(warning) for warning in review.get("warnings", [])],
            entries=entries,
        )
    except (KeyError, TypeError, ValueError) as error:
        logger.error("outline review provenance is malformed for job %s", job.id)
        raise HTTPException(
            status_code=409,
            detail="outline proposal is unavailable",
        ) from error


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ingestion(
    request: CreateIngestionRequest,
    response: Response,
    owner_id: Annotated[UUID, Depends(current_owner)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreateIngestionResponse:
    """Reserve an ingestion job and its immutable upload path."""

    key = _idempotency_key(idempotency_key)
    limits = load_limits()

    def reserve() -> tuple[IngestionJob, bool]:
        with database_connection() as connection:
            return create_job(
                connection,
                owner_id=owner_id,
                idempotency_key=key,
                original_filename=request.original_filename,
                content_type=request.content_type,
                content_length=request.content_length,
                document_type=request.document_type,
                limits=limits,
            )

    try:
        job, created = await run_in_threadpool(reserve)
    except IngestionError as error:
        raise _as_http(error) from error

    # Replaying the same key returns the original reservation rather than
    # minting a second path for the same upload.
    if not created:
        response.status_code = status.HTTP_200_OK
    return CreateIngestionResponse(
        job_id=job.id,
        status=job.status,
        storage_bucket=job.storage_bucket,
        storage_path=job.storage_path,
        maximum_bytes=limits.max_source_bytes,
    )


@router.post("/{job_id}/complete", status_code=status.HTTP_202_ACCEPTED)
async def complete_upload(
    job_id: UUID,
    response: Response,
    owner_id: Annotated[UUID, Depends(current_owner)],
) -> JobResponse:
    """Verify the uploaded object and hand the job to the worker."""

    limits = load_limits()

    def verify_and_queue() -> IngestionJob:
        with database_connection() as connection:
            job = get_job(connection, owner_id=owner_id, job_id=job_id)
            if job.status is not Status.AWAITING_UPLOAD:
                # Idempotent: a client retrying a dropped response, or racing
                # its own second call, sees the job as it currently stands.
                return job

            stored = object_info(
                job.storage_bucket, job.storage_path, backend=job.storage_backend
            )
            if stored is None:
                raise IngestionError(
                    ErrorCode.SOURCE_MISSING,
                    detail="no object at the reserved path",
                )
            if stored.size_bytes > limits.max_source_bytes:
                raise IngestionError(
                    ErrorCode.SOURCE_TOO_LARGE,
                    detail=f"stored object is {stored.size_bytes} bytes",
                )
            if stored.size_bytes == 0:
                raise IngestionError(
                    ErrorCode.INVALID_PDF, detail="stored object is empty"
                )
            # Declared metadata is a hint, not proof of format; the worker
            # still validates the actual file structure before parsing.
            if (
                stored.content_type
                and stored.content_type not in limits.allowed_content_types
            ):
                raise IngestionError(
                    ErrorCode.UNSUPPORTED_CONTENT_TYPE,
                    detail=f"stored content type {stored.content_type!r}",
                )

            uploader = object_uploader(
                connection, job.storage_bucket, job.storage_path
            )
            if uploader is not None and UUID(uploader) != job.owner_id:
                raise IngestionError(
                    ErrorCode.SOURCE_MISSING,
                    detail="stored object was uploaded by another user",
                )

            return mark_upload_complete(
                connection,
                owner_id=owner_id,
                job_id=job_id,
                verified_size_bytes=stored.size_bytes,
                limits=limits,
            )

    try:
        job = await run_in_threadpool(verify_and_queue)
    except JobNotFoundError as error:
        raise JOB_NOT_FOUND from error
    except IngestionError as error:
        raise _as_http(error) from error

    response.headers["Location"] = f"/api/ingestions/{job_id}"
    return _represent(job)


@router.get("")
async def list_ingestions(
    owner_id: Annotated[UUID, Depends(current_owner)],
    limit: int = 20,
) -> JobListResponse:
    def load() -> list[IngestionJob]:
        with database_connection(readonly=True) as connection:
            return list_jobs(connection, owner_id=owner_id, limit=min(limit, 100))

    return JobListResponse(
        jobs=[_represent(job) for job in await run_in_threadpool(load)]
    )


@router.get("/limits")
async def read_limits() -> IngestionLimitsResponse:
    """The active upload limits. Declared before `/{job_id}` so the literal
    path is not read as a job identifier."""

    limits = load_limits()
    return IngestionLimitsResponse(
        maximum_bytes=limits.max_source_bytes,
        maximum_pages=limits.max_pages,
        allowed_content_types=list(limits.allowed_content_types),
    )


@router.get("/{job_id}")
async def read_ingestion(
    job_id: UUID,
    owner_id: Annotated[UUID, Depends(current_owner)],
) -> JobResponse:
    """Durable job status. This is what the UI polls while a job runs."""

    def load() -> IngestionJob:
        with database_connection(readonly=True) as connection:
            return get_job(connection, owner_id=owner_id, job_id=job_id)

    try:
        return _represent(await run_in_threadpool(load))
    except JobNotFoundError as error:
        raise JOB_NOT_FOUND from error


@router.get("/{job_id}/toc-proposal")
async def read_toc_proposal(
    job_id: UUID,
    owner_id: Annotated[UUID, Depends(current_owner)],
) -> OutlineReviewResponse:
    """Return the deterministic, non-canonical hierarchy awaiting review."""

    def load() -> IngestionJob:
        with database_connection(readonly=True) as connection:
            return get_job(connection, owner_id=owner_id, job_id=job_id)

    try:
        return _represent_outline_review(await run_in_threadpool(load))
    except JobNotFoundError as error:
        raise JOB_NOT_FOUND from error


@router.post(
    "/{job_id}/toc-confirmation",
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_toc_proposal(
    job_id: UUID,
    request: ConfirmOutlineRequest,
    response: Response,
    owner_id: Annotated[UUID, Depends(current_owner)],
) -> JobResponse:
    """Validate a reviewed hierarchy and re-queue the exact same source."""

    toc = [(entry.level, entry.title, entry.page) for entry in request.entries]

    def confirm() -> IngestionJob:
        with database_connection() as connection:
            job = get_job(connection, owner_id=owner_id, job_id=job_id)
            if job.page_count is None:
                raise JobConflictError(
                    "outline review has no source page count"
                )
            validate_table_of_contents(toc, job.page_count)
            return confirm_outline_review(
                connection,
                owner_id=owner_id,
                job_id=job_id,
                toc=toc,
            )

    try:
        job = await run_in_threadpool(confirm)
    except JobNotFoundError as error:
        raise JOB_NOT_FOUND from error
    except IngestionError as error:
        raise _as_http(error) from error
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    response.headers["Location"] = f"/api/ingestions/{job_id}"
    return _represent(job)


@router.post("/{job_id}/cancel")
async def cancel_ingestion(
    job_id: UUID,
    owner_id: Annotated[UUID, Depends(current_owner)],
) -> JobResponse:
    """Cancel a job. A running job stops at its next safe boundary."""

    def cancel() -> IngestionJob:
        with database_connection() as connection:
            return request_cancellation(
                connection, owner_id=owner_id, job_id=job_id
            )

    try:
        return _represent(await run_in_threadpool(cancel))
    except JobNotFoundError as error:
        raise JOB_NOT_FOUND from error
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{job_id}/retry")
async def retry_ingestion(
    job_id: UUID,
    owner_id: Annotated[UUID, Depends(current_owner)],
) -> JobResponse:
    """Re-queue a failed job whose failure was marked retryable."""

    limits = load_limits()

    def retry() -> IngestionJob:
        with database_connection() as connection:
            return retry_job(
                connection, owner_id=owner_id, job_id=job_id, limits=limits
            )

    try:
        return _represent(await run_in_threadpool(retry))
    except JobNotFoundError as error:
        raise JOB_NOT_FOUND from error
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IngestionError as error:
        raise _as_http(error) from error
