"""One-stage-at-a-time runner for the independent video ingestion queue."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading

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
from video.media_store import FilesystemMediaStore, MediaStoreError
from video.pipeline import (
    MissingVideoTranscript,
    VideoPipelineDependencies,
    run_video_stage,
)
from video.states import Stage, TERMINAL
from video.vision import VisualAnalysisError


logger = logging.getLogger("study_partner.video_worker")
SUPPORTED_STAGES = frozenset(Stage)
DEFAULT_LEASE_SECONDS = 300


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
        interval = max(1.0, self.lease_seconds / 3)
        while not self._stop.wait(interval):
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
            except Exception:
                logger.exception(
                    "video lease renewal failed", extra={"job_id": str(self.job.id)}
                )


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
    ) -> None:
        self.worker_id = worker_id
        self.database_url = database_url
        self.dependencies = dependencies or VideoPipelineDependencies(
            media_store=FilesystemMediaStore(),
            youtube_acquirer=acquire_youtube,
        )
        self.temporary_root = temporary_root or Path(
            os.getenv("VIDEO_INGESTION_TEMP_ROOT") or tempfile.gettempdir()
        )
        self.lease_seconds = lease_seconds or int(
            os.getenv("VIDEO_INGESTION_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)
        )

    def work_directory(self, job_id: object) -> Path:
        return self.temporary_root / "study-partner-video-ingestion" / str(job_id)

    def claim(self) -> VideoIngestionJob | None:
        with database_connection(self.database_url) as connection:
            return claim_next_job(
                connection,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                supported_stages=SUPPORTED_STAGES,
            )

    def recover_abandoned_jobs(self) -> int:
        try:
            with database_connection(self.database_url) as connection:
                return reclaim_expired_leases(connection)
        except Exception:
            logger.exception("video lease reclamation failed")
            return 0

    def process(self, job: VideoIngestionJob) -> None:
        work_dir = self.work_directory(job.id)
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
        except BaseException as error:  # every attempt must converge
            self._record_failure(job, error, context)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _record_failure(
        self,
        job: VideoIngestionJob,
        error: BaseException,
        context: dict[str, object],
    ) -> None:
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
                    return
                if current.cancellation_requested:
                    finish_running_cancellation(
                        connection,
                        job_id=job.id,
                        worker_id=self.worker_id,
                        attempt_count=job.attempt_count,
                    )
                    return
                record_stage_failure(
                    connection,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    attempt_count=job.attempt_count,
                    code=code,
                    retryable=retryable,
                )
        except Exception:
            # The lease expiry path remains the final recovery mechanism.
            logger.exception("could not record video stage failure", extra=context)


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
