"""Stable ingestion error codes, retry classification, and backoff.

Users see a stable code and a safe sentence. Stack traces, SQL, provider
payloads, local paths, and credentials never leave the worker.
"""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import struct

import httpx
import psycopg
from psycopg import errors as postgres_errors


class ErrorCode(StrEnum):
    # Permanent: retrying cannot change the outcome.
    SOURCE_TOO_LARGE = "source_too_large"
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    INVALID_PDF = "invalid_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    TOO_MANY_PAGES = "too_many_pages"
    MISSING_TABLE_OF_CONTENTS = "missing_table_of_contents"
    INVALID_HIERARCHY = "invalid_hierarchy"
    UNSUPPORTED_DOCUMENT_CLASS = "unsupported_document_class"
    EXTRACTION_CONTRACT_VIOLATION = "extraction_contract_violation"
    EXTRACTION_QUALITY_REJECTED = "extraction_quality_rejected"
    CONTENT_LIMIT_EXCEEDED = "content_limit_exceeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    VERIFICATION_FAILED = "verification_failed"
    CANCELLED = "cancelled"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"

    # Transient: the same work may succeed later.
    STORAGE_UNAVAILABLE = "storage_unavailable"
    DATABASE_UNAVAILABLE = "database_unavailable"
    SERIALIZATION_FAILURE = "serialization_failure"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    WORKER_TERMINATED = "worker_terminated"
    LEASE_EXPIRED = "lease_expired"
    TEMPORARY_DISK_ERROR = "temporary_disk_error"
    UNEXPECTED_ERROR = "unexpected_error"


RETRYABLE_CODES = frozenset(
    {
        ErrorCode.STORAGE_UNAVAILABLE,
        ErrorCode.DATABASE_UNAVAILABLE,
        ErrorCode.SERIALIZATION_FAILURE,
        ErrorCode.PROVIDER_RATE_LIMITED,
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.WORKER_TERMINATED,
        ErrorCode.LEASE_EXPIRED,
        ErrorCode.TEMPORARY_DISK_ERROR,
        # An unclassified failure is retried a bounded number of times: it is
        # more often an infrastructure blip than a deterministic bug, and
        # max_attempts stops a real bug from looping forever.
        ErrorCode.UNEXPECTED_ERROR,
    }
)

SAFE_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.SOURCE_TOO_LARGE: "This file is larger than the upload limit.",
    ErrorCode.SOURCE_MISSING: "The uploaded file could not be found.",
    ErrorCode.SOURCE_CHANGED: "The uploaded file changed during processing.",
    ErrorCode.UNSUPPORTED_CONTENT_TYPE: "Only PDF files are supported.",
    ErrorCode.INVALID_PDF: "This file is not a readable PDF.",
    ErrorCode.ENCRYPTED_PDF: "Password-protected PDFs are not supported.",
    ErrorCode.TOO_MANY_PAGES: "This PDF has more pages than the current limit.",
    ErrorCode.MISSING_TABLE_OF_CONTENTS: (
        "This PDF has no embedded table of contents, which the current "
        "structured-book pipeline requires."
    ),
    ErrorCode.INVALID_HIERARCHY: (
        "This PDF's table of contents has invalid or out-of-order page ranges."
    ),
    ErrorCode.UNSUPPORTED_DOCUMENT_CLASS: (
        "Scanned PDFs are not supported yet; only digital PDFs with embedded "
        "text and a table of contents can be processed."
    ),
    ErrorCode.EXTRACTION_CONTRACT_VIOLATION: (
        "The extracted content did not match the expected structure."
    ),
    ErrorCode.EXTRACTION_QUALITY_REJECTED: (
        "Too little readable text could be extracted from this PDF."
    ),
    ErrorCode.CONTENT_LIMIT_EXCEEDED: (
        "This book's extracted content exceeds the configured safety limits."
    ),
    ErrorCode.QUOTA_EXCEEDED: "You have reached an ingestion limit.",
    ErrorCode.VERIFICATION_FAILED: (
        "The processed book failed its final consistency checks."
    ),
    ErrorCode.CANCELLED: "This ingestion was cancelled.",
    ErrorCode.ATTEMPTS_EXHAUSTED: (
        "Processing failed repeatedly and will not be retried automatically."
    ),
    ErrorCode.STORAGE_UNAVAILABLE: "File storage was temporarily unavailable.",
    ErrorCode.DATABASE_UNAVAILABLE: "The database was temporarily unavailable.",
    ErrorCode.SERIALIZATION_FAILURE: "A concurrent update interrupted processing.",
    ErrorCode.PROVIDER_RATE_LIMITED: "An external service rate-limited this job.",
    ErrorCode.PROVIDER_UNAVAILABLE: "An external service was temporarily unavailable.",
    ErrorCode.PROVIDER_TIMEOUT: "An external service timed out.",
    ErrorCode.WORKER_TERMINATED: "Processing was interrupted and will resume.",
    ErrorCode.LEASE_EXPIRED: "Processing stalled and will be picked up again.",
    ErrorCode.TEMPORARY_DISK_ERROR: "Temporary storage was unavailable.",
    ErrorCode.UNEXPECTED_ERROR: "Processing failed for an unexpected reason.",
}

