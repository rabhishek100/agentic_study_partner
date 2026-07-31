"""Document classification and the cheap rejections that precede parsing."""

import tempfile
import unittest
from pathlib import Path

from ingestion.config import IngestionLimits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.preflight import (
    DIGITAL_WITHOUT_TOC,
    MIXED,
    REVIEW,
    SCANNED,
    STRUCTURED_DIGITAL,
    inspect_pdf,
    preflight,
    require_supported,
    validate_table_of_contents,
)
from tests.pdf_fixtures import (
    corrupt_pdf,
    encrypted_pdf,
    mixed_pdf,
    ocr_backed_pdf,
    outline_pdf,
    pdf_without_outline,
    scanned_pdf,
    structured_pdf,
)


LIMITS = IngestionLimits(max_pages=400)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="preflight-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in self.directory.glob("*"):
            path.unlink()
        self.directory.rmdir()

    def path(self, name: str) -> Path:
        return self.directory / name

    def assert_rejects(self, source: Path, code: ErrorCode, *, limits=LIMITS):
        with self.assertRaises(IngestionError) as caught:
            preflight(source, limits=limits)
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(caught.exception.retryable)
        return caught.exception

    def test_a_structured_book_is_accepted(self):
        report = preflight(structured_pdf(self.path("book.pdf")), limits=LIMITS)

        self.assertEqual(report.document_class, STRUCTURED_DIGITAL)
        self.assertTrue(report.supported)
        self.assertEqual(report.page_count, 6)
        self.assertEqual(len(report.toc), 4)
        self.assertEqual(report.text_coverage, 1.0)
        self.assertFalse(report.profile.likely_ocr_backed)
        require_supported(report)

    def test_the_report_records_reproducible_provenance(self):
        report = preflight(structured_pdf(self.path("book.pdf")), limits=LIMITS)
        provenance = report.provenance()

        self.assertEqual(provenance["classifier_version"], "preflight-v1")
        self.assertEqual(provenance["document_class"], STRUCTURED_DIGITAL)
        self.assertEqual(provenance["toc_entries"], 4)
        self.assertEqual(provenance["page_count"], 6)
        profile = provenance["profile"]
        self.assertEqual(profile["profile_version"], "document-profile-v1")
        self.assertEqual(profile["sampled_pages"], 6)
        self.assertEqual(profile["pages_with_text"], 6)
        self.assertEqual(profile["ocr_overlay_coverage"], 0.0)
        self.assertFalse(profile["likely_ocr_backed"])

    def test_an_ocr_text_layer_is_distinguished_from_native_digital_text(self):
        report = preflight(ocr_backed_pdf(self.path("ocr.pdf")), limits=LIMITS)

        self.assertEqual(report.document_class, STRUCTURED_DIGITAL)
        self.assertFalse(report.supported)
        self.assertEqual(report.decision.action, REVIEW)
        self.assertEqual(report.profile.image_page_coverage, 1.0)
        self.assertEqual(report.profile.full_page_image_coverage, 1.0)
        self.assertEqual(report.profile.ocr_overlay_coverage, 1.0)
        self.assertTrue(report.profile.likely_ocr_backed)
        self.assertGreater(report.profile.estimated_total_image_pixels, 0)
        with self.assertRaises(IngestionError) as caught:
            require_supported(report)
        self.assertEqual(
            caught.exception.code, ErrorCode.UNSUPPORTED_DOCUMENT_CLASS
        )

    def test_an_illustrated_native_pdf_is_not_mistaken_for_an_ocr_scan(self):
        report = preflight(structured_pdf(self.path("book.pdf")), limits=LIMITS)

        self.assertEqual(report.profile.ocr_overlay_coverage, 0.0)
        self.assertFalse(report.profile.likely_ocr_backed)

    def test_a_corrupt_pdf_is_rejected(self):
        self.assert_rejects(corrupt_pdf(self.path("broken.pdf")), ErrorCode.INVALID_PDF)

    def test_an_encrypted_pdf_is_rejected(self):
        error = self.assert_rejects(
            encrypted_pdf(self.path("locked.pdf")), ErrorCode.ENCRYPTED_PDF
        )
        self.assertIn("Password-protected", error.safe_message)

    def test_a_book_over_the_page_limit_is_rejected_before_parsing(self):
        source = structured_pdf(self.path("long.pdf"), page_count=12)

        self.assert_rejects(
            source, ErrorCode.TOO_MANY_PAGES, limits=IngestionLimits(max_pages=10)
        )

    def test_a_scanned_pdf_is_classified_and_refused(self):
        report = preflight(scanned_pdf(self.path("scan.pdf")), limits=LIMITS)

        self.assertEqual(report.document_class, SCANNED)
        self.assertFalse(report.supported)
        self.assertEqual(report.pages_with_text, 0)
        with self.assertRaises(IngestionError) as caught:
            require_supported(report)
        self.assertEqual(
            caught.exception.code, ErrorCode.UNSUPPORTED_DOCUMENT_CLASS
        )

    def test_a_mixed_pdf_is_classified_and_refused(self):
        report = preflight(mixed_pdf(self.path("mixed.pdf")), limits=LIMITS)

        self.assertEqual(report.document_class, MIXED)
        with self.assertRaises(IngestionError):
            require_supported(report)

    def test_a_digital_pdf_without_an_outline_is_refused_with_its_own_reason(self):
        report = preflight(pdf_without_outline(self.path("flat.pdf")), limits=LIMITS)

        self.assertEqual(report.document_class, DIGITAL_WITHOUT_TOC)
        with self.assertRaises(IngestionError) as caught:
            require_supported(report)
        # Not "unsupported document class": the user should be told the actual
        # missing piece, because it is the one they might be able to fix.
        self.assertEqual(
            caught.exception.code, ErrorCode.MISSING_TABLE_OF_CONTENTS
        )
        self.assertIn("table of contents", caught.exception.safe_message)

    def test_content_before_the_first_outline_entry_is_flagged(self):
        source = outline_pdf(
            self.path("late-start.pdf"), [[1, "Chapter 1", 3]], page_count=6
        )

        report = preflight(source, limits=LIMITS)

        self.assertTrue(report.supported)
        self.assertTrue(any("not ingested" in note for note in report.warnings))

    def test_a_malformed_outline_is_rejected(self):
        # PyMuPDF refuses to write a level jump and clamps out-of-range pages,
        # so those defects cannot be expressed in a generated fixture. They are
        # covered directly in OutlineValidationTests, which is where a
        # third-party PDF carrying them would be caught.
        cases = {
            "backwards pages": [[1, "Chapter 1", 4], [1, "Chapter 2", 2]],
            "empty title": [[1, "   ", 1]],
        }
        for name, toc in cases.items():
            with self.subTest(case=name):
                source = outline_pdf(self.path(f"{name}.pdf".replace(" ", "-")), toc)
                report = preflight(source, limits=LIMITS)
                self.assertEqual(report.decision.action, REVIEW)
                with self.assertRaises(IngestionError) as caught:
                    require_supported(report)
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_HIERARCHY)

    def test_a_malformed_outline_can_still_be_profiled_for_repair(self):
        source = outline_pdf(
            self.path("broken-outline.pdf"),
            [[1, "Chapter 1", 1], [1, "   ", 2]],
        )

        report = inspect_pdf(source, limits=LIMITS)

        self.assertEqual(report.document_class, STRUCTURED_DIGITAL)
        self.assertEqual(report.profile.text_coverage, 1.0)
        self.assertEqual(len(report.toc), 2)


