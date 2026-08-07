"""Lease-based ingestion worker.

Polls Postgres, claims one job at a time, runs the structured pipeline, and
records the outcome. The process holds no inbound port and keeps nothing
important on local disk: the source PDF and the job state both live in managed
storage, so losing this container costs one attempt, never a book.
"""

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from uuid import uuid4

from dotenv import load_dotenv

from ingestion.cleanup import run_cleanup
from api.version import build_revision, build_time
from ingestion.config import IngestionLimits, load_limits
from ingestion.errors import ErrorCode, IngestionError, classify_failure
from ingestion.jobs import (
    IngestionJob,
    claim_next_job,
    fail_job,
    get_job,
    reclaim_expired_leases,
    renew_lease,
    schedule_retry,
)
from ingestion.pipeline import (
    CancellationRequested,
    PipelineDependencies,
    run_job,
)
from ingestion.states import Status, is_terminal
from storage.database import close_pools, connection as database_connection
from video.cleanup import run_video_cleanup
from video.worker import VideoWorker as StandaloneVideoWorker


logger = logging.getLogger("study_partner.worker")

TEMPORARY_DIRECTORY_NAME = "study-partner-ingestion"
# Renew well inside the lease so one slow renewal does not lose the job.
LEASE_RENEWAL_FRACTION = 3