# Capped exponential backoff. A provider Retry-After header takes precedence.
BACKOFF_SECONDS: tuple[int, ...] = (30, 120, 600, 1800)
MAXIMUM_BACKOFF_SECONDS = 1800
JITTER_FRACTION = 0.25


class IngestionError(Exception):
    """A failure with a stable code and a message that is safe to expose."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        detail: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = ErrorCode(code)
        # ``detail`` is for structured logs only and is never returned to a client.
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds
        super().__init__(detail or self.code.value)

    @property
    def retryable(self) -> bool:
        return is_retryable(self.code)

    @property
    def safe_message(self) -> str:
        return safe_message(self.code)


def is_retryable(code: ErrorCode | str) -> bool:
    return ErrorCode(code) in RETRYABLE_CODES


def safe_message(code: ErrorCode | str) -> str:
    """Return the user-facing sentence for a code, never internal details."""

    try:
        return SAFE_MESSAGES[ErrorCode(code)]
    except ValueError:
        return SAFE_MESSAGES[ErrorCode.UNEXPECTED_ERROR]


@dataclass(frozen=True)
class RetryDecision:
    """Whether a failed attempt is retried, and when."""

    retry: bool
    delay_seconds: float
    code: ErrorCode

    @property
    def retryable(self) -> bool:
        return is_retryable(self.code)


def _jitter(job_id: str, attempt: int) -> float:
    """Return a deterministic jitter multiplier in [1 - f, 1 + f].

    Deterministic per (job, attempt) so a retry schedule is reproducible in
    tests and in incident review, while still spreading concurrent retries.
    """

    digest = hashlib.sha256(f"{job_id}:{attempt}".encode()).digest()
    (raw,) = struct.unpack_from(">I", digest)
    return 1.0 + JITTER_FRACTION * (2.0 * (raw / 0xFFFFFFFF) - 1.0)


def backoff_seconds(
    attempt: int,
    *,
    job_id: str,
    retry_after_seconds: float | None = None,
) -> float:
    """Return the delay before attempt ``attempt + 1``.

    ``attempt`` is the number of attempts already made, so the first failure
    passes 1 and waits the first backoff step.
    """

    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return min(float(retry_after_seconds), float(MAXIMUM_BACKOFF_SECONDS))
    index = min(attempt - 1, len(BACKOFF_SECONDS) - 1)
    base = BACKOFF_SECONDS[index]
    return min(base * _jitter(job_id, attempt), float(MAXIMUM_BACKOFF_SECONDS))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Read a provider's Retry-After guidance when it is a plain delay."""

    raw = response.headers.get("Retry-After", "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        # HTTP-date form; the regular backoff schedule applies instead.
        return None


def recognize_exception(error: BaseException) -> tuple[ErrorCode, float | None]:
    """Name a raw exception from a provider, Storage, or the database.

    Stages raise IngestionError for everything they can anticipate; this is
    the safety net that keeps a recognizable infrastructure failure from
    being recorded as ``unexpected_error`` with the generic backoff. The
    first live ingestion surfaced exactly that gap.
    """

    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        if response.status_code == 429:
            return ErrorCode.PROVIDER_RATE_LIMITED, _retry_after_seconds(response)
        if response.status_code >= 500:
            return ErrorCode.PROVIDER_UNAVAILABLE, None
        return ErrorCode.UNEXPECTED_ERROR, None
    if isinstance(error, httpx.TimeoutException):
        return ErrorCode.PROVIDER_TIMEOUT, None
    if isinstance(error, httpx.HTTPError):
        return ErrorCode.PROVIDER_UNAVAILABLE, None
    if isinstance(
        error,
        (
            postgres_errors.SerializationFailure,
            postgres_errors.DeadlockDetected,
        ),
    ):
        return ErrorCode.SERIALIZATION_FAILURE, None
    if isinstance(error, psycopg.OperationalError):
        return ErrorCode.DATABASE_UNAVAILABLE, None
    if isinstance(error, OSError):
        return ErrorCode.TEMPORARY_DISK_ERROR, None
    return ErrorCode.UNEXPECTED_ERROR, None


def classify_failure(
    error: BaseException | IngestionError,
    *,
    attempt: int,
    max_attempts: int,
    job_id: str,
) -> RetryDecision:
    """Decide whether a failed attempt retries, and after how long."""

    if isinstance(error, IngestionError):
        code = error.code
        retry_after = error.retry_after_seconds
    else:
        code, retry_after = recognize_exception(error)

    if not is_retryable(code) or attempt >= max_attempts:
        return RetryDecision(retry=False, delay_seconds=0.0, code=code)
    return RetryDecision(
        retry=True,
        delay_seconds=backoff_seconds(
            attempt, job_id=job_id, retry_after_seconds=retry_after
        ),
        code=code,
    )
