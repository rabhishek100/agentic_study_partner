"""One-stage-at-a-time runner for the independent video ingestion queue."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Collection
from pathlib import Path

from observability import traced, record_error
from decks.source_preferences import enqueue_initial_for_video
from storage.database import connection as database_connection
from video.acquisition import AcquisitionError, acquire_youtube
from video.audio import AudioTranscriptionError
from video.errors import VideoErrorCode, VideoIngestionError
from video.evidence_store import VideoQualityGateError
from video.frames import FrameExtractionError, OcrError
from video.jobs import (
    VideoIngestionJob,
    claim_next_job,
    finish_running_cancellation,
    get_job,
    reclaim_expired_leases,
    record_stage_failure,
    renew_lease,
)
from video.media_store import MediaStoreError, configured_media_store
from video.pipeline import (
    MissingVideoTranscript,
    VideoPipelineDependencies,
    run_video_stage,
)
from video.states import TERMINAL, Stage, Status
from video.vision import VisualAnalysisError

logger = logging.getLogger("study_partner.video_worker")
SUPPORTED_STAGES = frozenset(Stage)
DEFAULT_LEASE_SECONDS = 300


def configured_supported_stages() -> frozenset[Stage]:
    """Return the stages this worker is allowed to claim.

    Hosted networks are sometimes blocked by a source provider even though
    the rest of the pipeline belongs in the hosted worker.  A comma-separated
    allowlist keeps routing explicit while retaining the same durable queue,
    leases, and checkpoints.
    """

    configured = os.getenv("VIDEO_WORKER_STAGES", "").strip()
    if not configured:
        return SUPPORTED_STAGES
    values = [value.strip() for value in configured.split(",") if value.strip()]
    if not values:
        raise ValueError("VIDEO_WORKER_STAGES must name at least one stage")
    try:
        return frozenset(Stage(value) for value in values)
    except ValueError as error:
        allowed = ", ".join(stage.value for stage in Stage)
        raise ValueError(
            f"VIDEO_WORKER_STAGES contains an unknown stage; expected: {allowed}"
        ) from error


class _LeaseRenewal:
    def __init__(
        self,
        *,
        job: VideoIngestionJob,
        worker_id: str,
        database_url: str | None,
        lease_seconds: int,
    ) -> None:
        self.job = job
        self.worker_id = worker_id
        self.database_url = database_url
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_LeaseRenewal":
        self._thread = threading.Thread(
            target=self._renew,
            name=f"video-lease-{self.job.id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _renew(self) -> None:
        # Keep heartbeats frequent even when an operator grants a long lease
        # for an hours-long media stage. A transient proxy/socket failure then
        # gets another fresh pooled connection promptly instead of waiting a
        # third of a multi-hour lease before trying again.
        interval = max(1.0, min(30.0, self.lease_seconds / 3))
        delay = interval
        while not self._stop.wait(delay):
            try:
                with database_connection(self.database_url) as connection:
                    held = renew_lease(
                        connection,
                        job_id=self.job.id,
                        worker_id=self.worker_id,
                        attempt_count=self.job.attempt_count,
                        lease_seconds=self.lease_seconds,
                    )
                if not held:
                    logger.warning("video lease lost", extra={"job_id": str(self.job.id)})
                    return
                delay = interval
            except Exception:
                logger.exception(
                    "video lease renewal failed", extra={"job_id": str(self.job.id)}
                )
                delay = min(5.0, interval)


class VideoWorker:
    """Narrow runner used by the existing local worker process."""

    def __init__(
        self,
        *,
        worker_id: str,
        database_url: str | None = None,
        dependencies: VideoPipelineDependencies | None = None,
        temporary_root: Path | None = None,
        lease_seconds: int | None = None,
        supported_stages: Collection[Stage] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.database_url = database_url
        self.dependencies = dependencies or VideoPipelineDependencies(
            media_store=configured_media_store(),
            youtube_acquirer=acquire_youtube,
        )
        self.temporary_root = temporary_root or Path(
            os.getenv("VIDEO_INGESTION_TEMP_ROOT") or tempfile.gettempdir()
        )
        self.lease_seconds = lease_seconds or int(
            os.getenv("VIDEO_INGESTION_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)
        )
        selected = (
            configured_supported_stages()
            if supported_stages is None
            else frozenset(Stage(stage) for stage in supported_stages)
        )
        if not selected:
            raise ValueError("video worker must support at least one stage")
        self.supported_stages = frozenset(selected)

    def work_directory(self, job_id: object) -> Path:
        return self.temporary_root / "study-partner-video-ingestion" / str(job_id)

    def claim(self) -> VideoIngestionJob | None:
        with database_connection(self.database_url) as connection:
            return claim_next_job(
                connection,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                supported_stages=self.supported_stages,
            )

    def recover_abandoned_jobs(self) -> int:
        try:
            with database_connection(self.database_url) as connection:
                return reclaim_expired_leases(connection)
        except Exception:
            logger.exception("video lease reclamation failed")
            return 0

    @traced("video.worker.VideoWorker.process", flow="video_ingestion")
    def process(self, job: VideoIngestionJob) -> None:
        work_dir = self.work_directory(job.id)
        preserve_work_dir = False
        context = {
            "job_id": str(job.id),
            "owner_id": str(job.owner_id),
            "video_id": str(job.video_id),
            "stage": str(job.stage),
            "attempt": job.attempt_count,
        }
        logger.info("video stage claimed", extra=context)
        try:
            with _LeaseRenewal(
                job=job,
                worker_id=self.worker_id,
                database_url=self.database_url,
                lease_seconds=self.lease_seconds,
            ):
                with database_connection(self.database_url) as connection:
                    run_video_stage(
                        connection,
                        job=job,
                        worker_id=self.worker_id,
                        work_dir=work_dir,
                        dependencies=self.dependencies,
                    )
                if job.stage is Stage.PUBLISH:
                    self._enqueue_initial_cards(job)
        except BaseException as error:  # every attempt must converge
            record_error(error)
            will_retry = self._record_failure(job, error, context)
            preserve_work_dir = will_retry and job.stage is Stage.ACQUIRE_SOURCE
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            if not preserve_work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _enqueue_initial_cards(self, job: VideoIngestionJob) -> None:
        """Dispatch after publish without making ingestion depend on cards."""

        try:
            with database_connection(self.database_url) as connection:
                queued = enqueue_initial_for_video(
                    connection,
                    owner_id=job.owner_id,
                    video_id=job.video_id,
                )
            if queued:
                logger.info(
                    "automatic lecture deck queued",
                    extra={
                        "owner_id": str(job.owner_id),
                        "video_id": str(job.video_id),
                    },
                )
        except Exception:
            logger.exception(
                "automatic lecture deck dispatch failed",
                extra={
                    "owner_id": str(job.owner_id),
                    "video_id": str(job.video_id),
                },
            )

    def _record_failure(
        self,
        job: VideoIngestionJob,
        error: BaseException,
        context: dict[str, object],
    ) -> bool:
        """Record a failure and report whether the database scheduled a retry."""

        code, retryable = _classify_failure(error)
        logger.warning(
            "video stage failed",
            extra={**context, "error_code": str(code)},
            exc_info=not isinstance(error, VideoIngestionError),
        )
        try:
            with database_connection(self.database_url) as connection:
                current = get_job(
                    connection, owner_id=job.owner_id, job_id=job.id
                )
                if current.status in TERMINAL:
                    return False
                if current.cancellation_requested:
                    finish_running_cancellation(
                        connection,
                        job_id=job.id,
                        worker_id=self.worker_id,
                        attempt_count=job.attempt_count,
                    )
                    return False
                failed = record_stage_failure(
                    connection,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    attempt_count=job.attempt_count,
                    code=code,
                    retryable=retryable,
                )
                return failed.status is Status.RETRY_SCHEDULED
        except Exception:
            # The lease expiry path remains the final recovery mechanism.
            logger.exception("could not record video stage failure", extra=context)
            return False


def _classify_failure(error: BaseException) -> tuple[VideoErrorCode, bool]:
    if isinstance(error, VideoIngestionError):
        return error.code, False
    if isinstance(error, MissingVideoTranscript):
        return VideoErrorCode.SOURCE_UNAVAILABLE, False
    if isinstance(error, VideoQualityGateError):
        return VideoErrorCode.INVALID_MEDIA, False
    if isinstance(
        error,
        (AcquisitionError, AudioTranscriptionError, VisualAnalysisError, OcrError),
    ):
        return VideoErrorCode.PROVIDER_UNAVAILABLE, True
    if isinstance(error, (FileNotFoundError, MediaStoreError)):
        return VideoErrorCode.SOURCE_UNAVAILABLE, True
    if isinstance(error, (ValueError, FrameExtractionError)):
        return VideoErrorCode.INVALID_MEDIA, False
    return VideoErrorCode.UNEXPECTED_ERROR, True
