"""Configurable ingestion limits.

Limits are read once per call so tests and deployments can change them through
the environment without reimporting anything. Every limit is enforced as early
as it can be: in the browser, in the Storage bucket, in the API, and again in
the worker before expensive work starts.
"""

from dataclasses import dataclass
import os


DEFAULT_SOURCE_BUCKET = "book-sources"
DEFAULT_MAX_SOURCE_BYTES = 104_857_600  # 100 MiB
# The design target, reachable now that parsing runs as bounded page batches
# across a process pool: each batch holds a fixed slice of the document, so
# parse memory no longer grows with book length. docs/parser-performance.md
# records the measurements behind this.
DEFAULT_MAX_PAGES = 1000
DEFAULT_MAX_QUEUED_JOBS_PER_OWNER = 3
DEFAULT_ALLOWED_CONTENT_TYPES = ("application/pdf",)
DEFAULT_LEASE_SECONDS = 300
DEFAULT_POLL_SECONDS = 5
DEFAULT_MAX_ATTEMPTS = 3
# A reserved path whose upload never completed is treated as abandoned after
# this long; the failed/cancelled source-retention window is deliberately
# longer so a user can still retry a failed job with its original object.
DEFAULT_ABANDONED_UPLOAD_HOURS = 24
DEFAULT_SOURCE_RETENTION_DAYS = 7
DEFAULT_CLEANUP_INTERVAL_SECONDS = 3600


def _int(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class IngestionLimits:
    """One immutable snapshot of the configured limits."""

    source_bucket: str = DEFAULT_SOURCE_BUCKET
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_pages: int = DEFAULT_MAX_PAGES
    max_queued_jobs_per_owner: int = DEFAULT_MAX_QUEUED_JOBS_PER_OWNER
    allowed_content_types: tuple[str, ...] = DEFAULT_ALLOWED_CONTENT_TYPES
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    poll_seconds: int = DEFAULT_POLL_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    abandoned_upload_hours: int = DEFAULT_ABANDONED_UPLOAD_HOURS
    source_retention_days: int = DEFAULT_SOURCE_RETENTION_DAYS
    cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS

    def storage_path(self, owner_id: object, job_id: object) -> str:
        """Return the immutable object path for one job's source PDF."""

        return f"{owner_id}/{job_id}/original.pdf"


def load_limits() -> IngestionLimits:
    """Read the active limits from the environment."""

    return IngestionLimits(
        source_bucket=os.getenv("INGESTION_SOURCE_BUCKET", "").strip()
        or DEFAULT_SOURCE_BUCKET,
        max_source_bytes=_int("INGESTION_MAX_SOURCE_BYTES", DEFAULT_MAX_SOURCE_BYTES),
        max_pages=_int("INGESTION_MAX_PAGES", DEFAULT_MAX_PAGES),
        max_queued_jobs_per_owner=_int(
            "INGESTION_MAX_QUEUED_JOBS_PER_OWNER",
            DEFAULT_MAX_QUEUED_JOBS_PER_OWNER,
        ),
        lease_seconds=_int("INGESTION_LEASE_SECONDS", DEFAULT_LEASE_SECONDS),
        poll_seconds=_int("INGESTION_WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS),
        max_attempts=_int("INGESTION_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
        abandoned_upload_hours=_int(
            "INGESTION_ABANDONED_UPLOAD_HOURS", DEFAULT_ABANDONED_UPLOAD_HOURS
        ),
        source_retention_days=_int(
            "INGESTION_SOURCE_RETENTION_DAYS", DEFAULT_SOURCE_RETENTION_DAYS
        ),
        cleanup_interval_seconds=_int(
            "INGESTION_CLEANUP_INTERVAL_SECONDS", DEFAULT_CLEANUP_INTERVAL_SECONDS
        ),
    )
