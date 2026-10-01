"""Ingest a book from this machine, bypassing the Storage upload ceiling.

Supabase enforces a project-wide 50 MB upload limit that the free plan cannot
raise. Two books in this corpus are larger, and shrinking them is not an
option: they already carry only 110-160 dpi of real detail, and recompressing
would degrade the input to the stage whose whole job is reading them.

Everything after acquisition is the ordinary pipeline. In particular a scanned
book still stops at `needs_toc_review` and still needs its outline confirmed in
the interface before it becomes answerable, so this is a way around the upload
limit and not a way around the review gate.

    uv run python -m scripts.ingest_local_book --source book.pdf

Re-run the same command after confirming the outline to resume the job.
"""


import argparse
import logging
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from observability import traced
from ingestion.config import load_limits
from ingestion.errors import IngestionError
from ingestion.jobs import claim_next_job, get_job, list_jobs
from ingestion.local_source import (
    LOCAL_BUCKET,
    inspect_local_source,
    queue_local_source,
)
from ingestion.pipeline import PipelineDependencies, run_job
from ingestion.states import Status
from worker.main import _LeaseRenewal
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)


logger = logging.getLogger("study_partner.scripts.ingest_local_book")

WORKER_ID = "local-admin-ingest"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest a PDF already on this machine, skipping Storage."
    )
    parser.add_argument(
        "--source", type=Path, required=True, help="PDF to ingest"
    )
    parser.add_argument(
        "--owner-id", help="Owner UUID; defaults to DEFAULT_OWNER_ID"
    )
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL")
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Scratch directory; a temporary one is used when omitted",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Continue an existing job for this file instead of creating one, "
            "which is what to use after confirming an outline"
        ),
    )
    return parser


