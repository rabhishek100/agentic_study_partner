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
from parsing.version import PARSER_VERSION
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
from .captions import caption_book_figures
from .config import IngestionLimits
from .errors import ErrorCode, IngestionError
from .jobs import (
    IngestionJob,
    advance_stage,
    append_event,
    cancel_running_job,
    complete_job,
    get_job,
    outline_review,
    pause_for_outline_review,
    record_progress,
    set_stage,
)
from .ocr_stage import propose_outline, require_proposable, transcribe_book
from .ocr_store import transcribed_text
from .preflight import (
    OCR,
    REVIEW,
    PreflightReport,
    preflight,
    require_supported,
    validate_table_of_contents,
)
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
    captioner_factory: Callable[[], Any] | None = None
    ocr_factory: Callable[[], Any] | None = None
    ocr_reference_factory: Callable[[], Any] | None = None
    chunking_config: ChunkingConfig = field(default_factory=ChunkingConfig)

    def embedder(self) -> Any:
        if self.embedder_factory is not None:
            return self.embedder_factory()
        from retrieval.vector import build_embedder

        return build_embedder()

    def captioner(self) -> Any:
        if self.captioner_factory is not None:
            return self.captioner_factory()
        from ingestion.captions import OpenRouterCaptioner

        return OpenRouterCaptioner()

    def ocr(self) -> Any:
        if self.ocr_factory is not None:
            return self.ocr_factory()
        from ingestion.ocr import OpenRouterOcrProvider

        return OpenRouterOcrProvider()

    def ocr_reference(self) -> Any:
        """The deterministic engine the fabrication gate compares against.

        Optional by design. Its absence leaves pages unassessable, which is
        recorded as such; it never stops a book, because the gate flags and
        does not block.
        """

        if self.ocr_reference_factory is not None:
            return self.ocr_reference_factory()
        from ingestion.ocr import TesseractOcrProvider

        try:
            return TesseractOcrProvider()
        except ValueError:
            logger.warning("no local OCR engine; pages will be unassessable")
            return None


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
    *,
    approved_toc: list[tuple[int, str, int]] | None = None,
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

    expected_toc = approved_toc or report.normalized_toc
    if book.toc != expected_toc:
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION,
            detail="parser hierarchy differs from the preflight-approved outline",
        )
    if len(book.sections) != len(expected_toc):
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION,
            detail=(
                f"{len(book.sections)} sections for "
                f"{len(expected_toc)} approved outline entries"
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


def _confirmed_outline(job: IngestionJob) -> list[tuple[int, str, int]] | None:
    """Read the exact human-confirmed hierarchy from durable provenance."""

    review = outline_review(job)
    if review is None or review.get("state") != "confirmed":
        return None
    raw_entries = review.get("confirmed_entries")
    if not isinstance(raw_entries, list):
        raise IngestionError(
            ErrorCode.INVALID_HIERARCHY,
            detail="confirmed outline provenance has no entries",
        )
    try:
        return [
            (int(entry["level"]), str(entry["title"]), int(entry["page"]))
            for entry in raw_entries
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise IngestionError(
            ErrorCode.INVALID_HIERARCHY,
            detail="confirmed outline provenance is malformed",
        ) from error


def _validate_stage(
    job: IngestionJob,
    *,
    limits: IngestionLimits,
    work_dir: Path,
    database_url: str | None,
    dependencies: PipelineDependencies,
) -> (
    tuple[
        IngestionJob,
        Path,
        str,
        PreflightReport,
        list[tuple[int, str, int]],
    ]
    | JobOutcome
):
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
    if job.file_hash is not None and download.sha256 != job.file_hash:
        raise IngestionError(
            ErrorCode.SOURCE_CHANGED,
            detail="source bytes changed after preflight or outline review",
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
    confirmed_toc = _confirmed_outline(job)
    if confirmed_toc is not None:
        review = outline_review(job) or {}
        if review.get("source_sha256") != download.sha256:
            raise IngestionError(
                ErrorCode.SOURCE_CHANGED,
                detail="confirmed outline belongs to a different source hash",
            )
        validate_table_of_contents(confirmed_toc, report.page_count)
        approved_toc = confirmed_toc
    elif report.decision.action == OCR:
        return _transcribe_and_pause(
            job,
            source=source,
            report=report,
            limits=limits,
            database_url=database_url,
            dependencies=dependencies,
        )
    else:
        proposal = report.outline.proposal
        if (
            report.decision.action == REVIEW
            and proposal is not None
            and proposal.entries
        ):
            with _database(database_url) as connection:
                paused = pause_for_outline_review(
                    connection,
                    owner_id=job.owner_id,
                    job_id=job.id,
                    proposal=proposal.as_toc(),
                    reasons=report.decision.reasons,
                    outline_source=report.decision.outline_source
                    or "deterministic_proposal",
                    proposer_version=proposal.proposer_version,
                    warnings=proposal.warnings,
                )
            return JobOutcome(job=paused, book_id=None)
        require_supported(report)
        approved_toc = report.normalized_toc
    return job, source, download.sha256, report, approved_toc


def _transcribe_and_pause(
    job: IngestionJob,
    *,
    source: Path,
    report: PreflightReport,
    limits: IngestionLimits,
    database_url: str | None,
    dependencies: PipelineDependencies,
) -> JobOutcome:
    """Transcribe a scanned or OCR-backed source, then hand it to a reviewer.

    The stage produces text and a candidate hierarchy, and stops. Nothing here
    may become canonical without confirmation: a wrong chapter boundary yields
    a confidently wrong citation, and no later stage would catch it.
    """

    owner_id = job.owner_id
    with _database(database_url) as connection:
        job = advance_stage(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            current_status=job.status,
            status=Status.OCR,
            stage=Stage.OCR_PAGES,
        )
        record_progress(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            completed=0,
            total=report.page_count,
            unit="pages",
        )

    def publish(completed: int, total: int) -> None:
        try:
            with _database(database_url) as connection:
                record_progress(
                    connection,
                    owner_id=owner_id,
                    job_id=job.id,
                    completed=completed,
                    total=total,
                    unit="pages",
                )
        except Exception:
            # Progress is a convenience. Losing a tick must not cost a page
            # that has already been paid for.
            logger.warning("could not record transcription progress", exc_info=True)

    def cancelled() -> bool:
        with _database(database_url) as connection:
            current = get_job(connection, owner_id=owner_id, job_id=job.id)
        return current.cancellation_requested

    provider = dependencies.ocr()
    outcome = transcribe_book(
        source,
        owner_id=owner_id,
        job_id=job.id,
        limits=limits,
        provider=provider,
        reference=dependencies.ocr_reference(),
        open_connection=lambda: _database(database_url),
        on_progress=publish,
        should_stop=cancelled,
    )
    logger.info("transcribed job %s: %s", job.id, outcome.provenance())

    job = _check_cancelled(job, database_url=database_url)

    with _database(database_url) as connection:
        job = set_stage(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            current_status=Status.OCR,
            stage=Stage.PROPOSE_TOC,
            provenance={
                "ocr": {
                    **outcome.provenance(),
                    "provider": provider.name,
                    "model_id": provider.model_id,
                    "prompt_hash": provider.prompt_hash,
                    "render_dpi": limits.ocr_render_dpi,
                }
            },
        )
        pages = transcribed_text(connection, owner_id=owner_id, job_id=job.id)

    proposal = propose_outline(pages, page_count=report.page_count)
    entries = require_proposable(
        list(proposal.entries), page_count=report.page_count
    )
    warnings = [*report.warnings, *proposal.warnings]
    if outcome.summary.flagged:
        warnings.append(
            f"{outcome.summary.flagged} pages contain text the reference engine "
            "could not corroborate"
        )

    with _database(database_url) as connection:
        if proposal.provenance_json:
            set_stage(
                connection,
                owner_id=owner_id,
                job_id=job.id,
                current_status=Status.OCR,
                stage=Stage.PROPOSE_TOC,
                provenance=proposal.provenance_json,
            )
        paused = pause_for_outline_review(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            proposal=entries,
            reasons=report.decision.reasons,
            outline_source=proposal.source,
            proposer_version=proposal.proposer_version,
            warnings=warnings,
        )
    return JobOutcome(job=paused, book_id=None)


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
    dependencies: PipelineDependencies,
) -> tuple[IngestionJob, int, dict[str, int | float]] | JobOutcome:
    """Validate, parse, and canonically import the source.

    Returns the job, its book, and the extraction metrics later stages verify
    against, or a finished outcome when the upload turned out to be a
    duplicate of a book the owner already has.
    """

    owner_id = job.owner_id
    validated = _validate_stage(
        job,
        limits=limits,
        work_dir=work_dir,
        database_url=database_url,
        dependencies=dependencies,
    )
    if isinstance(validated, JobOutcome):
        return validated
    job, source, file_hash, report, approved_toc = validated
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

    with _database(database_url) as connection:
        transcription = transcribed_text(
            connection, owner_id=owner_id, job_id=job.id
        )
    if transcription:
        # A transcribed book already carries its text. Running the PDF parser
        # over it would read the same pixels a second time with a weaker
        # engine, and on a pure scan would find nothing at all.
        return _persist_transcribed(
            job,
            source=source,
            file_hash=file_hash,
            report=report,
            approved_toc=approved_toc,
            transcription=transcription,
            database_url=database_url,
        )

    # Deferred: importing the parser pulls in Unstructured and Torch, about
    # half a gigabyte of resident memory that an idle worker should not hold.
    from parsing.parser import parse_book

    def report_batch(completed: int, total: int) -> None:
        """Publish real page progress as each batch of pages finishes."""

        pages_done = min(report.page_count, round(report.page_count * completed / total))
        try:
            with _database(database_url) as progress_connection:
                record_progress(
                    progress_connection,
                    owner_id=owner_id,
                    job_id=job.id,
                    completed=pages_done,
                    total=report.page_count,
                    unit="pages",
                )
        except Exception:
            # Progress is a convenience; losing an update must not fail a
            # parse that is otherwise going fine.
            logger.warning("could not record parse progress", exc_info=True)

    try:
        book = parse_book(
            source,
            force=True,
            book_cache=work_dir / PARSED_BOOK_CACHE,
            elements_cache=work_dir / ELEMENTS_CACHE,
            on_batch=report_batch,
            toc_override=approved_toc,
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

    metrics = evaluate_extraction(book, report, approved_toc=approved_toc)
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


def _persist_transcribed(
    job: IngestionJob,
    *,
    source: Path,
    file_hash: str,
    report: PreflightReport,
    approved_toc: list[tuple[int, str, int]],
    transcription: list[tuple[int, str]],
    database_url: str | None,
) -> tuple[IngestionJob, int, dict[str, int | float]]:
    """Build and import a book from its transcription and confirmed outline.

    The outline is the reviewer's, unaltered. Structure extraction reads the
    transcription markup and never the pixels, so every decision it makes is
    deterministic and reproducible from data already stored.
    """

    from parsing.transcript import build_transcribed_book, printed_numbering

    owner_id = job.owner_id
    try:
        book = build_transcribed_book(
            source,
            toc=approved_toc,
            pages=transcription,
            page_count=report.page_count,
        )
    except ValueError as error:
        raise IngestionError(
            ErrorCode.EXTRACTION_CONTRACT_VIOLATION,
            detail=f"transcription rejected: {error}",
        ) from error

    numbering = printed_numbering(transcription)
    metrics = evaluate_extraction(book, report, approved_toc=approved_toc)
    logger.info(
        "built transcribed book for job %s: %s", job.id, numbering.provenance()
    )

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
            provenance={
                "parser": {
                    "version": f"transcript-{PARSER_VERSION}",
                    **metrics,
                },
                "printed_numbering": numbering.provenance(),
            },
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
            dependencies=dependencies,
        )
        if isinstance(ingested, JobOutcome):
            # Validation resolved the job on its own: a duplicate upload, or a
            # source that transcribed and is now waiting on outline review.
            return ingested
        job, book_id, metrics = ingested

    # Caption figures ---------------------------------------------------
    # Before chunking, because a caption becomes the chunk text that makes a
    # figure findable at all. A captioning failure must not sink a book that
    # is otherwise complete, so the outcome is recorded and the run continues.
    with _database(database_url) as connection:
        job = _enter_stage(
            connection,
            job,
            status=Status.CAPTIONING,
            stage=Stage.CAPTION_FIGURES,
            book_id=book_id,
        )
    try:
        with _database(database_url) as connection:
            captions = caption_book_figures(
                connection,
                book_id,
                owner_id=owner_id,
                captioner=dependencies.captioner(),
                on_progress=lambda done, total: None,
            )
        caption_provenance = {
            "captioned": captions.captioned,
            "reused": captions.reused,
            "skipped_boilerplate": captions.skipped_boilerplate,
            "skipped_small": captions.skipped_small,
            "failed": captions.failed,
        }
    except Exception:
        logger.exception("figure captioning failed; continuing without captions")
        caption_provenance = {"error": "captioning_unavailable"}
    with _database(database_url) as connection:
        record_progress(
            connection,
            owner_id=owner_id,
            job_id=job.id,
            completed=caption_provenance.get("captioned", 0)
            + caption_provenance.get("reused", 0),
            total=caption_provenance.get("captioned", 0)
            + caption_provenance.get("reused", 0),
            unit="figures",
        )
    job = _check_cancelled(job, database_url=database_url)

    # Chunk -------------------------------------------------------------
    with _database(database_url) as connection:
        job = _enter_stage(
            connection,
            job,
            status=Status.CHUNKING,
            stage=Stage.BUILD_CHUNKS,
            book_id=book_id,
            provenance={"captions": caption_provenance},
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
            metadata={
                "pdf": report.metadata,
                "preflight": report.provenance(),
                "outline_review": outline_review(job),
            },
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
