"""Structure read back out of a page transcription."""

import tempfile
import unittest
from base64 import b64decode
from pathlib import Path

import fitz

from parsing.models import NON_CONTENT_CATEGORIES
from retrieval.chunking import searchable_text
from parsing.transcript import (
    build_transcribed_book,
    printed_numbering,
    table_to_text,
)


def _blank_pdf(path: Path, *, page_count: int = 6) -> Path:
    document = fitz.open()
    for number in range(page_count):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 96), f"page {number + 1}", fontsize=11)
    document.save(path)
    document.close()
    return path


class TableTextTests(unittest.TestCase):
    def test_cells_become_a_readable_row(self) -> None:
        html = (
            "<table><tr><td>Google's PaLM</td><td>540B</td></tr>"
            "<tr><td>Meta's Llama 3</td><td>405B</td></tr></table>"
        )
        self.assertEqual(
            table_to_text(html),
            "Google's PaLM | 540B\nMeta's Llama 3 | 405B",
        )

    def test_a_header_row_is_kept(self) -> None:
        html = "<table><thead><tr><th>Model</th><th>Parameters</th></tr></thead></table>"
        self.assertEqual(table_to_text(html), "Model | Parameters")


class BuildTranscribedBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.source = _blank_pdf(Path(self._directory.name) / "scan.pdf")

    def _build(self, toc, pages, **kwargs):
        return build_transcribed_book(
            self.source, toc=toc, pages=pages, page_count=6, **kwargs
        )

    def test_prose_lists_and_headings_get_distinct_categories(self) -> None:
        book = self._build(
            [(1, "Proximity Service", 1)],
            [(1, "# Proximity Service\n\nA paragraph.\n\n- First item\n- Second item")],
        )

        blocks = [(block.category, block.text) for block in book.sections[0].texts]
        self.assertIn(("Title", "Proximity Service"), blocks)
        self.assertIn(("NarrativeText", "A paragraph."), blocks)
        self.assertIn(("ListItem", "- First item"), blocks)
        self.assertIn(("ListItem", "- Second item"), blocks)

    def test_a_table_keeps_its_place_in_reading_order(self) -> None:
        """The placeholder is what preserves where the table sat in the prose."""

        book = self._build(
            [(1, "Chapter", 1)],
            [
                (
                    1,
                    "# Chapter\n\nBefore.\n\n"
                    "<table><tr><td>PaLM</td><td>540B</td></tr></table>\n\nAfter.",
                )
            ],
        )

        section = book.sections[0]
        texts = [block.text for block in section.texts]
        self.assertEqual(texts.index("Before.") + 1, texts.index("[TABLE 0]"))
        self.assertEqual(texts.index("[TABLE 0]") + 1, texts.index("After."))
        self.assertEqual(len(section.tables), 1)
        self.assertIn("<table>", section.tables[0].html)
        self.assertEqual(section.tables[0].text, "PaLM | 540B")

    def test_a_located_figure_is_cropped_from_its_region(self) -> None:
        book = self._build(
            [(1, "Chapter", 1)],
            [
                (
                    2,
                    '# Chapter\n\n<figure data-bbox="169,441,852,706">'
                    "Figure 1.1: Nearby search</figure>",
                )
            ],
        )

        section = book.sections[0]
        self.assertEqual(len(section.images), 1)
        self.assertIn("[IMAGE 0]", [block.text for block in section.texts])
        # The printed caption is kept as searchable text beside the image.
        self.assertIn(
            "Figure 1.1: Nearby search", [block.text for block in section.texts]
        )
        payload = b64decode(section.images[0].base64)
        self.assertGreater(len(payload), 0)
        with fitz.open(stream=payload, filetype="jpeg") as cropped:
            crop = cropped[0].rect
        # Roughly the requested fraction of a 612x792 page, not the whole page.
        self.assertLess(crop.width, 612 * 0.95)
        self.assertLess(crop.height, 792 * 0.95)

    def test_an_unlocated_figure_falls_back_to_the_whole_page(self) -> None:
        book = self._build(
            [(1, "Chapter", 1)],
            [(2, "# Chapter\n\n<figure>Figure 3.2: Elman RNN</figure>")],
        )

        self.assertEqual(len(book.sections[0].images), 1)

    def test_a_degenerate_figure_box_is_dropped_rather_than_stored(self) -> None:
        book = self._build(
            [(1, "Chapter", 1)],
            [(2, '# Chapter\n\n<figure data-bbox="10,10,12,12">rule</figure>')],
        )

        self.assertEqual(book.sections[0].images, [])

    def test_running_margins_are_preserved_but_not_content(self) -> None:
        """Canonical storage is lossless; retrieval still must not index them."""

        book = self._build(
            [(1, "Chapter", 1)],
            [
                (
                    1,
                    "<!-- header: Chapter 9 -->\n# Chapter\n\nBody.\n"
                    "<!-- footer: 288 | Chapter 9. Text-to-Image Generation -->",
                )
            ],
        )

        by_category = {block.category: block.text for block in book.sections[0].texts}
        self.assertEqual(by_category["DetectedHeader"], "Chapter 9")
        self.assertIn("288", by_category["DetectedFooter"])
        self.assertTrue(NON_CONTENT_CATEGORIES.issuperset({"DetectedHeader"}))

    def test_two_sections_sharing_a_page_split_at_the_heading(self) -> None:
        """The transcription marks headings, so the split is a comparison.

        The PDF parser has to locate a heading among positioned elements and
        sometimes cannot; here it is labelled, so sub-page attribution is
        reliable rather than best-effort.
        """

        book = self._build(
            [(1, "Data Preparation", 3), (2, "Image preparation", 3)],
            [
                (
                    3,
                    "# Data Preparation\n\nOur dataset consists of pairs.\n\n"
                    "## Image preparation\n\nWe focus on two main steps.",
                )
            ],
        )

        first, second = book.sections
        self.assertIn(
            "Our dataset consists of pairs.", [b.text for b in first.texts]
        )
        self.assertNotIn(
            "We focus on two main steps.", [b.text for b in first.texts]
        )
        self.assertIn("We focus on two main steps.", [b.text for b in second.texts])

    def test_a_heading_matches_despite_punctuation_and_numbering(self) -> None:
        """An outline entry and its printed heading disagree constantly."""

        book = self._build(
            [(1, "Introduction", 1), (1, "1 Proximity Service", 2)],
            [
                (1, "# Introduction\n\nOpening."),
                (2, "# 1  Proximity Service\n\nIn this chapter."),
            ],
        )

        self.assertIn("In this chapter.", [b.text for b in book.sections[1].texts])

    def test_pages_before_the_first_entry_are_not_attributed(self) -> None:
        """Front matter the outline does not cover belongs to no section."""

        book = self._build(
            [(1, "Chapter One", 4)],
            [(1, "Cover text."), (4, "# Chapter One\n\nBody.")],
        )

        texts = [block.text for block in book.sections[0].texts]
        self.assertNotIn("Cover text.", texts)
        self.assertIn("Body.", texts)

    def test_the_confirmed_outline_is_carried_through_unaltered(self) -> None:
        """The hierarchy is the one thing a human vouched for."""

        toc = [(1, "Chapter One", 1), (2, "A Section", 2)]
        book = self._build(toc, [(1, "# Chapter One\n\nBody.")])

        self.assertEqual(book.toc, toc)
        self.assertEqual(len(book.sections), len(toc))

    def test_a_book_without_an_outline_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._build([], [(1, "# Chapter")])

    def test_a_book_without_transcription_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._build([(1, "Chapter", 1)], [])


