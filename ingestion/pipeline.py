"""The structured-PDF ingestion pipeline.

One claimed job runs through validate, parse, persist, chunk, embed, and
verify. Each stage is idempotent, commits its own short transactions, and
never holds a transaction open across a download, a parse, or a provider call.

Deliberately plain Python: this is deterministic orchestration, not an agent
making decisions. LangGraph stays where inspectable choices actually happen.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from parsing.models import ParsedBook
from parsing.parser import PARSER_VERSION, parse_book
from retrieval.models import ChunkingConfig
from retrieval.postgres import rebuild as rebuild_chunks
from retrieval.vector import rebuild_vector_index
from storage.database import book_retrieval_completeness
from storage.database import connection as database_connection
from storage.postgres import (
    BookAlreadyExistsError,
    InvalidBookError,
    book_for_job,
    canonical_counts,
    delete_book,
    ingest_book,
    mark_book_ready,
    ready_book_by_hash,
    restore_book,
)
from .config import IngestionLimits
from .errors import ErrorCode, IngestionError
from .jobs import (
    IngestionJob,
    advance_stage,
    append_event,
    cancel_running_job,
    complete_job,
    get_job,
    record_progress,
    set_stage,
)
from .preflight import PreflightReport, preflight, require_supported
from .storage_objects import download_object, object_info
from .states import Stage, Status


logger = logging.getLogger("study_partner.ingestion.pipeline")

SOURCE_FILENAME = "original.pdf"
ELEMENTS_CACHE = "elements.json"
PARSED_BOOK_CACHE = "parsed_book.json"

# An extraction this empty is not worth importing; something went wrong even
# though the parser returned without raising.
MINIMUM_TOTAL_CHARACTERS = 200
MINIMUM_SECTIONS_WITH_TEXT = 0.10


class CancellationRequested(Exception):
    """The owner asked to stop and the job reached a safe boundary."""


@dataclass
class PipelineDependencies:
    """Injection points, so tests can run the pipeline without paid calls."""

    embedder_factory: Callable[[], Any] | None = None
    chunking_config: ChunkingConfig = field(default_factory=ChunkingConfig)

    def embedder(self) -> Any:
        if self.embedder_factory is not None:
            return self.embedder_factory()
        from retrieval.vector import build_embedder

        return build_embedder()


@dataclass
class JobOutcome:
    """What one pipeline run produced."""

    job: IngestionJob
    book_id: int | None
    duplicate_of: int | None = None

    @property
    def duplicate(self) -> bool:
        return self.duplicate_of is not None


def _database(database_url: str | None):
    return database_connection(database_url)


def _check_cancelled(
    job: IngestionJob,
    *,
    database_url: str | None,
) -> IngestionJob:
    """Stop at a stage boundary when cancellation has been requested.

    Cooperative on purpose: a job is never abandoned midway through canonical
    persistence, so there is no half-imported book to clean up.
    """

    with _database(database_url) as connection:
        current = get_job(connection, owner_id=job.owner_id, job_id=job.id)
        if not current.cancellation_requested:
            return current
        cancel_running_job(
            connection,
            owner_id=current.owner_id,
            job_id=current.id,
            current_status=current.status,
        )
    raise CancellationRequested(str(job.id))


def evaluate_extraction(
    book: ParsedBook,
    report: PreflightReport,
) -> dict[str, int | float]:
    """Measure what the parser produced and reject clearly unusable output.

    Structural contract failures are fatal in canonical ingestion. This gate
    catches the other case: a parse that succeeded mechanically but returned
    almost nothing, which would otherwise become an empty, unanswerable book.
    """

    total_characters = sum(len(section.full_text) for section in book.sections)
    sections_with_text = sum(1 for section in book.sections if section.full_text)
    metrics = {
        "sections": len(book.sections),
        "sections_with_text": sections_with_text,
        "total_characters": total_characters,
        "tables": sum(len(section.tables) for section in book.sections),
        "images": sum(len(section.images) for section in book.sections),
    }

    if len(book.sections) != len(report.toc):
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION,
            detail=(
                f"{len(book.sections)} sections for {len(report.toc)} outline entries"
            ),
        )
    if total_characters < MINIMUM_TOTAL_CHARACTERS:
        raise IngestionError(
            ErrorCode.EXTRACTION_QUALITY_REJECTED,
            detail=f"only {total_characters} extracted characters",
        )
    if sections_with_text < max(1, MINIMUM_SECTIONS_WITH_TEXT * len(book.sections)):
        raise IngestionError(
            ErrorCode.EXTRACTION_QUALITY_REJECTED,
            detail=(
                f"{sections_with_text} of {len(book.sections)} sections carry text"
            ),
        )
    return metrics


def verify_book(
    connection,
    *,
    owner_id: UUID,
    book_id: int,
    expected: dict[str, int | float],
) -> dict[str, Any]:
    """Prove a book is answerable before anyone can select it.

    Every check here is something that, if wrong, would show up as a missing
    citation or an empty answer in front of a reader.
    """

    try:
        restored = restore_book(connection, book_id, owner_id=owner_id)
    except (InvalidBookError, KeyError) as error:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED,
            detail=f"canonical restoration failed: {error}",
        ) from error

    counts = canonical_counts(connection, book_id, owner_id=owner_id)
    if counts["nodes"] != expected["sections"]:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED,
            detail=f"{counts['nodes']} nodes stored for {expected['sections']}",
        )
    if counts["tables"] != expected["tables"] or counts["images"] != expected["images"]:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED,
            detail=(
                f"payload counts differ: stored {counts['tables']} tables and "
                f"{counts['images']} images"
            ),
        )
    restored_characters = sum(len(section.full_text) for section in restored.sections)
    if restored_characters != expected["total_characters"]:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED,
            detail=(
                f"restored {restored_characters} characters, "
                f"extracted {expected['total_characters']}"
            ),
        )

    chunk_count, embedding_count = book_retrieval_completeness(
        connection, owner_id=owner_id, book_id=book_id
    )
    if chunk_count < 1:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED, detail="no chunks were built"
        )
    if embedding_count != chunk_count:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED,
            detail=f"{embedding_count} embeddings for {chunk_count} chunks",
        )

    orphans = connection.execute(
        """
        select count(*) as orphans
        from chunk_sources
        left join content_blocks
          on content_blocks.id = chunk_sources.source_block_id
         and content_blocks.owner_id = chunk_sources.owner_id
        where chunk_sources.owner_id = %s
          and chunk_sources.source_book_id = %s
          and content_blocks.id is null
        """,
        (owner_id, book_id),
    ).fetchone()["orphans"]
    if orphans:
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED,
            detail=f"{orphans} chunk sources do not resolve to canonical blocks",
        )

    return {
        "nodes": counts["nodes"],
        "blocks": counts["blocks"],
        "tables": counts["tables"],
        "images": counts["images"],
        "chunks": chunk_count,
        "embeddings": embedding_count,
    }


def _validate_stage(
    job: IngestionJob,
    *,
    limits: IngestionLimits,
    work_dir: Path,
    database_url: str | None,
) -> tuple[IngestionJob, Path, str, PreflightReport] | JobOutcome:
    """Verify the upload, hash it, and decide whether this book can be ingested."""

    stored = object_info(job.storage_bucket, job.storage_path)
    if stored is None:
        raise IngestionError(
            ErrorCode.SOURCE_MISSING, detail="source object is gone at parse time"
        )
    if stored.size_bytes > limits.max_source_bytes:
        raise IngestionError(
            ErrorCode.SOURCE_TOO_LARGE, detail=f"stored object {stored.size_bytes} bytes"
        )
    if (
        job.verified_size_bytes is not None
        and stored.size_bytes != job.verified_size_bytes
    ):
        raise IngestionError(
            ErrorCode.SOURCE_CHANGED,
            detail=(
                f"object is {stored.size_bytes} bytes, "
                f"{job.verified_size_bytes} were verified"
            ),
        )

    with _database(database_url) as connection:
        job = set_stage(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            current_status=Status.VALIDATING,
            stage=Stage.DOWNLOAD_SOURCE,
        )

    source = work_dir / SOURCE_FILENAME
    download = download_object(
        job.storage_bucket,
        job.storage_path,
        source,
        maximum_bytes=limits.max_source_bytes,
    )
    if download.size_bytes != stored.size_bytes:
        raise IngestionError(
            ErrorCode.SOURCE_CHANGED,
            detail="object changed while it was being downloaded",
        )

    # An owner who uploads a book they already have gets that book back rather
    # than a second copy. The existing book is never replaced automatically.
    with _database(database_url) as connection:
        duplicate = ready_book_by_hash(
            connection, owner_id=job.owner_id, file_hash=download.sha256
        )
        if duplicate is not None:
            finished = complete_job(
                connection,
                owner_id=job.owner_id,
                job_id=job.id,
                current_status=Status.VALIDATING,
                book_id=duplicate["id"],
                duplicate_of=duplicate["id"],
            )
            return JobOutcome(
                job=finished, book_id=duplicate["id"], duplicate_of=duplicate["id"]
            )

        job = set_stage(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            current_status=Status.VALIDATING,
            stage=Stage.PREFLIGHT,
            file_hash=download.sha256,
            verified_size_bytes=download.size_bytes,
        )

    report = preflight(source, limits=limits)
    with _database(database_url) as connection:
        job = set_stage(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            current_status=Status.VALIDATING,
            stage=Stage.PREFLIGHT,
            provenance={"preflight": report.provenance()},
            page_count=report.page_count,
            document_class=report.document_class,
        )
        for warning in report.warnings:
            append_event(
                connection,
                owner_id=job.owner_id,
                job_id=job.id,
                event_type="preflight_warning",
                status=job.status,
                stage=Stage.PREFLIGHT,
                message=warning,
            )
    require_supported(report)
    return job, source, download.sha256, report


POST_PERSIST_STATUSES = (
    Status.PERSISTING,
    Status.CHUNKING,
    Status.EMBEDDING,
    Status.VERIFYING,
)


def _enter_stage(
    connection,
    job: IngestionJob,
    *,
    status: Status,
    stage: Stage,
    reset_progress: bool = True,
    **columns: Any,
) -> IngestionJob:
    """Enter a stage from either a fresh run or a resumed one."""

    if job.status is status:
        return set_stage(
            connection,
            owner_id=job.owner_id,
            job_id=job.id,
            current_status=status,
            stage=stage,
            **columns,
        )
    return advance_stage(
        connection,
        owner_id=job.owner_id,
        job_id=job.id,
        current_status=job.status,
        status=status,
        stage=stage,
        reset_progress=reset_progress,
        **columns,
    )


def _ingest_source(
    job: IngestionJob,
    *,
    limits: IngestionLimits,
    work_dir: Path,
    database_url: str | None,
) -> tuple[IngestionJob, int, dict[str, int | float]] | JobOutcome:
    """Validate, parse, and canonically import the source.

    Returns the job, its book, and the extraction metrics later stages verify
    against, or a finished outcome when the upload turned out to be a
    duplicate of a book the owner already has.
    """

    owner_id = job.owner_id
    validated = _validate_stage(
        job, limits=limits, work_dir=work_dir, database_url=database_url
    )
    if isinstance(validated, JobOutcome):
        return validated
    job, source, file_hash, report = validated
    job = _check_cancelled(job, database_url=database_url)

    with _database(database_url) as connection:
        job = advance_stage(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            current_status=job.status,
            status=Status.PARSING,
            stage=Stage.PARSE_PAGES,
        )
        record_progress(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            completed=0,
            total=report.page_count,
            unit="pages",
        )

    try:
        book = parse_book(
            source,
            force=True,
            book_cache=work_dir / PARSED_BOOK_CACHE,
            elements_cache=work_dir / ELEMENTS_CACHE,
        )
    except IngestionError:
        raise
    except ValueError as error:
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION, detail=f"parser rejected: {error}"
        ) from error
    except Exception as error:
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION, detail=f"parser failed: {error!r}"
        ) from error

    metrics = evaluate_extraction(book, report)
    with _database(database_url) as connection:
        record_progress(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            completed=report.page_count,
            total=report.page_count,
            unit="pages",
        )
    job = _check_cancelled(job, database_url=database_url)

    with _database(database_url) as connection:
        job = advance_stage(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            current_status=job.status,
            status=Status.PERSISTING,
            stage=Stage.PERSIST_CANONICAL,
            provenance={"parser": {"version": PARSER_VERSION, **metrics}},
        )
        book_id = _persist_canonical(
            connection,
            job=job,
            book=book,
            report=report,
            file_hash=file_hash,
        )
    return job, book_id, metrics


def _committed_book(
    job: IngestionJob,
    *,
    database_url: str | None,
) -> tuple[int, dict[str, int | float]] | None:
    """Return the book and metrics a previous attempt already committed.

    Canonical ingestion is one transaction, so a book row for this job means
    the import finished. Its metrics come from the stored rows rather than from
    a parse that would otherwise have to be repeated.
    """

    if job.status not in POST_PERSIST_STATUSES:
        return None
    with _database(database_url) as connection:
        existing = book_for_job(
            connection, owner_id=job.owner_id, ingestion_job_id=job.id
        )
        if existing is None or existing["file_hash"] != job.file_hash:
            return None
        counts = canonical_counts(connection, existing["id"], owner_id=job.owner_id)
        characters = connection.execute(
            """
            select coalesce(sum(direct_char_count), 0) as characters,
                   count(*) filter (where direct_text <> '') as with_text
            from nodes where book_id = %s and owner_id = %s
            """,
            (existing["id"], job.owner_id),
        ).fetchone()
    return int(existing["id"]), {
        "sections": counts["nodes"],
        "sections_with_text": int(characters["with_text"]),
        "total_characters": int(characters["characters"]),
        "tables": counts["tables"],
        "images": counts["images"],
    }


def run_job(
    job: IngestionJob,
    *,
    limits: IngestionLimits,
    work_dir: Path,
    database_url: str | None = None,
    dependencies: PipelineDependencies | None = None,
) -> JobOutcome:
    """Run one claimed job to a ready book, a duplicate, or an exception.

    A resumed job continues from the canonical import when a previous attempt
    committed one, and otherwise restarts from validation. Restarting is always
    safe; continuing on top of work that cannot be verified is not.

    The caller owns failure handling: it classifies the exception, decides
    whether to retry, and records the outcome on the job.
    """

    dependencies = dependencies or PipelineDependencies()
    work_dir.mkdir(parents=True, exist_ok=True)
    owner_id = job.owner_id

    resumed = _committed_book(job, database_url=database_url)
    if resumed is not None:
        book_id, metrics = resumed
        logger.info("resuming job %s from canonical book %s", job.id, book_id)
        job = _check_cancelled(job, database_url=database_url)
        if job.status in (Status.EMBEDDING, Status.VERIFYING):
            # Rebuild the derived data rather than trusting a partial build
            # from the attempt that died.
            with _database(database_url) as connection:
                job = advance_stage(
                    connection,
                    owner_id=owner_id,
                    job_id=job.id,
                    current_status=job.status,
                    status=Status.CHUNKING,
                    stage=Stage.BUILD_CHUNKS,
                )
    else:
        if job.status is not Status.VALIDATING:
            with _database(database_url) as connection:
                job = advance_stage(
                    connection,
                    owner_id=owner_id,
                    job_id=job.id,
                    current_status=job.status,
                    status=Status.VALIDATING,
                    stage=Stage.VERIFY_UPLOAD,
                )
        ingested = _ingest_source(
            job,
            limits=limits,
            work_dir=work_dir,
            database_url=database_url,
        )
        if isinstance(ingested, JobOutcome):
            # A duplicate upload was resolved during validation.
            return ingested
        job, book_id, metrics = ingested

    # Chunk -------------------------------------------------------------
    with _database(database_url) as connection:
        job = _enter_stage(
            connection,
            job,
            status=Status.CHUNKING,
            stage=Stage.BUILD_CHUNKS,
            book_id=book_id,
        )
    with _database(database_url) as connection:
        summary = rebuild_chunks(
            connection,
            book_id,
            owner_id=owner_id,
            config=dependencies.chunking_config,
        )
    with _database(database_url) as connection:
        record_progress(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            completed=summary.chunk_count,
            total=summary.chunk_count,
            unit="chunks",
        )
    job = _check_cancelled(job, database_url=database_url)

    # Embed -------------------------------------------------------------
    with _database(database_url) as connection:
        job = _enter_stage(
            connection,
            job,
            status=Status.EMBEDDING,
            stage=Stage.BUILD_EMBEDDINGS,
            provenance={"chunker": {"chunks": summary.chunk_count}},
        )
    # No transaction is held across the provider calls inside this rebuild.
    with _database(database_url) as connection:
        vectors = rebuild_vector_index(
            connection,
            embedder=dependencies.embedder(),
            owner_id=owner_id,
            book_id=book_id,
        )
    with _database(database_url) as connection:
        record_progress(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            completed=vectors.total_count,
            total=vectors.total_count,
            unit="chunks",
        )
    job = _check_cancelled(job, database_url=database_url)

    # Verify and publish ------------------------------------------------
    with _database(database_url) as connection:
        job = _enter_stage(
            connection,
            job,
            status=Status.VERIFYING,
            stage=Stage.VERIFY_BOOK,
            # Keep the embedding counters: verification is a handful of checks,
            # and a finished job should not report zero progress.
            reset_progress=False,
            provenance={
                "embedding": {
                    "model": vectors.model_name,
                    "dimension": vectors.dimension,
                    "embedded": vectors.embedded_count,
                    "reused": vectors.unchanged_count,
                }
            },
        )
        verified = verify_book(
            connection, owner_id=owner_id, book_id=book_id, expected=metrics
        )

    with _database(database_url) as connection:
        mark_book_ready(connection, book_id, owner_id=owner_id)
        job = complete_job(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            current_status=Status.VERIFYING,
            book_id=book_id,
        )
        append_event(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            event_type="verified",
            status=Status.READY,
            metadata=verified,
        )
    return JobOutcome(job=job, book_id=book_id)


def _persist_canonical(
    connection,
    *,
    job: IngestionJob,
    book: ParsedBook,
    report: PreflightReport,
    file_hash: str,
) -> int:
    """Import canonical content, reusing what a previous attempt committed.

    Canonical ingestion is a single transaction, so an existing book row for
    this job means the whole import already succeeded and can be reused. A row
    that does not match the current source is removed and rebuilt.
    """

    existing = book_for_job(
        connection, owner_id=job.owner_id, ingestion_job_id=job.id
    )
    if existing is not None:
        if (
            existing["file_hash"] == file_hash
            and existing["parser_version"] == PARSER_VERSION
        ):
            logger.info(
                "reusing canonical book %s committed by an earlier attempt",
                existing["id"],
            )
            return int(existing["id"])
        delete_book(connection, existing["id"], owner_id=job.owner_id)

    title = report.metadata.get("title") or Path(job.original_filename).stem
    author = report.metadata.get("author")
    try:
        return ingest_book(
            connection,
            book,
            owner_id=job.owner_id,
            title=title,
            author=author,
            file_hash=file_hash,
            page_count=report.page_count,
            parser_version=PARSER_VERSION,
            metadata={"pdf": report.metadata, "preflight": report.provenance()},
            source_storage_bucket=job.storage_bucket,
            source_storage_path=job.storage_path,
            ingestion_job_id=job.id,
            ready=False,
        )
    except BookAlreadyExistsError as error:
        # The owner has this hash in a book that is not this job's, and it is
        # not ready, so duplicate detection did not catch it. Refusing is
        # correct: an automatic replace could destroy their existing book.
        raise IngestionError(
            ErrorCode.VERIFICATION_FAILED, detail=str(error)
        ) from error
    except InvalidBookError as error:
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION, detail=str(error)
        ) from error
