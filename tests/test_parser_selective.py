"""Selective layout extraction: page classification and page-number safety.

The expensive property — that selective extraction finds the same tables and
images as a whole-document layout parse — is verified against real books by
`scripts/compare_extraction.py`, because it needs minutes of model inference.
These tests cover the parts that must hold for every document.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from parsing.parser import (
    _restore_page_numbers,
    _subset,
    extract_selective,
    pages_needing_layout,
)
from tests.pdf_fixtures import BODY_TEXT, structured_pdf


class _Metadata:
    def __init__(self, page_number):
        self.page_number = page_number


class _Element:
    """Enough of an Unstructured element for page remapping."""

    def __init__(self, page_number, text=""):
        self.metadata = _Metadata(page_number)
        self.text = text


class PageClassificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="selective-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in sorted(self.directory.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.directory.rmdir()

    def _document(self, builder) -> Path:
        path = self.directory / "doc.pdf"
        document = fitz.open()
        builder(document)
        document.save(str(path))
        document.close()
        return path

    def test_text_only_pages_do_not_need_the_layout_model(self):
        def build(document):
            for _ in range(3):
                document.new_page().insert_text((72, 96), BODY_TEXT, fontsize=11)

        self.assertEqual(pages_needing_layout(self._document(build)), [])

    def test_a_page_with_an_embedded_image_needs_the_layout_model(self):
        def build(document):
            document.new_page().insert_text((72, 96), BODY_TEXT, fontsize=11)
            page = document.new_page()
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 12))
            page.insert_image(fitz.Rect(72, 72, 140, 140), pixmap=pixmap)
            document.new_page().insert_text((72, 96), BODY_TEXT, fontsize=11)

        self.assertEqual(pages_needing_layout(self._document(build)), [1])

    def test_an_unreadable_page_is_included_rather_than_skipped(self):
        """Missing a table loses content; re-parsing a page only costs time."""

        source = structured_pdf(self.directory / "book.pdf", page_count=6)
        with patch("fitz.Page.get_images", side_effect=RuntimeError("bad page")):
            self.assertEqual(pages_needing_layout(source), [0, 1, 2, 3, 4, 5])


class PageNumberTests(unittest.TestCase):
    def test_subset_pages_are_mapped_back_to_the_original_document(self):
        # A subset of original pages 5, 9 and 40 reports itself as 1, 2, 3.
        restored = _restore_page_numbers(
            [_Element(1), _Element(2), _Element(3)], [4, 8, 39]
        )

        self.assertEqual([e.metadata.page_number for e in restored], [5, 9, 40])

    def test_an_out_of_range_page_fails_loudly(self):
        # Silently keeping a wrong page number would corrupt every citation
        # built from the affected block.
        for number in (None, 0, 4):
            with self.subTest(page_number=number):
                with self.assertRaises(ValueError):
                    _restore_page_numbers([_Element(number)], [4, 8, 39])


class SubsetTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="subset-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in sorted(self.directory.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.directory.rmdir()

    def test_a_subset_holds_the_requested_pages_in_order(self):
        source = structured_pdf(self.directory / "book.pdf", page_count=6)

        subset = _subset(source, [4, 1], self.directory / "subset.pdf")

        with fitz.open(subset) as document:
            self.assertEqual(document.page_count, 2)
            # Page 5 of the original, then page 2.
            self.assertIn("Page 5", document[0].get_text())
            self.assertIn("Page 2", document[1].get_text())

    def test_every_page_is_extracted_exactly_once(self):
        source = structured_pdf(self.directory / "book.pdf", page_count=6)
        seen: list[int] = []

        def fake_partition(path, strategy):
            with fitz.open(path) as document:
                count = document.page_count
            return [_Element(number + 1) for number in range(count)]

        with (
            patch("parsing.parser._partition", side_effect=fake_partition),
            patch("parsing.parser.pages_needing_layout", return_value=[1, 3]),
        ):
            elements = extract_selective(source)

        seen = [element.metadata.page_number for element in elements]
        self.assertEqual(seen, [1, 2, 3, 4, 5, 6])

    def test_a_document_that_is_entirely_rich_skips_the_split(self):
        source = structured_pdf(self.directory / "book.pdf", page_count=6)

        with (
            patch("parsing.parser._partition", return_value=[]) as partition,
            patch("parsing.parser.pages_needing_layout", return_value=list(range(6))),
        ):
            extract_selective(source)

        # Parsed once, directly, with no subset copies.
        partition.assert_called_once()
        self.assertEqual(partition.call_args.args[1], "hi_res")


if __name__ == "__main__":
    unittest.main()