class OutlineValidationTests(unittest.TestCase):
    def test_a_well_formed_outline_passes(self):
        validate_table_of_contents(
            [(1, "Chapter 1", 1), (2, "Section", 2), (3, "Subsection", 3)],
            page_count=10,
        )

    def test_descending_levels_may_skip_freely(self):
        # Returning from a deep subsection straight to the next chapter is
        # normal; only descending without a parent is invalid.
        validate_table_of_contents(
            [(1, "Chapter 1", 1), (2, "Section", 2), (3, "Sub", 3), (1, "Ch 2", 4)],
            page_count=10,
        )

    def test_an_orphan_level_is_rejected(self):
        # Level 1 to level 3 leaves the subsection with no parent, so its path
        # could not be reconstructed losslessly by canonical ingestion.
        with self.assertRaises(IngestionError) as caught:
            validate_table_of_contents(
                [(1, "Chapter 1", 1), (3, "Orphan subsection", 2)], page_count=10
            )

        self.assertEqual(caught.exception.code, ErrorCode.INVALID_HIERARCHY)

    def test_a_page_beyond_the_document_is_rejected(self):
        with self.assertRaises(IngestionError) as caught:
            validate_table_of_contents([(1, "Chapter 1", 99)], page_count=10)

        self.assertEqual(caught.exception.code, ErrorCode.INVALID_HIERARCHY)

    def test_an_empty_outline_reports_the_missing_table_of_contents(self):
        with self.assertRaises(IngestionError) as caught:
            validate_table_of_contents([], page_count=10)

        self.assertEqual(
            caught.exception.code, ErrorCode.MISSING_TABLE_OF_CONTENTS
        )


if __name__ == "__main__":
    unittest.main()