def _why_unclaimable(job) -> str:
    """Say what is actually stopping the claim, not what usually would.

    The first version guessed - "another worker may hold it, or it is waiting
    on outline review" - and the real reason was a sixteen-second retry
    backoff, which the job itself records. A plausible cause reads exactly like
    a measured one and sends the reader somewhere else entirely.
    """

    from datetime import datetime, timezone

    if job.status is Status.NEEDS_TOC_REVIEW:
        return "it is waiting for its outline to be confirmed"
    if job.lease_owner:
        return f"another worker holds it ({job.lease_owner})"
    if job.cancellation_requested:
        return "cancellation was requested"
    if job.next_attempt_at is not None:
        remaining = (job.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            return f"a retry backoff has {remaining:.0f}s left; run again after it"
    if job.status is Status.READY:
        return "it is already finished"
    return f"it is in status {job.status} with nothing scheduled"


def _existing_job(connection, *, owner_id, file_hash: str):
    """The job already carrying this file's bytes, if there is one."""

    for summary in list_jobs(connection, owner_id=owner_id, limit=100):
        if summary.file_hash == file_hash:
            return summary
    return None


def _ensure_readable(
    book_id: int,
    *,
    source,
    owner_id,
    limits,
    database_url: str | None,
) -> None:
    """Give the finished book something the reading pane can open.

    A book ingested this way never uploaded its bytes, so the pane has nothing
    to show unless one is stored deliberately. Doing it here rather than as a
    step an operator has to remember: forgetting it produces a finished,
    answerable book whose every citation is unopenable, and the failure is
    silent until somebody clicks.
    """

    from ingestion.storage_objects import upload_object
    from ingestion.viewer_copy import build_viewer_copy, needs_viewer_copy
    from storage.database import connection as database_connection

    with database_connection(database_url) as connection:
        stored = connection.execute(
            """
            select viewer_storage_path, source_storage_bucket
            from books where id = %s and owner_id = %s
            """,
            (book_id, owner_id),
        ).fetchone()
    if stored is None or stored["viewer_storage_path"]:
        return
    if stored["source_storage_bucket"] != LOCAL_BUCKET:
        return

    with tempfile.TemporaryDirectory() as scratch:
        destination = Path(scratch) / "viewer.pdf"
        if needs_viewer_copy(source.size_bytes, ceiling_bytes=limits.max_source_bytes):
            logger.info("rendering a readable copy for the reading pane")
            copy = build_viewer_copy(
                source.path, destination, ceiling_bytes=limits.max_source_bytes
            )
            if copy is None:
                logger.warning("no readable copy fits; the reading pane stays empty")
                return
            payload = copy.path.read_bytes()
        else:
            payload = source.path.read_bytes()

        path = f"{owner_id}/book-{book_id}/viewer.pdf"
        try:
            upload_object(limits.source_bucket, path, payload, overwrite=True)
        except IngestionError as error:
            logger.warning("could not store the readable copy: %s", error.safe_message)
            return

    with database_connection(database_url) as connection:
        connection.execute(
            """
            update books set viewer_storage_bucket = %s, viewer_storage_path = %s
            where id = %s and owner_id = %s
            """,
            (limits.source_bucket, path, book_id, owner_id),
        )
    logger.info("book %s can be opened in the reading pane", book_id)


@traced("scripts.ingest_local_book.main", flow="cli")
def main(argv: list[str] | None = None) -> int:
    # Every other entry point that reaches the pipeline does this, and without
    # it the whole run is configured by defaults. The database URL happened to
    # match its fallback, so the first thing to actually notice was the
    # embedder, three stages in, refusing a key that was sitting in `.env` all
    # along.
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    arguments = build_argument_parser().parse_args(argv)
    owner_id = (
        parse_owner_id(arguments.owner_id)
        if arguments.owner_id
        else environment_owner_id()
    )
    limits = load_limits()

    try:
        source = inspect_local_source(arguments.source)
    except IngestionError as error:
        logger.error("%s", error.safe_message)
        return 2

    logger.info(
        "source %s: %.1f MB, sha256 %s",
        source.path.name,
        source.size_bytes / 1e6,
        source.sha256[:12],
    )
    if source.size_bytes > limits.max_source_bytes:
        logger.info(
            "larger than the %.0f MB upload ceiling, which is why this path exists",
            limits.max_source_bytes / 1e6,
        )

    with database_connection(arguments.database_url) as connection:
        existing = _existing_job(connection, owner_id=owner_id, file_hash=source.sha256)
        if existing is not None and not arguments.resume:
            logger.error(
                "job %s already carries this file (status %s); pass --resume to "
                "continue it",
                existing.id,
                existing.status,
            )
            return 3
        if existing is None:
            job = queue_local_source(
                connection, owner_id=owner_id, source=source, limits=limits
            )
            logger.info("queued job %s", job.id)
        else:
            job = get_job(connection, owner_id=owner_id, job_id=existing.id)
            logger.info("resuming job %s from status %s", job.id, job.status)

        # Only this process can serve a local job: the file is on this machine
        # and nowhere else, which is why the shared worker cannot see it.
        claimed = claim_next_job(
            connection, worker_id=WORKER_ID, limits=limits, include_local=True
        )

    if claimed is None or claimed.id != job.id:
        logger.error("could not claim job %s: %s", job.id, _why_unclaimable(job))
        return 4

    with tempfile.TemporaryDirectory() as scratch:
        work_dir = arguments.work_dir or Path(scratch)
        # Transcribing a long book takes many minutes, and the lease is five.
        # Without renewal the job is reclaimed mid-run and every write after
        # that is refused by the status guards - which is exactly what happened
        # on the first production attempt, after all 427 pages had been read
        # and paid for. The checkpoints survived; the job did not.
        try:
            with _LeaseRenewal(
                job_id=str(claimed.id),
                worker_id=WORKER_ID,
                limits=limits,
                database_url=arguments.database_url,
            ):
                outcome = run_job(
                    claimed,
                    limits=limits,
                    work_dir=Path(work_dir) / str(claimed.id),
                    database_url=arguments.database_url,
                    dependencies=PipelineDependencies(),
                    local_source=source.path,
                )
        except IngestionError as error:
            logger.error("ingestion failed: %s", error.safe_message)
            return 5

    final = outcome.job
    if final.status is Status.READY and outcome.book_id is not None:
        _ensure_readable(
            outcome.book_id,
            source=source,
            owner_id=owner_id,
            limits=limits,
            database_url=arguments.database_url,
        )

    if final.status is Status.NEEDS_TOC_REVIEW:
        logger.info(
            "job %s is waiting on outline review. Confirm the hierarchy in the "
            "interface, then re-run this command with --resume.",
            final.id,
        )
        return 0
    if final.status is Status.READY:
        logger.info("book %s is ready", outcome.book_id)
        return 0

    logger.warning("job %s finished in status %s", final.id, final.status)
    return 1


if __name__ == "__main__":
    load_dotenv()
    sys.exit(main())
