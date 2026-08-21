"""The deck runner the worker process polls alongside its other queues."""

from __future__ import annotations

import logging
import os
import threading

from storage.database import connection as database_connection

from . import jobs
from .generate import DeckGenerationError
from .pipeline import DeckCancellationRequested, DeckSourceError, run_deck_job

logger = logging.getLogger("study_partner.deck_worker")


class _LeaseRenewal:
    """Keeps one claimed job leased while its model calls run."""

    def __init__(
        self,
        *,
        job: jobs.DeckJob,
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
            target=self._renew, name=f"deck-lease-{self.job.id}", daemon=True
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
                    held = jobs.renew_lease(
                        connection,
                        job_id=self.job.id,
                        worker_id=self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                if not held:
                    logger.warning(
                        "deck lease lost", extra={"job_id": str(self.job.id)}
                    )
                    return
            except Exception:
                logger.exception(
                    "deck lease renewal failed", extra={"job_id": str(self.job.id)}
                )


class DeckWorker:
    """Narrow runner: claim one deck job, generate it, record the outcome."""

    def __init__(
        self,
        *,
        worker_id: str,
        database_url: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.database_url = database_url
        self.lease_seconds = lease_seconds or int(
            os.getenv("DECK_JOB_LEASE_SECONDS", jobs.DEFAULT_LEASE_SECONDS)
        )

    def claim(self) -> jobs.DeckJob | None:
        with database_connection(self.database_url) as connection:
            return jobs.claim_next_job(
                connection,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )

    def recover_abandoned_jobs(self) -> int:
        try:
            with database_connection(self.database_url) as connection:
                return jobs.reclaim_expired_leases(connection)
        except Exception:
            logger.exception("deck lease reclamation failed")
            return 0

    def process(self, job: jobs.DeckJob) -> None:
        context = {
            "job_id": str(job.id),
            "owner_id": str(job.owner_id),
            "attempt": job.attempt_count,
        }
        logger.info("deck job claimed", extra=context)
        try:
            with _LeaseRenewal(
                job=job,
                worker_id=self.worker_id,
                database_url=self.database_url,
                lease_seconds=self.lease_seconds,
            ):
                with database_connection(self.database_url) as connection:
                    run_deck_job(connection, job=job)
        except DeckCancellationRequested:
            logger.info("deck job cancelled at a safe boundary", extra=context)
            with database_connection(self.database_url) as connection:
                jobs.finish_cancellation(connection, job_id=job.id)
        except BaseException as error:  # every attempt must converge
            self._record_failure(job, error, context)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise

    def _record_failure(
        self, job: jobs.DeckJob, error: BaseException, context: dict[str, object]
    ) -> None:
        code, retryable = _classify(error)
        logger.warning(
            "deck job failed",
            extra={**context, "error_code": code},
            exc_info=not isinstance(error, (DeckSourceError, DeckGenerationError)),
        )
        try:
            with database_connection(self.database_url) as connection:
                jobs.fail_job(
                    connection,
                    job_id=job.id,
                    code=code,
                    detail=str(error),
                    retryable=retryable,
                )
        except Exception:
            # The lease expiry path remains the final recovery mechanism.
            logger.exception("could not record deck job failure", extra=context)


def _classify(error: BaseException) -> tuple[str, bool]:
    """A source problem will not fix itself; a provider problem usually does."""

    if isinstance(error, DeckSourceError):
        return "source_unavailable", False
    if isinstance(error, DeckGenerationError):
        return "generation_failed", True
    if isinstance(error, ValueError):
        return "invalid_scope", False
    return "unexpected_error", True
