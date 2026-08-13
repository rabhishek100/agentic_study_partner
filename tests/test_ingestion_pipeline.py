"""The ingestion pipeline against local Supabase Storage and Postgres.

One test runs the real layout parser end to end because that is the only way
to prove the whole path works. The rest stub the parser so failure handling,
cancellation, and crash recovery stay fast to exercise.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv

from ingestion.config import IngestionLimits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.jobs import (
    claim_next_job,
    confirm_outline_review,
    create_job,
    get_job,
    list_events,
    mark_upload_complete,
    outline_review,
    request_cancellation,
)
from ingestion.pipeline import (
    CancellationRequested,
    PipelineDependencies,
    _book_title,
    _paper_outline,
    evaluate_extraction,
    run_job,
)
from ingestion.preflight import PreflightReport, preflight
from ingestion.states import Status
from ingestion.storage_objects import delete_object, storage_client
from parsing.models import ParsedBook, Section, TextBlock
from storage.database import DEFAULT_EMBEDDING_MODEL, connection, resolve_database_url
from storage.postgres import list_books, restore_book
from tests.pdf_fixtures import (
    encrypted_pdf,
    pdf_with_visual_headings,
    scanned_pdf,
    structured_pdf,
)
from tests.postgres import require_empty_ingestion_queue

load_dotenv()

LIMITS = IngestionLimits(max_pages=400, lease_seconds=300)

STORAGE_CONFIGURED = bool(
    os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

FIXTURE_TOC = [
    (1, "Chapter 1. Overview", 1),
    (2, "Why monitoring matters", 2),
    (1, "Chapter 2. Data Distribution Shifts", 4),
    (2, "Detecting skew", 5),
]

BODY = (
    "Training-serving skew appears when production inputs drift away from the "
    "training distribution, which is why monitoring input statistics matters."
)


class StubOcrProvider:
    """A local stand-in for the hosted transcription model.

    It emits the heading markup a real transcription carries, so the stage's
    proposal is exercised rather than bypassed.
    """

    name = "stub"
    model_id = "test/vision-1"
    prompt_hash = "stub-prompt"

    # A page of real prose, so the extraction-quality gate sees a plausible
    # book rather than a stub too thin to be worth importing.
    BODY = (
        "A proximity service discovers nearby places such as restaurants and "
        "theaters, and powers features like finding the best restaurants near "
        "a location. The design begins by narrowing scope: whether the user "
        "may specify a search radius, and whether the system expands that "
        "radius when too few businesses fall inside it."
    )

    def transcribe(self, image: bytes, mime_type: str, page: int):
        from ingestion.ocr import PageTranscription

        del image, mime_type
        return PageTranscription(
            page=page,
            text=f"# Chapter {page}\n\n{self.BODY}\n\n"
            f"<!-- footer: | {page} -->",
            provider=self.name,
            model_id=self.model_id,
            render_dpi=72,
            prompt_hash=self.prompt_hash,
            input_tokens=100,
            output_tokens=40,
            cost_usd=0.001,
        )


class DeterministicEmbedder:
    """A local stand-in for the hosted embedder.

    Its model name comes from the same configuration the readiness check reads,
    so the provenance comparison is exercised rather than bypassed.
    """

    def __init__(self) -> None:
        self.model_name = (
            os.getenv("OPENROUTER_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
        )
        self.model_revision = "test"
        self.device = "cpu"
        self.dimension = 3072
        self.max_sequence_length = 8192

    def _vector(self, text: str) -> list[float]:
        seed = sum(text.encode()) or 1
        return [((seed * (index + 1)) % 97) / 97.0 for index in range(self.dimension)]

    def embed_documents(self, texts) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def stub_parsed_book(
    source: str = "original.pdf",
    *,
    toc: list[tuple[int, str, int]] | None = None,
    page_count: int = 6,
) -> ParsedBook:
    """A ParsedBook consistent with the generated structured fixture."""

    selected_toc = toc or FIXTURE_TOC
    ranges = [
        (entry[2], selected_toc[index + 1][2] - 1)
        if index + 1 < len(selected_toc)
        else (entry[2], page_count)
        for index, entry in enumerate(selected_toc)
    ]
    paths: list[list[str]] = []
    current_path: list[str] = []
    for level, title, _page in selected_toc:
        current_path[level - 1 :] = [title]
        paths.append(list(current_path))
    sections = [
        Section(
            path=path,
            level=len(path),
            start_page=start,
            end_page=end,
            texts=[
                TextBlock(text=f"{path[-1]}. {BODY}", category="NarrativeText", page=start)
            ],
        )
        for path, (start, end) in zip(paths, ranges, strict=True)
    ]
    return ParsedBook(source=source, toc=selected_toc, sections=sections)


class PaperOutlineTests(unittest.TestCase):
    def test_a_paper_without_a_trustworthy_outline_uses_one_document_scope(self):
        with tempfile.TemporaryDirectory(prefix="paper-outline-test-") as directory:
            source = pdf_with_visual_headings(Path(directory) / "paper.pdf")
            report = preflight(source, limits=LIMITS)

        outline, strategy = _paper_outline(report)

        self.assertEqual(outline, [(1, "Full paper", 1)])
        self.assertEqual(strategy, "whole_document")

    def test_a_paper_keeps_a_trustworthy_native_section_outline(self):
        with tempfile.TemporaryDirectory(prefix="paper-outline-test-") as directory:
            source = structured_pdf(Path(directory) / "paper.pdf")
            report = preflight(source, limits=LIMITS)

        outline, strategy = _paper_outline(report)

        self.assertEqual(outline, report.normalized_toc)
        self.assertEqual(strategy, "native_sections")


@unittest.skipUnless(
    STORAGE_CONFIGURED,
    "requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for local Storage",
)
class PipelineFixture(unittest.TestCase):
    """One owner, one uploaded source, one claimed job."""

    def setUp(self):
        require_empty_ingestion_queue(self)
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        self.directory = Path(tempfile.mkdtemp(prefix="pipeline-test-"))
        self.uploaded: list[tuple[str, str]] = []
        self.dependencies = PipelineDependencies(
            embedder_factory=DeterministicEmbedder
        )

        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@test.local"),
            )
        self.addCleanup(self._discard)

    def _discard(self):
        for bucket, path in self.uploaded:
            try:
                delete_object(bucket, path)
            except IngestionError:
                pass
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))
        for path in sorted(self.directory.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.directory.rmdir()

    def upload(self, source: Path, storage_path: str) -> None:
        with storage_client() as client:
            response = client.post(
                f"/object/book-sources/{storage_path}",
                content=source.read_bytes(),
                headers={"Content-Type": "application/pdf"},
            )
        self.assertIn(response.status_code, (200, 201), response.text)
        self.uploaded.append(("book-sources", storage_path))

    def queued_job(self, source: Path, *, document_type: str = "book"):
        with connection(self.database_url) as database:
            job, _ = create_job(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                original_filename=source.name,
                content_type="application/pdf",
                content_length=source.stat().st_size,
                document_type=document_type,
                limits=LIMITS,
            )
        self.upload(source, job.storage_path)
        with connection(self.database_url) as database:
            return mark_upload_complete(
                database,
                owner_id=self.owner,
                job_id=job.id,
                verified_size_bytes=source.stat().st_size,
                limits=LIMITS,
            )

    def claim(self, source: Path, *, document_type: str = "book"):
        self.queued_job(source, document_type=document_type)
        with connection(self.database_url) as database:
            return claim_next_job(database, worker_id="test-worker", limits=LIMITS)

    def run_claimed(self, job, **kwargs):
        return run_job(
            job,
            limits=LIMITS,
            work_dir=self.directory / str(job.id),
            database_url=self.database_url,
            dependencies=self.dependencies,
            **kwargs,
        )

    def job_now(self, job_id):
        with connection(self.database_url) as database:
            return get_job(database, owner_id=self.owner, job_id=job_id)


class EndToEndTests(PipelineFixture):
    def test_a_structured_pdf_becomes_a_verified_ready_book(self):
        """The whole path with the real parser: upload to selectable book."""

        source = structured_pdf(self.directory / "book.pdf")
        job = self.claim(source)

        outcome = self.run_claimed(job)

        self.assertFalse(outcome.duplicate)
        self.assertEqual(outcome.job.status, Status.READY)
        self.assertIsNotNone(outcome.book_id)

        with connection(self.database_url) as database:
            books = list_books(database, owner_id=self.owner)
            restored = restore_book(database, outcome.book_id, owner_id=self.owner)
            chunks = database.execute(
                "select count(*) as total from chunks where source_book_id = %s",
                (outcome.book_id,),
            ).fetchone()["total"]
            embeddings = database.execute(
                """
                select count(*) as total from chunk_embeddings
                where source_book_id = %s
                """,
                (outcome.book_id,),
            ).fetchone()["total"]
            stored = database.execute(
                """
                select status, ready_at, file_hash, source_storage_path,
                       ingestion_job_id, page_count
                from books where id = %s
                """,
                (outcome.book_id,),
            ).fetchone()

        self.assertEqual([book["id"] for book in books], [outcome.book_id])
        self.assertEqual(stored["status"], "ready")
        self.assertIsNotNone(stored["ready_at"])
        self.assertEqual(stored["ingestion_job_id"], job.id)
        self.assertEqual(stored["source_storage_path"], job.storage_path)
        self.assertEqual(stored["page_count"], 6)
        self.assertEqual(restored.toc, FIXTURE_TOC)
        self.assertGreater(chunks, 0)
        self.assertEqual(chunks, embeddings)

        finished = self.job_now(job.id)
        self.assertEqual(finished.book_id, outcome.book_id)
        self.assertEqual(finished.page_count, 6)
        # A finished job reports finished progress, not a reset counter.
        self.assertEqual(finished.progress_percent, 100.0)
        self.assertEqual(finished.progress_completed, chunks)
        self.assertEqual(finished.document_class, "structured_digital")
        self.assertRegex(finished.file_hash, r"^[0-9a-f]{64}$")
        self.assertIn("preflight", finished.provenance)
        self.assertIn("parser", finished.provenance)
        self.assertIn("embedding", finished.provenance)

        with connection(self.database_url) as database:
            events = [
                event["event_type"]
                for event in list_events(
                    database, owner_id=self.owner, job_id=job.id
                )
            ]
        self.assertEqual(events[:3], ["created", "queued", "claimed"])
        self.assertEqual(events[-2:], ["ready", "verified"])

    def test_a_reviewed_visual_outline_resumes_the_same_job(self):
        """A deterministic proposal is inert until confirmed, then exact."""

        source = pdf_with_visual_headings(self.directory / "visual.pdf")
        first_claim = self.claim(source)

        paused = self.run_claimed(first_claim)

        self.assertIsNone(paused.book_id)
        self.assertEqual(paused.job.status, Status.NEEDS_TOC_REVIEW)
        review = outline_review(paused.job)
        proposed_toc = [
            (entry["level"], entry["title"], entry["page"])
            for entry in review["entries"]
        ]
        self.assertEqual(
            proposed_toc,
            [
                (1, "1 Introduction", 1),
                (2, "1.1 Why Parallelism Matters", 1),
                (1, "2 Memory Systems", 3),
                (2, "2.1 Locality", 3),
            ],
        )
        with connection(self.database_url) as database:
            self.assertEqual(
                list_books(database, owner_id=self.owner, ready_only=False),
                [],
            )
            confirm_outline_review(
                database,
                owner_id=self.owner,
                job_id=first_claim.id,
                toc=proposed_toc,
            )
            second_claim = claim_next_job(
                database,
                worker_id="test-worker",
                limits=LIMITS,
            )

        self.assertEqual(second_claim.id, first_claim.id)
        outcome = self.run_claimed(second_claim)

        self.assertEqual(outcome.job.status, Status.READY)
        with connection(self.database_url) as database:
            restored = restore_book(
                database,
                outcome.book_id,
                owner_id=self.owner,
            )
            stored = database.execute(
                "select metadata_json from books where id = %s",
                (outcome.book_id,),
            ).fetchone()
        self.assertEqual(restored.toc, proposed_toc)
        self.assertEqual(
            stored["metadata_json"]["outline_review"]["state"],
            "confirmed",
        )


class StubbedParserTests(PipelineFixture):
    """Failure handling and recovery, without paying for a real parse."""

    def setUp(self):
        super().setUp()
        # Patched at its source: the pipeline imports the parser lazily, so
        # there is deliberately no module-level name to replace.
        self.parser = patch(
            "parsing.parser.parse_book",
            side_effect=self._stub_parse,
        )
        self.parser.start()
        self.addCleanup(self.parser.stop)

    @staticmethod
    def _stub_parse(source, *args, **kwargs):
        del args
        import fitz

        with fitz.open(source) as document:
            page_count = document.page_count
        return stub_parsed_book(
            str(source),
            toc=kwargs.get("toc_override"),
            page_count=page_count,
        )

    def test_a_paper_without_an_outline_ingests_as_one_whole_paper(self):
        source = pdf_with_visual_headings(self.directory / "paper.pdf")
        job = self.claim(source, document_type="paper")

        outcome = self.run_claimed(job)

        self.assertEqual(outcome.job.status, Status.READY)
        with connection(self.database_url) as database:
            restored = restore_book(
                database,
                outcome.book_id,
                owner_id=self.owner,
            )
            stored = database.execute(
                "select document_type, metadata_json from books where id = %s",
                (outcome.book_id,),
            ).fetchone()
            node_types = database.execute(
                "select node_type from nodes where book_id = %s order by id",
                (outcome.book_id,),
            ).fetchall()

        self.assertEqual(restored.toc, [(1, "Full paper", 1)])
        self.assertEqual(stored["document_type"], "paper")
        self.assertEqual(
            stored["metadata_json"]["paper_outline"]["strategy"],
            "whole_document",
        )
        self.assertEqual([row["node_type"] for row in node_types], ["section"])

    def test_an_encrypted_pdf_fails_without_a_retry(self):
        job = self.claim(encrypted_pdf(self.directory / "locked.pdf"))

        with self.assertRaises(IngestionError) as caught:
            self.run_claimed(job)

        self.assertEqual(caught.exception.code, ErrorCode.ENCRYPTED_PDF)
        self.assertFalse(caught.exception.retryable)

    def test_a_scanned_pdf_is_transcribed_and_then_waits_for_review(self):
        """A scan reaches a hierarchy through a person, never on its own.

        Transcription answers the text question; it is not allowed to answer
        the structure question, because a wrong chapter boundary produces a
        confidently wrong citation that nothing downstream would catch.
        """

        job = self.claim(scanned_pdf(self.directory / "scan.pdf"))
        self.dependencies = PipelineDependencies(
            embedder_factory=DeterministicEmbedder,
            ocr_factory=StubOcrProvider,
            ocr_reference_factory=lambda: None,
        )

        outcome = self.run_claimed(job)

        self.assertIsNone(outcome.book_id)
        paused = self.job_now(job.id)
        self.assertEqual(paused.status, Status.NEEDS_TOC_REVIEW)
        self.assertEqual(paused.document_class, "scanned")
        # Every page was read and committed before the pause.
        ocr = paused.provenance.get("ocr", {})
        self.assertEqual(ocr["pages"], paused.page_count)
        self.assertEqual(ocr["model_id"], StubOcrProvider.model_id)
        # The proposal is evidence a reviewer must confirm, not a hierarchy.
        review = paused.provenance["outline_review"]
        self.assertEqual(review["state"], "pending")
        self.assertEqual(review["outline_source"], "transcribed_headings")
        self.assertTrue(review["entries"])

    def test_a_transcribed_scan_becomes_a_ready_book_after_review(self):
        """The whole scanned path: transcribe, review, build, ingest.

        The second pass must not run the PDF parser. On a pure scan it would
        read the same pixels with a weaker engine and find nothing at all, so
        the book is built from the transcription already stored.
        """

        self.dependencies = PipelineDependencies(
            embedder_factory=DeterministicEmbedder,
            ocr_factory=StubOcrProvider,
            ocr_reference_factory=lambda: None,
        )
        first_claim = self.claim(scanned_pdf(self.directory / "scan.pdf"))

        paused = self.run_claimed(first_claim)
        self.assertEqual(paused.job.status, Status.NEEDS_TOC_REVIEW)

        review = outline_review(paused.job)
        confirmed = [
            (entry["level"], entry["title"], entry["page"])
            for entry in review["entries"]
        ]
        with connection(self.database_url) as database:
            confirm_outline_review(
                database,
                owner_id=self.owner,
                job_id=first_claim.id,
                toc=confirmed,
            )
            second_claim = claim_next_job(
                database, worker_id="test-worker", limits=LIMITS
            )

        outcome = self.run_claimed(second_claim)

        self.assertEqual(outcome.job.status, Status.READY)
        self.assertIsNotNone(outcome.book_id)
        # Built from the transcription, and recorded as such: a reader asking
        # where this text came from gets the transcription, not a parse that
        # never happened.
        provenance = outcome.job.provenance
        self.assertTrue(provenance["parser"]["version"].startswith("transcript-"))
        self.assertIn("printed_numbering", provenance)
        with connection(self.database_url) as database:
            books = list_books(database, owner_id=self.owner)
        self.assertEqual(len(books), 1)

    def test_a_local_source_runs_without_touching_storage(self):
        """The route for books the 50 MB upload ceiling cannot carry.

        Only acquisition changes. The book still transcribes, still stops at
        outline review, and still needs a human before it is answerable, so
        this is a way around the upload limit and not around the review gate.
        """

        from ingestion.local_source import inspect_local_source, queue_local_source

        self.dependencies = PipelineDependencies(
            embedder_factory=DeterministicEmbedder,
            ocr_factory=StubOcrProvider,
            ocr_reference_factory=lambda: None,
        )
        source = inspect_local_source(scanned_pdf(self.directory / "local.pdf"))
        with connection(self.database_url) as database:
            queued = queue_local_source(
                database, owner_id=self.owner, source=source, limits=LIMITS
            )
            claimed = claim_next_job(
                database,
                worker_id="test-worker",
                limits=LIMITS,
                include_local=True,
            )

        self.assertEqual(claimed.id, queued.id)
        # Nothing was ever uploaded, so a Storage-backed run would fail here.
        self.assertNotIn(claimed.storage_path, self.uploaded)

        outcome = self.run_claimed(claimed, local_source=source.path)

        self.assertEqual(outcome.job.status, Status.NEEDS_TOC_REVIEW)
        self.assertEqual(outcome.job.file_hash, source.sha256)

    def test_a_local_source_that_changed_is_refused(self):
        """The hash is what ties a confirmed outline to the file it came from."""

        from ingestion.local_source import inspect_local_source, queue_local_source

        source = inspect_local_source(scanned_pdf(self.directory / "swap.pdf"))
        with connection(self.database_url) as database:
            queue_local_source(
                database, owner_id=self.owner, source=source, limits=LIMITS
            )
            claimed = claim_next_job(
                database,
                worker_id="test-worker",
                limits=LIMITS,
                include_local=True,
            )

        replacement = structured_pdf(self.directory / "other.pdf")

        with self.assertRaises(IngestionError) as caught:
            self.run_claimed(claimed, local_source=replacement)

        self.assertEqual(caught.exception.code, ErrorCode.SOURCE_CHANGED)

    def test_a_source_deleted_after_queueing_fails_the_job(self):
        job = self.claim(structured_pdf(self.directory / "book.pdf"))
        delete_object(job.storage_bucket, job.storage_path)
        self.uploaded.clear()

        with self.assertRaises(IngestionError) as caught:
            self.run_claimed(job)

        self.assertEqual(caught.exception.code, ErrorCode.SOURCE_MISSING)

    def test_a_second_upload_of_the_same_book_returns_the_first(self):
        source = structured_pdf(self.directory / "book.pdf")
        first = self.claim(source)
        original = self.run_claimed(first)

        second = self.claim(source)
        outcome = self.run_claimed(second)

        self.assertTrue(outcome.duplicate)
        self.assertEqual(outcome.book_id, original.book_id)
        self.assertEqual(outcome.job.status, Status.READY)

        with connection(self.database_url) as database:
            books = list_books(database, owner_id=self.owner, ready_only=False)
        # The existing book is returned, never replaced or duplicated.
        self.assertEqual(len(books), 1)

    def test_cancellation_stops_before_canonical_content_is_written(self):
        job = self.claim(structured_pdf(self.directory / "book.pdf"))
        with connection(self.database_url) as database:
            request_cancellation(database, owner_id=self.owner, job_id=job.id)

        with self.assertRaises(CancellationRequested):
            self.run_claimed(job)

        finished = self.job_now(job.id)
        self.assertEqual(finished.status, Status.CANCELLED)
        self.assertIsNone(finished.book_id)
        with connection(self.database_url) as database:
            self.assertEqual(
                list_books(database, owner_id=self.owner, ready_only=False), []
            )

    def test_a_crash_after_canonical_import_reuses_the_committed_book(self):
        job = self.claim(structured_pdf(self.directory / "book.pdf"))
        first = self.run_claimed(job)

        # Rewind the job to just after canonical persistence, as a crash
        # between the import and the chunk build would leave it.
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'persisting', stage = 'persist_canonical',
                    completed_at = null, book_id = null
                where id = %s
                """,
                (job.id,),
            )
            database.execute(
                "update books set status = 'processing', ready_at = null where id = %s",
                (first.book_id,),
            )
            resumed = get_job(database, owner_id=self.owner, job_id=job.id)

        outcome = self.run_claimed(resumed)

        self.assertEqual(outcome.book_id, first.book_id)
        with connection(self.database_url) as database:
            books = list_books(database, owner_id=self.owner, ready_only=False)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["status"], "ready")

    def test_a_crash_during_embedding_rebuilds_derived_data_only(self):
        job = self.claim(structured_pdf(self.directory / "book.pdf"))
        first = self.run_claimed(job)

        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'embedding', stage = 'build_embeddings',
                    completed_at = null
                where id = %s
                """,
                (job.id,),
            )
            database.execute(
                "update books set status = 'processing', ready_at = null where id = %s",
                (first.book_id,),
            )
            # Derived rows from the dead attempt are what gets rebuilt.
            database.execute(
                "delete from chunk_embeddings where source_book_id = %s",
                (first.book_id,),
            )
            resumed = get_job(database, owner_id=self.owner, job_id=job.id)

        outcome = self.run_claimed(resumed)

        self.assertEqual(outcome.book_id, first.book_id)
        self.assertEqual(outcome.job.status, Status.READY)
        with connection(self.database_url) as database:
            counts = database.execute(
                """
                select
                    (select count(*) from chunks where source_book_id = %s) as chunks,
                    (select count(*) from chunk_embeddings
                     where source_book_id = %s) as embeddings,
                    (select count(*) from books where owner_id = %s) as books
                """,
                (first.book_id, first.book_id, self.owner),
            ).fetchone()

        self.assertEqual(counts["books"], 1)
        self.assertGreater(counts["chunks"], 0)
        self.assertEqual(counts["chunks"], counts["embeddings"])

    def test_a_book_is_not_selectable_before_verification(self):
        job = self.claim(structured_pdf(self.directory / "book.pdf"))

        with patch(
            "ingestion.pipeline.rebuild_vector_index",
            side_effect=IngestionError(ErrorCode.PROVIDER_UNAVAILABLE),
        ):
            with self.assertRaises(IngestionError):
                self.run_claimed(job)

        with connection(self.database_url) as database:
            visible = list_books(database, owner_id=self.owner)
            everything = list_books(database, owner_id=self.owner, ready_only=False)

        self.assertEqual(visible, [])
        self.assertEqual(len(everything), 1)
        self.assertEqual(everything[0]["status"], "processing")

    def test_verification_refuses_to_publish_an_unembedded_book(self):
        job = self.claim(structured_pdf(self.directory / "book.pdf"))

        class SilentEmbedder(DeterministicEmbedder):
            """Reports a different model, so no embedding is compatible."""

            def __init__(self) -> None:
                super().__init__()
                self.model_name = "some/other-model"

        self.dependencies = PipelineDependencies(embedder_factory=SilentEmbedder)

        with self.assertRaises(IngestionError) as caught:
            self.run_claimed(job)

        self.assertEqual(caught.exception.code, ErrorCode.VERIFICATION_FAILED)
        with connection(self.database_url) as database:
            self.assertEqual(list_books(database, owner_id=self.owner), [])


class ExtractionQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="quality-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in self.directory.glob("*"):
            path.unlink()
        self.directory.rmdir()

    def report(self) -> PreflightReport:
        return preflight(
            structured_pdf(self.directory / "book.pdf"), limits=LIMITS
        )

    def test_a_healthy_extraction_reports_its_metrics(self):
        metrics = evaluate_extraction(stub_parsed_book(), self.report())

        self.assertEqual(metrics["sections"], 4)
        self.assertEqual(metrics["sections_with_text"], 4)
        self.assertGreater(metrics["total_characters"], 200)

    def test_a_section_count_mismatch_is_a_contract_violation(self):
        book = stub_parsed_book()
        truncated = ParsedBook(
            source=book.source, toc=book.toc, sections=book.sections[:2]
        )

        with self.assertRaises(IngestionError) as caught:
            evaluate_extraction(truncated, self.report())

        self.assertEqual(
            caught.exception.code, ErrorCode.EXTRACTION_CONTRACT_VIOLATION
        )

    def test_a_different_parser_outline_is_a_contract_violation(self):
        book = stub_parsed_book()
        changed = ParsedBook(
            source=book.source,
            toc=[
                (level, "Wrong title" if index == 0 else title, page)
                for index, (level, title, page) in enumerate(book.toc)
            ],
            sections=book.sections,
        )

        with self.assertRaises(IngestionError) as caught:
            evaluate_extraction(changed, self.report())

        self.assertEqual(
            caught.exception.code, ErrorCode.EXTRACTION_CONTRACT_VIOLATION
        )

    def test_an_almost_empty_extraction_is_rejected(self):
        book = stub_parsed_book()
        emptied = ParsedBook(
            source=book.source,
            toc=book.toc,
            sections=[
                Section(
                    path=section.path,
                    level=section.level,
                    start_page=section.start_page,
                    end_page=section.end_page,
                    texts=[],
                )
                for section in book.sections
            ],
        )

        with self.assertRaises(IngestionError) as caught:
            evaluate_extraction(emptied, self.report())

        self.assertEqual(
            caught.exception.code, ErrorCode.EXTRACTION_QUALITY_REJECTED
        )


if __name__ == "__main__":
    unittest.main()


class BookTitleTests(unittest.TestCase):
    """Choosing between the embedded title and the uploaded filename.

    A PDF's metadata title is frequently a placeholder its author never
    changed. One deck in this corpus carries "TestDoc", which became the name
    of a 550-slide course in the library.
    """

    def test_a_placeholder_loses_to_the_filename(self) -> None:
        self.assertEqual(
            _book_title("TestDoc", "PythonMastery (1).pdf"), "PythonMastery (1)"
        )
        self.assertEqual(
            _book_title("untitled", "Head First Design Patterns.pdf"),
            "Head First Design Patterns",
        )

    def test_a_real_title_beats_an_abbreviated_filename(self) -> None:
        """Which is the case the metadata is there for."""

        self.assertEqual(
            _book_title("Designing Machine Learning Systems", "dmls.pdf"),
            "Designing Machine Learning Systems",
        )

    def test_a_tool_export_name_is_not_a_title(self) -> None:
        self.assertEqual(
            _book_title("Microsoft Word - ch3.docx", "Chapter Three.pdf"),
            "Chapter Three",
        )

    def test_a_missing_or_tiny_title_falls_back(self) -> None:
        self.assertEqual(_book_title(None, "AI Engineering.pdf"), "AI Engineering")
        self.assertEqual(_book_title("", "AI Engineering.pdf"), "AI Engineering")
        self.assertEqual(_book_title("ab", "AI Engineering.pdf"), "AI Engineering")