class SearchableTextTests(unittest.TestCase):
    """Index what a reader searches for; display what the page prints."""

    def test_latex_markup_is_removed_but_its_words_survive(self) -> None:
        indexable = searchable_text(
            "We discard pairs where the image is smaller than $64 \\times 64$ pixels."
        )

        assert indexable is not None
        self.assertNotIn("\\times", indexable)
        self.assertNotIn("$", indexable)
        self.assertIn("64", indexable)
        self.assertIn("pixels", indexable)

    def test_a_display_equation_contributes_no_command_terms(self) -> None:
        """`frac` and `partial` match nothing anyone ever searches for."""

        indexable = searchable_text(
            "The gradient $$\\frac{\\partial L}{\\partial w}$$ is computed."
        )

        assert indexable is not None
        self.assertNotIn("frac", indexable)
        self.assertNotIn("partial", indexable)
        self.assertIn("gradient", indexable)
        self.assertIn("computed", indexable)

    def test_ordinary_prose_needs_no_second_rendering(self) -> None:
        """Every chunk of every natively digital book takes this path."""

        self.assertIsNone(searchable_text("A paragraph with no mathematics in it."))


class PrintedNumberingTests(unittest.TestCase):
    """A citation names the page a reader sees, so this must be measured."""

    def test_consecutive_pages_anchor_the_mapping(self) -> None:
        numbering = printed_numbering(
            [
                (9, "<!-- footer: | 1 -->"),
                (10, "<!-- footer: | 2 -->"),
                (11, "<!-- footer: | 3 -->"),
            ]
        )

        self.assertEqual(numbering.matched_pages, 3)
        self.assertEqual(numbering.confidence, 1.0)
        self.assertEqual(numbering.printed(11), 3)
        self.assertEqual(numbering.pdf_page(3), 11)
        self.assertFalse(numbering.drifts)

    def test_a_scan_that_skipped_pages_is_mapped_piecewise(self) -> None:
        """Measured on a phone-scanned book: the offset runs 8 at the front to 1
        at the back, because seven printed pages are absent from the scan. A
        single global offset put its last chapter seven pages wrong, and a
        citation seven pages wrong is worse than none, because it looks right.
        """

        numbering = printed_numbering(
            [
                (9, "<!-- footer: | 1 -->"),
                (39, "<!-- footer: Step 4 - Wrap Up | 31 -->"),
                (99, "<!-- footer: 93 | Chapter 4 -->"),
                (249, "<!-- footer: 246 | Chapter 8 -->"),
                (399, "<!-- footer: 398 | Chapter 13 -->"),
            ]
        )

        self.assertTrue(numbering.drifts)
        self.assertEqual(numbering.offset_range, (1, 8))
        # Anchored pages resolve exactly, whichever end of the book they are on.
        self.assertEqual(numbering.printed(399), 398)
        self.assertEqual(numbering.pdf_page(246), 249)

    def test_a_chapter_number_in_the_footer_loses_to_the_page_number(self) -> None:
        """They are told apart by how fast they move, not by position.

        A chapter number advances by one every thirty pages; no plausible rate
        band contains both it and a page number.
        """

        numbering = printed_numbering(
            [
                (309, "<!-- footer: 308 | Chapter 10 -->"),
                (339, "<!-- footer: 338 | Chapter 11 -->"),
                (369, "<!-- footer: 368 | Chapter 12 -->"),
            ]
        )

        self.assertEqual(numbering.printed(309), 308)
        self.assertEqual([anchor.printed for anchor in numbering.arabic],
                         [308, 338, 368])

    def test_printed_numbers_may_run_ahead_of_the_pdf_page(self) -> None:
        """One scan was made from a copy with its front matter removed."""

        numbering = printed_numbering(
            [(1, "<!-- footer: 9 -->"), (2, "<!-- footer: 10 -->")]
        )

        self.assertEqual(numbering.printed(1), 9)
        self.assertEqual(numbering.pdf_page(10), 2)

    def test_roman_front_matter_is_anchored_separately(self) -> None:
        """It restarts at arabic 1, so one sequence holding both is not monotonic."""

        numbering = printed_numbering(
            [
                (5, "<!-- footer: iii -->"),
                (7, "<!-- footer: v -->"),
                (9, "<!-- footer: | 1 -->"),
                (10, "<!-- footer: | 2 -->"),
            ]
        )

        self.assertEqual(len(numbering.roman), 2)
        self.assertEqual(len(numbering.arabic), 2)
        self.assertEqual(numbering.pdf_page(3, roman=True), 5)
        self.assertEqual(numbering.pdf_page(1), 9)

    def test_one_anchor_alone_establishes_nothing(self) -> None:
        """Any integer in any margin produces one; a book cannot rest on it."""

        numbering = printed_numbering([(3, "<!-- footer: 288 | Chapter 9 -->")])

        self.assertEqual(numbering.matched_pages, 0)
        self.assertIsNone(numbering.printed(3))

    def test_a_book_with_no_margins_maps_nothing(self) -> None:
        numbering = printed_numbering([(1, "Body only."), (2, "More body.")])

        self.assertEqual(numbering.sampled_pages, 0)
        self.assertIsNone(numbering.printed(1))
        self.assertIsNone(numbering.pdf_page(1))

    def test_provenance_reports_the_drift(self) -> None:
        numbering = printed_numbering(
            [
                (9, "<!-- footer: | 1 -->"),
                (99, "<!-- footer: 93 -->"),
                (399, "<!-- footer: 398 -->"),
            ]
        )

        provenance = numbering.provenance()
        self.assertTrue(provenance["drifts"])
        self.assertEqual(provenance["offset_range"], [1, 8])
        self.assertEqual(provenance["arabic_anchors"], 3)


if __name__ == "__main__":
    unittest.main()
