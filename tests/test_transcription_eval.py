"""Scoring a transcription against a reference."""

import unittest

from scripts.evaluate_outlines import _matches
from evals.transcription import (
    FORMULA,
    PATHOLOGICAL,
    PROSE,
    TABLE,
    categorize_page,
    character_error_rate,
    fabricated_spans,
    score_page,
    table_cell_f1,
    word_error_rate,
)


REFERENCE = (
    "A proximity service discovers nearby places such as restaurants and "
    "theaters, and powers features like finding the best restaurants near a "
    "location within a chosen radius."
)


class CategorizeTests(unittest.TestCase):
    def test_a_flagged_page_is_pathological_whatever_else_it_holds(self) -> None:
        """The gate already said this page needs looking at."""

        self.assertEqual(
            categorize_page("<table><tr><td>x</td></tr></table>", flagged=True, unassessable=False),
            PATHOLOGICAL,
        )

    def test_a_table_page_outranks_a_formula_page(self) -> None:
        """Table structure is the harder thing, and plain OCR cannot represent it."""

        text = "<table><tr><td>a</td></tr></table> $x = 1$ $y = 2$"
        self.assertEqual(categorize_page(text, flagged=False, unassessable=False), TABLE)

    def test_two_formulas_make_a_formula_page(self) -> None:
        text = "The gradient $\\frac{dL}{dw}$ and the loss $L = 1$ are related."
        self.assertEqual(
            categorize_page(text, flagged=False, unassessable=False), FORMULA
        )

    def test_ordinary_text_is_prose(self) -> None:
        self.assertEqual(
            categorize_page(REFERENCE, flagged=False, unassessable=False), PROSE
        )


class ErrorRateTests(unittest.TestCase):
    def test_an_identical_reading_scores_zero(self) -> None:
        self.assertEqual(character_error_rate(REFERENCE, REFERENCE), 0.0)
        self.assertEqual(word_error_rate(REFERENCE, REFERENCE), 0.0)

    def test_markup_is_not_counted_against_an_engine(self) -> None:
        """One engine emits HTML and LaTeX; another emits none.

        Comparing markup would measure the prompt rather than the reading.
        """

        marked = f"<!-- header: Chapter 1 -->\n{REFERENCE}\n<figure>A figure</figure>"

        self.assertLess(character_error_rate(marked, REFERENCE), 0.15)

    def test_a_misread_word_costs_more_in_wer_than_cer(self) -> None:
        candidate = REFERENCE.replace("restaurants", "restaurunts")

        self.assertLess(
            character_error_rate(candidate, REFERENCE),
            word_error_rate(candidate, REFERENCE),
        )

    def test_an_empty_reading_scores_one(self) -> None:
        self.assertEqual(character_error_rate("", REFERENCE), 1.0)


class TableCellTests(unittest.TestCase):
    TABLE_HTML = (
        "<table><tr><td>Google PaLM</td><td>540B</td></tr>"
        "<tr><td>Meta Llama 3</td><td>405B</td></tr></table>"
    )

    def test_matching_cells_score_one(self) -> None:
        self.assertEqual(table_cell_f1(self.TABLE_HTML, self.TABLE_HTML), 1.0)

    def test_flattening_a_table_into_prose_scores_zero(self) -> None:
        """Which is exactly what the deterministic engine does with one."""

        flattened = "Google PaLM 540B Meta Llama 3 405B"

        self.assertEqual(table_cell_f1(flattened, self.TABLE_HTML), 0.0)

    def test_one_misread_cell_is_mostly_right(self) -> None:
        damaged = self.TABLE_HTML.replace("540B", "S40B")

        score = table_cell_f1(damaged, self.TABLE_HTML)
        assert score is not None
        self.assertGreater(score, 0.6)
        self.assertLess(score, 1.0)

    def test_a_page_with_no_table_either_side_is_not_scored(self) -> None:
        self.assertIsNone(table_cell_f1(REFERENCE, REFERENCE))


