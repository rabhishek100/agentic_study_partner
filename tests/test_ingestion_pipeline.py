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
    create_job,
    get_job,
    list_events,
    mark_upload_complete,
    request_cancellation,
)
from ingestion.pipeline import (
    CancellationRequested,
    PipelineDependencies,
    evaluate_extraction,
    run_job,
)
from ingestion.preflight import PreflightReport, preflight
from ingestion.states import Status
from ingestion.storage_objects import delete_object, storage_client
from parsing.models import ParsedBook, Section, TextBlock
from storage.database import DEFAULT_EMBEDDING_MODEL, connection, resolve_database_url
from storage.postgres import list_books, restore_book
from tests.pdf_fixtures import encrypted_pdf, scanned_pdf, structured_pdf
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


def stub_parsed_book(source: str = "original.pdf") -> ParsedBook:
    """A ParsedBook consistent with the generated structured fixture."""

    ranges = [(1, 1), (2, 3), (4, 4), (5, 6)]
    paths = [
        ["Chapter 1. Overview"],
        ["Chapter 1. Overview", "Why monitoring matters"],
        ["Chapter 2. Data Distribution Shifts"],
        ["Chapter 2. Data Distribution Shifts", "Detecting skew"],
    ]
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
    return ParsedBook(source=source, toc=FIXTURE_TOC, sections=sections)


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

    def queued_job(self, source: Path):
        with connection(self.database_url) as database:
            job, _ = create_job(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                original_filename=source.name,
                content_type="application/pdf",
                content_length=source.stat().st_size,
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

    def claim(self, source: Path):
        self.queued_job(source)
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


class StubbedParserTests(PipelineFixture):
    """Failure handling and recovery, without paying for a real parse."""

    def setUp(self):
        super().setUp()
        # Patched at its source: the pipeline imports the parser lazily, so
        # there is deliberately no module-level name to replace.
        self.parser = patch(
            "parsing.parser.parse_book",
            side_effect=lambda *a, **k: stub_parsed_book(),
        )
        self.parser.start()
        self.addCleanup(self.parser.stop)

    def test_an_encrypted_pdf_fails_without_a_retry(self):
        job = self.claim(encrypted_pdf(self.directory / "locked.pdf"))

        with self.assertRaises(IngestionError) as caught:
            self.run_claimed(job)

        self.assertEqual(caught.exception.code, ErrorCode.ENCRYPTED_PDF)
        self.assertFalse(caught.exception.retryable)

    def test_a_scanned_pdf_is_refused_rather_than_guessed_at(self):
        job = self.claim(scanned_pdf(self.directory / "scan.pdf"))

        with self.assertRaises(IngestionError) as caught:
            self.run_claimed(job)

        self.assertEqual(
            caught.exception.code, ErrorCode.UNSUPPORTED_DOCUMENT_CLASS
        )
        # The class it detected is recorded even though ingestion stopped.
        self.assertEqual(self.job_now(job.id).document_class, "scanned")

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
