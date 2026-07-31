"""Ingesting a book the platform's upload path cannot carry."""

import tempfile
import unittest
from pathlib import Path

from ingestion.config import IngestionLimits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.local_source import (
    LOCAL_BUCKET,
    inspect_local_source,
    is_local,
    queue_local_source,
)
from ingestion.states import Status
from storage.database import connection as database_connection
from tests.pdf_fixtures import scanned_pdf, structured_pdf
from tests.postgres import PostgresOwnerMixin


class InspectLocalSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_a_pdf_is_measured_and_hashed(self) -> None:
        source = structured_pdf(self.directory / "book.pdf")

        measured = inspect_local_source(source)

        self.assertEqual(measured.path, source.resolve())
        self.assertEqual(measured.size_bytes, source.stat().st_size)
        self.assertRegex(measured.sha256, r"^[0-9a-f]{64}$")

    def test_the_same_bytes_hash_the_same(self) -> None:
        """The hash is what proves a confirmed outline still matches its file."""

        source = structured_pdf(self.directory / "book.pdf")
        copy = self.directory / "copy.pdf"
        copy.write_bytes(source.read_bytes())

        self.assertEqual(
            inspect_local_source(source).sha256, inspect_local_source(copy).sha256
        )

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(IngestionError) as caught:
            inspect_local_source(self.directory / "absent.pdf")
        self.assertEqual(caught.exception.code, ErrorCode.SOURCE_MISSING)

    def test_an_empty_file_is_refused(self) -> None:
        empty = self.directory / "empty.pdf"
        empty.write_bytes(b"")

        with self.assertRaises(IngestionError) as caught:
            inspect_local_source(empty)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_PDF)

    def test_something_that_is_not_a_pdf_is_refused(self) -> None:
        """Cheaply, before a 60 MB file is hashed and queued."""

        impostor = self.directory / "notes.pdf"
        impostor.write_bytes(b"just some text, not a document at all")

        with self.assertRaises(IngestionError) as caught:
            inspect_local_source(impostor)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_PDF)


class QueueLocalSourceTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.addCleanup(self.tearDownPostgresOwner)
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_a_local_job_is_queued_and_claimable(self) -> None:
        source = inspect_local_source(scanned_pdf(self.directory / "scan.pdf"))

        with database_connection(self.database_url) as connection:
            job = queue_local_source(
                connection, owner_id=self.owner_id, source=source
            )

        self.assertEqual(job.status, Status.QUEUED)
        self.assertEqual(job.file_hash, source.sha256)
        self.assertEqual(job.verified_size_bytes, source.size_bytes)
        self.assertTrue(is_local(job))
        self.assertEqual(job.storage_bucket, LOCAL_BUCKET)

    def test_a_file_over_the_upload_ceiling_is_still_accepted(self) -> None:
        """Carrying what the upload path cannot is the entire point.

        Supabase enforces a project-wide 50 MB limit the free plan cannot
        raise, and two books in this corpus are larger. Shrinking them would
        degrade sources that already carry only 110-160 dpi of real detail.
        """

        source = inspect_local_source(scanned_pdf(self.directory / "big.pdf"))
        tiny_ceiling = IngestionLimits(max_source_bytes=1)

        with database_connection(self.database_url) as connection:
            job = queue_local_source(
                connection,
                owner_id=self.owner_id,
                source=source,
                limits=tiny_ceiling,
            )

        self.assertEqual(job.status, Status.QUEUED)
        self.assertGreater(job.verified_size_bytes, tiny_ceiling.max_source_bytes)

    def test_the_bypass_is_recorded_rather_than_silent(self) -> None:
        """An operator route around a product limit has to be auditable."""

        source = inspect_local_source(scanned_pdf(self.directory / "scan.pdf"))

        with database_connection(self.database_url) as connection:
            job = queue_local_source(
                connection, owner_id=self.owner_id, source=source
            )

        recorded = job.provenance["local_source"]
        self.assertEqual(recorded["origin"], "local_admin_ingest")
        self.assertEqual(recorded["sha256"], source.sha256)
        self.assertEqual(recorded["filename"], "scan.pdf")

    def test_a_storage_backed_job_is_not_local(self) -> None:
        from ingestion.jobs import create_job

        with database_connection(self.database_url) as connection:
            job, _ = create_job(
                connection,
                owner_id=self.owner_id,
                idempotency_key=__import__("uuid").uuid4(),
                original_filename="book.pdf",
                content_type="application/pdf",
                content_length=1024,
            )

        self.assertFalse(is_local(job))


if __name__ == "__main__":
    unittest.main()
