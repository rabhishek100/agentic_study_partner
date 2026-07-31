"""Deterministic outline cleanup, trust assessment, and heading proposals."""

import tempfile
import unittest
from pathlib import Path

import fitz

from ingestion.config import IngestionLimits
from ingestion.outlines import (
    analyze_outline,
    assess_outline,
    normalize_outline,
    normalize_title,
)
from ingestion.preflight import PARSE, REVIEW, inspect_pdf, preflight, require_supported
from tests.pdf_fixtures import (
    ocr_backed_pdf,
    pdf_with_visual_headings,
    structured_pdf,
)


LIMITS = IngestionLimits(max_pages=400)


class OutlineNormalizationTests(unittest.TestCase):
    def test_unicode_whitespace_and_zero_width_characters_are_safe_to_remove(self):
        self.assertEqual(
            normalize_title("  Chapter\u00a01\u200b:  Intro\ufeffduction  "),
            "Chapter 1: Introduction",
        )

    def test_empty_titles_are_dropped_without_changing_structure_or_pages(self):
        normalized = normalize_outline(
            [
                (1, " Chapter 1 ", 3),
                (2, "\u200b \ufeff", 4),
                (2, "  Section\u00a01.1 ", 5),
            ]
        )

        self.assertEqual(
            normalized.as_toc(),
            [(1, "Chapter 1", 3), (2, "Section 1.1", 5)],
        )
        self.assertEqual(len(normalized.dropped_entries), 1)
        self.assertEqual(normalized.dropped_entries[0].source_index, 1)

    def test_invalid_levels_and_destinations_are_never_fabricated(self):
        normalized = normalize_outline([(3, "Orphan", -1)])

        self.assertEqual(normalized.as_toc(), [(3, "Orphan", -1)])


class OutlineAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="outline-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in self.directory.glob("*"):
            path.unlink()
        self.directory.rmdir()

    def path(self, name: str) -> Path:
        return self.directory / name

    def test_a_valid_embedded_outline_is_trusted(self):
        report = inspect_pdf(
            structured_pdf(self.path("book.pdf")),
            limits=LIMITS,
        )

        self.assertEqual(report.decision.action, PARSE)
        self.assertFalse(report.outline.assessment.needs_review)
        self.assertEqual(report.normalized_toc, report.toc)
        self.assertIsNone(report.outline.proposal)

    def test_blank_publisher_entries_are_removed_without_forcing_review(self):
        source = structured_pdf(self.path("blank.pdf"))
        with fitz.open(source) as document:
            document.set_toc(
                [
                    [1, "Chapter 1", 1],
                    [1, " \u200b ", 2],
                    [1, "Chapter 2", 4],
                ]
            )
            document.save(self.path("blank-saved.pdf"))

        report = preflight(self.path("blank-saved.pdf"), limits=LIMITS)

        self.assertEqual(report.decision.action, PARSE)
        self.assertEqual(
            report.normalized_toc,
            [(1, "Chapter 1", 1), (1, "Chapter 2", 4)],
        )
        self.assertEqual(
            len(report.outline.normalization.dropped_entries),
            1,
        )
        require_supported(report)

    def test_invalid_structure_and_incomplete_coverage_require_review(self):
        source = structured_pdf(self.path("source.pdf"), page_count=20)
        with fitz.open(source) as document:
            assessment = assess_outline(
                document,
                normalize_outline(
                    [(1, "Preface.pdf", -1), (2, "Preface", 2)]
                ),
            )

        self.assertTrue(assessment.needs_review)
        self.assertIn("invalid_destinations", assessment.reasons)
        self.assertIn("incomplete_coverage", assessment.reasons)

    def test_ocr_gibberish_is_detected_as_suspicious(self):
        source = structured_pdf(self.path("source.pdf"))
        with fitz.open(source) as document:
            assessment = assess_outline(
                document,
                normalize_outline(
                    [
                        (1, "Chapter 1", 1),
                        (2, "derr3 derr3 dd3 dw dd3 dw", 2),
                        (2, "dd±", 3),
                        (2, "• 260 = 520 • (260iv + b - 500)", 4),
                    ]
                ),
            )

        self.assertIn("suspicious_titles", assessment.reasons)

    def test_same_page_entries_are_measured_but_not_rejected(self):
        source = structured_pdf(self.path("source.pdf"))
        with fitz.open(source) as document:
            assessment = assess_outline(
                document,
                normalize_outline(
                    [
                        (1, "Chapter 1", 1),
                        (2, "Introduction", 1),
                        (2, "First Section", 1),
                        (1, "Chapter 2", 4),
                    ]
                ),
            )

        self.assertEqual(assessment.same_page_entry_count, 2)
        self.assertNotIn("same_page_entries", assessment.reasons)


class OutlineProposalTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="outline-proposal-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in self.directory.glob("*"):
            path.unlink()
        self.directory.rmdir()

    def path(self, name: str) -> Path:
        return self.directory / name

    def test_visual_headings_create_a_review_only_hierarchy(self):
        report = inspect_pdf(
            pdf_with_visual_headings(self.path("headings.pdf")),
            limits=LIMITS,
        )

        self.assertEqual(report.decision.action, REVIEW)
        self.assertFalse(report.supported)
        self.assertEqual(report.decision.outline_source, "deterministic_proposal")
        self.assertIsNotNone(report.outline.proposal)
        self.assertEqual(
            report.outline.proposal.as_toc(),
            [
                (1, "1 Introduction", 1),
                (2, "1.1 Why Parallelism Matters", 1),
                (1, "2 Memory Systems", 3),
                (2, "2.1 Locality", 3),
            ],
        )

    def test_ocr_backed_pages_are_never_treated_as_an_automatic_proposal(self):
        source = ocr_backed_pdf(self.path("ocr.pdf"))
        with fitz.open(source) as document:
            analysis = analyze_outline(
                document,
                [tuple(entry[:3]) for entry in document.get_toc()],
                likely_ocr_backed=True,
            )

        self.assertIsNone(analysis.proposal)


if __name__ == "__main__":
    unittest.main()
