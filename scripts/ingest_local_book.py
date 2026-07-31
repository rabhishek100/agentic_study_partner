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

from ingestion.config import load_limits
from ingestion.errors import IngestionError
from ingestion.jobs import claim_next_job, get_job, list_jobs
from ingestion.local_source import inspect_local_source, queue_local_source
from ingestion.pipeline import PipelineDependencies, run_job
from ingestion.states import Status
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


def _existing_job(connection, *, owner_id, file_hash: str):
    """The job already carrying this file's bytes, if there is one."""

    for summary in list_jobs(connection, owner_id=owner_id, limit=100):
        if summary.file_hash == file_hash:
            return summary
    return None


def main(argv: list[str] | None = None) -> int:
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

        claimed = claim_next_job(connection, worker_id=WORKER_ID, limits=limits)

    if claimed is None or claimed.id != job.id:
        logger.error(
            "could not claim job %s; another worker may hold it, or it is "
            "waiting on outline review",
            job.id,
        )
        return 4

    with tempfile.TemporaryDirectory() as scratch:
        work_dir = arguments.work_dir or Path(scratch)
        try:
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
    sys.exit(main())
