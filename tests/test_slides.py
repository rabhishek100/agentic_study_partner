"""Recovering a slide deck's sections from its own typography."""

import tempfile
import unittest
from pathlib import Path

import fitz

from ingestion.outlines import SLIDE_PROPOSER_VERSION, analyze_outline
from parsing.slides import (
    MINIMUM_SECTIONS,
    profile_slides,
    synthesize_slide_outline,
)


def _deck(
    path: Path,
    *,
    sections: dict[int, tuple[str, int]] | None = None,
    footer: bool = True,
    landscape: bool = True,
) -> Path:
    """A course-style deck: landscape, titled slides, numbered footers.

    ``sections`` maps a section number to its title and slide count.
    """

    sections = sections or {
        1: ("Python Review", 4),
        2: ("Core Topics", 5),
        3: ("Classes and Objects", 4),
    }
    width, height = (792, 612) if landscape else (612, 792)
    document = fitz.open()
    for number, (title, slides) in sections.items():
        for index in range(slides):
            page = document.new_page(width=width, height=height)
            heading = title if index == 0 else f"{title} detail {index}"
            page.insert_text((60, 90), heading, fontsize=44)
            page.insert_text((60, 200), "A bullet of body text on the slide.", fontsize=16)
            if footer:
                page.insert_text(
                    (40, height - 40), f"{number}- Copyright, Someone", fontsize=12
                )
    document.save(path)
    document.close()
    return path


def _book(path: Path, *, pages: int = 8) -> Path:
    """Portrait pages of dense prose: everything a deck is not."""

    body = (
        "Training-serving skew appears when production inputs drift away from "
        "the training distribution, which is why monitoring input statistics "
        "matters a great deal in practice for any deployed model at all. "
    ) * 6
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(60, 60, 552, 730), body, fontsize=11)
    document.save(path)
    document.close()
    return path


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_a_deck_is_recognised(self) -> None:
        with fitz.open(_deck(self.directory / "deck.pdf")) as document:
            profile = profile_slides(document)

        self.assertTrue(profile.is_deck)
        self.assertEqual(profile.landscape_ratio, 1.0)
        self.assertEqual(profile.titled_ratio, 1.0)

    def test_a_prose_book_is_not_a_deck(self) -> None:
        """The costly mistake is treating a book as a deck, not the reverse."""

        with fitz.open(_book(self.directory / "book.pdf")) as document:
            profile = profile_slides(document)

        self.assertFalse(profile.is_deck)

    def test_a_portrait_document_is_not_a_deck(self) -> None:
        with fitz.open(
            _deck(self.directory / "portrait.pdf", landscape=False)
        ) as document:
            self.assertFalse(profile_slides(document).is_deck)

    def test_provenance_records_what_was_measured(self) -> None:
        with fitz.open(_deck(self.directory / "deck.pdf")) as document:
            provenance = profile_slides(document).provenance()

        for key in ("landscape_ratio", "titled_ratio", "median_characters"):
            self.assertIn(key, provenance)


class SynthesizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_one_entry_per_section_titled_by_its_opening_slide(self) -> None:
        with fitz.open(_deck(self.directory / "deck.pdf")) as document:
            outline = synthesize_slide_outline(document)

        self.assertEqual(
            outline,
            [
                (1, "1. Python Review", 1),
                (1, "2. Core Topics", 5),
                (1, "3. Classes and Objects", 10),
            ],
        )

    def test_slides_are_not_listed_individually(self) -> None:
        """550 review rows and a 370-character citation unit help nobody.

        The sections are what the deck is organised by, and the token-based
        chunker already packs several slides into one chunk.
        """

        with fitz.open(_deck(self.directory / "deck.pdf")) as document:
            outline = synthesize_slide_outline(document)
            pages = document.page_count

        assert outline is not None
        self.assertLess(len(outline), pages)

    def test_a_deck_without_footer_numbers_is_refused(self) -> None:
        """A rough deck structure is harder to review than an honest generic one."""

        with fitz.open(_deck(self.directory / "bare.pdf", footer=False)) as document:
            self.assertIsNone(synthesize_slide_outline(document))

    def test_too_few_sections_is_not_a_scheme(self) -> None:
        with fitz.open(
            _deck(
                self.directory / "thin.pdf",
                sections={1: ("Only Section", 4), 2: ("Second", 4)},
            )
        ) as document:
            outline = synthesize_slide_outline(document)

        self.assertIsNone(outline)
        self.assertLess(2, MINIMUM_SECTIONS)

    def test_a_book_yields_no_deck_outline(self) -> None:
        with fitz.open(_book(self.directory / "book.pdf")) as document:
            self.assertIsNone(synthesize_slide_outline(document))

    def test_backwards_section_numbers_are_refused(self) -> None:
        """A deck that renumbers is not sectioned the way this assumes."""

        with fitz.open(
            _deck(
                self.directory / "odd.pdf",
                sections={3: ("Third", 4), 1: ("First", 4), 2: ("Second", 4)},
            )
        ) as document:
            self.assertIsNone(synthesize_slide_outline(document))


class ProposalTests(unittest.TestCase):
    """A deck reaches review through the same gate every other book does."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_a_deck_proposal_replaces_the_span_headings_one(self) -> None:
        with fitz.open(_deck(self.directory / "deck.pdf")) as document:
            analysis = analyze_outline(document, [], likely_ocr_backed=False)

        assert analysis.proposal is not None
        self.assertEqual(analysis.proposal.proposer_version, SLIDE_PROPOSER_VERSION)
        self.assertEqual(len(analysis.proposal.entries), 3)
        self.assertTrue(analysis.proposal.warnings)

    def test_a_book_still_gets_the_span_headings_proposal(self) -> None:
        with fitz.open(_book(self.directory / "book.pdf")) as document:
            analysis = analyze_outline(document, [], likely_ocr_backed=False)

        if analysis.proposal is not None:
            self.assertNotEqual(
                analysis.proposal.proposer_version, SLIDE_PROPOSER_VERSION
            )


if __name__ == "__main__":
    unittest.main()