class JsonFormatter(logging.Formatter):
    """Emit structured logs. Never include tokens, keys, URLs, or book text."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "job_id",
            "owner_id",
            "book_id",
            "video_id",
            "stage",
            "status",
            "attempt",
            "elapsed_seconds",
            "error_code",
            "error_detail",
            "pages",
            "chunks",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    # A file copy of the structured stream, so a failed attempt's traceback
    # survives the terminal scrollback and can be read by tooling.
    log_file = os.getenv("WORKER_LOG_FILE", "").strip()
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


@dataclass
class _LeaseRenewal:
    """Keeps one claimed job leased while its stages run."""

    job_id: str
    worker_id: str
    limits: IngestionLimits
    database_url: str | None
    _stop: threading.Event = None
    _thread: threading.Thread = None

    def __enter__(self) -> "_LeaseRenewal":
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._renew, name=f"lease-{self.job_id}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exception) -> bool:
        self._stop.set()
        self._thread.join(timeout=5)
        return False

    def _renew(self) -> None:
        interval = max(1.0, self.limits.lease_seconds / LEASE_RENEWAL_FRACTION)
        while not self._stop.wait(interval):
            try:
                with database_connection(self.database_url) as connection:
                    held = renew_lease(
                        connection,
                        job_id=self.job_id,
                        worker_id=self.worker_id,
                        limits=self.limits,
                    )
                if not held:
                    # Another worker owns this job now. Stop renewing; the
                    # pipeline's status guards stop this attempt from writing.
                    logger.warning("lease lost", extra={"job_id": self.job_id})
                    return
            except Exception:
                logger.exception("lease renewal failed", extra={"job_id": self.job_id})


class Worker:
    """One polling worker. Several may run; the queue semantics do not change."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        limits: IngestionLimits | None = None,
        database_url: str | None = None,
        dependencies: PipelineDependencies | None = None,
        temporary_root: Path | None = None,
        video_worker: StandaloneVideoWorker | None = None,
    ) -> None:
        self.limits = limits or load_limits()
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self.database_url = database_url
        self.dependencies = dependencies or PipelineDependencies()
        self.temporary_root = temporary_root or Path(
            os.getenv("INGESTION_TEMP_ROOT") or tempfile.gettempdir()
        )
        self._stopping = threading.Event()
        self.video_worker = video_worker or StandaloneVideoWorker(
            worker_id=self.worker_id,
            database_url=self.database_url,
            temporary_root=self.temporary_root,
        )
        self._prefer_video = False
        # Force the first loop iteration to run a retention pass, so a worker
        # that restarts daily still cleans up even with long intervals.
        self._last_cleanup = -float(self.limits.cleanup_interval_seconds)

    def request_stop(self, *_: object) -> None:
        """Stop claiming new work. The current job finishes its attempt."""

        if not self._stopping.is_set():
            logger.info("stop requested", extra={"job_id": None})
        self._stopping.set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def install_signal_handlers(self) -> None:
        def handle(signal_number: int, frame: FrameType | None) -> None:
            self.request_stop()

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)

    def work_directory(self, job_id: str) -> Path:
        return self.temporary_root / TEMPORARY_DIRECTORY_NAME / str(job_id)

    def claim(self) -> IngestionJob | None:
        with database_connection(self.database_url) as connection:
            return claim_next_job(
                connection, worker_id=self.worker_id, limits=self.limits
            )

    def recover_abandoned_jobs(self) -> int:
        """Return jobs whose worker died to the queue."""

        try:
            with database_connection(self.database_url) as connection:
                return reclaim_expired_leases(connection, limits=self.limits)
        except Exception:
            logger.exception("lease reclamation failed")
            return 0

    def process(self, job: IngestionJob) -> None:
        """Run one claimed job and record its outcome exactly once."""

        started = time.monotonic()
        work_dir = self.work_directory(str(job.id))
        context = {
            "job_id": str(job.id),
            "owner_id": str(job.owner_id),
            "attempt": job.attempt_count,
        }
        logger.info("job claimed", extra=context)

        try:
            with _LeaseRenewal(
                job_id=str(job.id),
                worker_id=self.worker_id,
                limits=self.limits,
                database_url=self.database_url,
            ):
                outcome = run_job(
                    job,
                    limits=self.limits,
                    work_dir=work_dir,
                    database_url=self.database_url,
                    dependencies=self.dependencies,
                )
        except CancellationRequested:
            logger.info(
                "job cancelled at a safe boundary",
                extra={**context, "elapsed_seconds": round(time.monotonic() - started)},
            )
        except BaseException as error:  # noqa: BLE001 - every failure is recorded
            self._record_failure(job, error, context, started)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
        else:
            if outcome.job.status is Status.NEEDS_TOC_REVIEW:
                logger.info(
                    "job paused for outline review",
                    extra={
                        **context,
                        "status": str(outcome.job.status),
                        "elapsed_seconds": round(time.monotonic() - started),
                    },
                )
            else:
                logger.info(
                    "job ready",
                    extra={
                        **context,
                        "book_id": outcome.book_id,
                        "elapsed_seconds": round(time.monotonic() - started),
                    },
                )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _record_failure(
        self,
        job: IngestionJob,
        error: BaseException,
        context: dict,
        started: float,
    ) -> None:
        """Classify a failed attempt and either reschedule it or end it."""

        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            error = IngestionError(
                ErrorCode.WORKER_TERMINATED, detail="worker was interrupted"
            )
        decision = classify_failure(
            error,
            attempt=job.attempt_count,
            max_attempts=job.max_attempts,
            job_id=str(job.id),
        )
        logger.warning(
            "job attempt failed",
            extra={
                **context,
                "error_code": str(decision.code),
                # `IngestionError.detail` exists for exactly this and was never
                # recorded, so a failed ingestion showed only its generic safe
                # message: which page, which headings, and which contract was
                # violated were all computed and then discarded.
                "error_detail": getattr(error, "detail", None) or repr(error),
                "elapsed_seconds": round(time.monotonic() - started),
            },
            exc_info=not isinstance(error, IngestionError),
        )
        recorded = (
            error
            if isinstance(error, IngestionError)
            else IngestionError(decision.code, detail=repr(error))
        )
        try:
            with database_connection(self.database_url) as connection:
                current = get_job(connection, owner_id=job.owner_id, job_id=job.id)
                if is_terminal(current.status):
                    # Cancelled or already finished elsewhere; nothing to record.
                    return
                if decision.retry:
                    schedule_retry(
                        connection,
                        owner_id=job.owner_id,
                        job_id=job.id,
                        current_status=current.status,
                        error=recorded,
                        delay_seconds=decision.delay_seconds,
                    )
                else:
                    fail_job(
                        connection,
                        owner_id=job.owner_id,
                        job_id=job.id,
                        current_status=current.status,
                        error=recorded,
                    )
        except Exception:
            # The job keeps its lease and is reclaimed once that lease expires,
            # so an unrecordable failure still converges instead of hanging.
            logger.exception("could not record job failure", extra=context)

    def run_retention_pass(self) -> None:
        """Apply the retention policy when its interval has elapsed."""

        now = time.monotonic()
        if now - self._last_cleanup < self.limits.cleanup_interval_seconds:
            return
        self._last_cleanup = now
        try:
            with database_connection(self.database_url) as connection:
                run_cleanup(connection, limits=self.limits)
        except Exception:
            logger.exception("retention pass failed")
        # Separate attempt: the two domains keep different objects in different
        # backends, and books failing to reach Storage must not be the reason
        # a volume sized for video never gets swept.
        try:
            with database_connection(self.database_url) as connection:
                run_video_cleanup(connection)
        except Exception:
            logger.exception("video retention pass failed")

    def run_once(self) -> bool:
        """Claim and run at most one job. True when work was done."""

        self.recover_abandoned_jobs()
        self.video_worker.recover_abandoned_jobs()
        self.run_retention_pass()
        if self._prefer_video:
            video_job = self.video_worker.claim()
            if video_job is not None:
                self.video_worker.process(video_job)
                self._prefer_video = False
                return True
            job = self.claim()
            if job is not None:
                self.process(job)
                self._prefer_video = True
                return True
        else:
            job = self.claim()
            if job is not None:
                self.process(job)
                self._prefer_video = True
                return True
            video_job = self.video_worker.claim()
            if video_job is not None:
                self.video_worker.process(video_job)
                self._prefer_video = False
                return True
        return False

    def run(self) -> None:
        """Poll until asked to stop."""

        logger.info(
            "worker started",
            extra={
                "job_id": None,
                "build_revision": build_revision(),
                "build_time": build_time(),
            },
        )
        while not self.stopping:
            try:
                worked = self.run_once()
            except Exception:
                logger.exception("worker loop error")
                worked = False
            if not worked and not self.stopping:
                self._stopping.wait(self.limits.poll_seconds)
        logger.info("worker stopped", extra={"job_id": None})


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the PDF ingestion worker.")
    parser.add_argument("--worker-id", help="Defaults to hostname plus a random suffix")
    parser.add_argument("--database-url", help="Defaults to DATABASE_URL")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one job and exit, for smoke tests",
    )
    return parser


def main() -> None:
    load_dotenv()
    configure_logging()
    arguments = build_argument_parser().parse_args()

    worker = Worker(worker_id=arguments.worker_id, database_url=arguments.database_url)
    worker.install_signal_handlers()
    try:
        if arguments.once:
            worker.run_once()
        else:
            worker.run()
    finally:
        close_pools()


if __name__ == "__main__":
    main()