class FabricationTests(unittest.TestCase):
    def test_an_invented_clause_is_reported(self) -> None:
        """The measurement a character error rate cannot make.

        A page can score 2% CER and still assert a sentence the page does not
        contain, and that sentence reaches a reader with a citation attached.
        """

        invented = " ".join(f"contrived{index:03d}" for index in range(14))

        spans = fabricated_spans(f"{REFERENCE} {invented}", REFERENCE)

        self.assertEqual(len(spans), 1)
        self.assertIn("contrived000", spans[0])

    def test_ordinary_misreadings_are_not_fabrication(self) -> None:
        candidate = REFERENCE.replace("theaters", "theatres").replace("radius", "radios")

        self.assertEqual(fabricated_spans(candidate, REFERENCE), [])

    def test_a_faithful_reading_reports_nothing(self) -> None:
        self.assertEqual(fabricated_spans(REFERENCE, REFERENCE), [])


class ScorePageTests(unittest.TestCase):
    def test_a_thin_page_is_reported_but_not_aggregated(self) -> None:
        """One misread word on a caption-only page swings the rate by tens of percent."""

        score = score_page(
            book_id=1,
            page=1,
            category=PROSE,
            engine="gemini",
            candidate="Figure 1.1",
            reference="Figure 1.1",
        )

        self.assertFalse(score.scored)

    def test_a_full_page_is_aggregated(self) -> None:
        long_reference = " ".join(f"word{index:03d}" for index in range(60))

        score = score_page(
            book_id=1,
            page=1,
            category=PROSE,
            engine="gemini",
            candidate=long_reference,
            reference=long_reference,
        )

        self.assertTrue(score.scored)
        self.assertEqual(score.character_error_rate, 0.0)

    def test_provenance_is_serializable(self) -> None:
        score = score_page(
            book_id=1,
            page=2,
            category=TABLE,
            engine="tesseract",
            candidate=REFERENCE,
            reference=REFERENCE,
        )

        provenance = score.provenance()
        self.assertEqual(provenance["engine"], "tesseract")
        self.assertIn("cer", provenance)
        self.assertIn("fabricated_spans", provenance)




class PublishedChapterMatchTests(unittest.TestCase):
    """Comparing an extracted chapter to the one a publisher lists.

    The rule has to be loose enough to accept real disagreement between a
    retailer's listing and the book's own heading, and tight enough that a
    section promoted to a chapter is still counted as spurious. Too loose and
    the check reports success on the outline it was written to catch.
    """

    def test_a_printed_chapter_number_does_not_prevent_a_match(self) -> None:
        self.assertTrue(_matches("1 Proximity Service", "Proximity Service"))
        self.assertTrue(_matches("Chapter 4. Transformer", "Transformer"))

    def test_a_trailing_clause_does_not_prevent_a_match(self) -> None:
        """"Metrics Monitoring" against "Metrics Monitoring and Alerting System"."""

        self.assertTrue(
            _matches("5 Metrics Monitoring and Alerting System", "Metrics Monitoring")
        )

    def test_punctuation_and_case_do_not_prevent_a_match(self) -> None:
        self.assertTrue(
            _matches("ChatGPT: Personal Assistant Chatbot", "chatgpt personal assistant chatbot")
        )

    def test_a_section_promoted_to_a_chapter_is_still_spurious(self) -> None:
        """The four that stole 62 pages between them must not slip through."""

        for promoted in (
            "Text cleaning and normalization",
            "CIDEr",
            "A framework for ML system design interviews",
            "Framing the problem as an ML task",
        ):
            for real in ("Gmail Smart Compose", "Image Captioning", "Introduction and Overview"):
                with self.subTest(promoted=promoted, real=real):
                    self.assertFalse(_matches(promoted, real))

    def test_an_empty_title_never_matches(self) -> None:
        self.assertFalse(_matches("", "Transformer"))
        self.assertFalse(_matches("42", "Transformer"))

if __name__ == "__main__":
    unittest.main()
