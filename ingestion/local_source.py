"""Ingest a book the platform's upload path cannot carry.

Supabase enforces a project-wide 50 MB ceiling on uploads, and on the free plan
that is not configurable. Two books in this corpus exceed it: a 351-page scan at
61 MB and a 427-page scan at 55 MB. Neither can reach the worker through the
browser, and neither should be shrunk to fit — they already carry only 110 to
160 dpi of real detail, and recompressing them would degrade the input to the
stage whose entire job is reading them accurately.

So this path exists: an operator points the worker at a file already on the
machine, and the job runs with Storage taken out of the loop. Everything after
acquisition is unchanged — the same preflight, the same transcription, the same
mandatory outline review, the same canonical import.

It is deliberately not an API. The 50 MB limit is a real product constraint and
the upload interface states it; this is the operator's way around it for the
library's own books, not a hole in the limit for anyone who asks.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from psycopg import Connection

from storage.database import parse_owner_id

from .config import IngestionLimits, load_limits
from .errors import ErrorCode, IngestionError
from .jobs import (
    LOCAL_SOURCE_BUCKET,
    IngestionJob,
    append_event,
    create_job,
    get_job,
)
from .states import Status


__all__ = ["LocalSource", "inspect_local_source", "queue_local_source"]

# Marks a job whose bytes never went through Storage. The columns are NOT NULL
# and the cleanup sweep deletes by them, so a scheme that cannot name a real
# object is safer than a plausible-looking path that points at nothing. It is
# also what keeps the shared worker from claiming these jobs.
LOCAL_BUCKET = LOCAL_SOURCE_BUCKET

HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class LocalSource:
    """A file on the worker's own filesystem, measured before it is queued."""

    path: Path
    size_bytes: int
    sha256: str

    def provenance(self) -> dict[str, object]:
        return {
            "origin": "local_admin_ingest",
            "filename": self.path.name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_local_source(path: Path) -> LocalSource:
    """Measure a local file, refusing anything that is not a readable PDF."""

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise IngestionError(
            ErrorCode.SOURCE_MISSING, detail=f"{resolved} is not a file"
        )
    size = resolved.stat().st_size
    if size <= 0:
        raise IngestionError(ErrorCode.INVALID_PDF, detail="source file is empty")
    with resolved.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise IngestionError(
                ErrorCode.INVALID_PDF, detail="source does not begin with %PDF-"
            )
    return LocalSource(path=resolved, size_bytes=size, sha256=_hash_file(resolved))


def queue_local_source(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source: LocalSource,
    limits: IngestionLimits | None = None,
) -> IngestionJob:
    """Create a queued job for a file already on this machine.

    The upload-size ceiling is deliberately not applied: carrying a book the
    platform's upload path cannot is the entire reason this exists. The page
    ceiling still is, because that one is about what the worker can process
    rather than what Storage will accept.
    """

    limits = limits or load_limits()
    owner = parse_owner_id(owner_id)

    job, _ = create_job(
        connection,
        owner_id=owner,
        idempotency_key=uuid4(),
        original_filename=source.path.name,
        content_type=None,
        content_length=None,
        limits=limits,
    )

    with connection.transaction():
        connection.execute(
            """
            update ingestion_jobs
            set status = %s,
                storage_bucket = %s,
                storage_path = %s,
                verified_size_bytes = %s,
                file_hash = %s,
                next_attempt_at = now(),
                provenance_json = provenance_json || %s
            where id = %s and owner_id = %s and status = %s
            """,
            (
                str(Status.QUEUED),
                LOCAL_BUCKET,
                f"{owner}/{job.id}/local.pdf",
                source.size_bytes,
                source.sha256,
                _jsonb({"local_source": source.provenance()}),
                job.id,
                owner,
                str(Status.AWAITING_UPLOAD),
            ),
        )
        append_event(
            connection,
            owner_id=owner,
            job_id=job.id,
            event_type="queued",
            status=Status.QUEUED,
            message="queued from a local file, bypassing Storage",
            metadata=source.provenance(),
        )
    return get_job(connection, owner_id=owner, job_id=job.id)


def _jsonb(value: dict[str, object]):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def is_local(job: IngestionJob) -> bool:
    """Whether a job's bytes live on this machine rather than in Storage."""

    return job.storage_bucket == LOCAL_BUCKET
