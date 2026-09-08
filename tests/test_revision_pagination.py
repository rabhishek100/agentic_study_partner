"""A revision sheet may span more than one spread, and must still read as one.

The sheet was two hand-authored A4 pages: an overview page and a details page.
Dense chapters could not be compressed into that and failed validation at the
word cap — the cap being a proxy for "fits two pages". The sheet now flows its
*detail* content across as many pages as it needs, up to `REVISION_MAX_PAGES`,
while the overview page keeps its fixed composition.

What must hold is that flowing loses nothing and reorders nothing: a reader
works through the sheet front to back, so a packing that moved notes around to
fill pages evenly would break the thing the sheet is for.
"""

import unittest
from unittest import mock

from revision_sheets.html_render import _distribute, max_pages
from revision_sheets import validate as validation


def blocks(*weights, section="model"):
    return [(section, f"<p>{index}</p>", weight) for index, weight in enumerate(weights)]


class DistributionTests(unittest.TestCase):
    def test_every_block_survives_exactly_once_and_in_order(self) -> None:
        source = blocks(*range(1, 13))
        for pages in range(1, 6):
            flowed = [block for run in _distribute(source, pages) for block in run]
            self.assertEqual(flowed, source, f"{pages} pages")

    def test_pages_are_balanced_by_weight_rather_than_by_count(self) -> None:
        """One long note and many short ones should not all land together."""

        source = blocks(100, 10, 10, 10, 10, 10, 10, 10, 10, 10)
        runs = _distribute(source, 2)

        self.assertEqual(len(runs), 2)
        first, second = (sum(b[2] for b in run) for run in runs)
        # Not equal — the first block alone is over half the weight — but the
        # split must not simply halve the *count* and leave 100 + 40 vs 50.
        self.assertLess(abs(first - second), sum(b[2] for b in source))
        self.assertEqual(len(runs[0]), 1)

    def test_no_page_is_left_empty(self) -> None:
        """Asking for more pages than there are blocks yields no blank page."""

        runs = _distribute(blocks(5, 5), 5)

        self.assertTrue(all(run for run in runs))
        self.assertLessEqual(len(runs), 2)

    def test_a_single_page_is_the_whole_flow(self) -> None:
        source = blocks(1, 2, 3)
        self.assertEqual(_distribute(source, 1), [source])

    def test_no_blocks_yields_one_empty_run_rather_than_crashing(self) -> None:
        self.assertEqual(_distribute([], 3), [[]])


class WordBudgetTests(unittest.TestCase):
    """The cap moves with the paper instead of contradicting it."""

    def test_the_budget_scales_with_the_page_allowance(self) -> None:
        with mock.patch.dict("os.environ", {"REVISION_MAX_PAGES": "2"}):
            two = max_pages() * validation.WORDS_CEILING
        with mock.patch.dict("os.environ", {"REVISION_MAX_PAGES": "5"}):
            five = max_pages() * validation.WORDS_CEILING

        # The two-page sheet's tuned ceiling, preserved exactly.
        self.assertEqual(two, 650)
        self.assertEqual(five, 1625)

    def test_a_sheet_that_failed_at_two_pages_fits_at_five(self) -> None:
        """The reported failure: 749 words rejected against a 650 ceiling."""

        with mock.patch.dict("os.environ", {"REVISION_MAX_PAGES": "2"}):
            self.assertGreater(749, max_pages() * validation.WORDS_CEILING)
        with mock.patch.dict("os.environ", {"REVISION_MAX_PAGES": "5"}):
            self.assertLess(749, max_pages() * validation.WORDS_CEILING)

    def test_the_floor_is_two_pages_however_it_is_configured(self) -> None:
        """A one-page sheet has no detail page at all; the search would be empty."""

        with mock.patch.dict("os.environ", {"REVISION_MAX_PAGES": "1"}):
            self.assertEqual(max_pages(), 2)


if __name__ == "__main__":
    unittest.main()


class SchemaAndBudgetAgreeTests(unittest.TestCase):
    """The caps and the word ceiling must not contradict each other.

    Raising the item caps to cover dense chapters is only coherent if a sheet
    that actually uses them can still pass the word budget. Otherwise the model
    is told two incompatible things — fill these fields, and stay under this
    many words — and fails whichever it obeys second.
    """

    # The upper end of the guidance the model is given, per item.
    NOTE_WORDS, ROW_WORDS, CUE_WORDS = 35, 25, 25

    def capacity(self) -> int:
        from revision_sheets.contracts import Sheet

        def cap(field: str) -> int:
            meta = Sheet.model_fields[field].metadata
            return next(m.max_length for m in meta if hasattr(m, "max_length"))

        return (
            cap("essential_notes") * self.NOTE_WORDS
            + cap("comparison_rows") * self.ROW_WORDS
            + cap("recall_cues") * self.CUE_WORDS
            # Title, central idea, diagram description, node and edge labels.
            + 15 + 40 + 15 + 8 * 8 + 10 * 13
        )

    def test_a_full_sheet_fits_the_five_page_budget(self) -> None:
        with mock.patch.dict("os.environ", {"REVISION_MAX_PAGES": "5"}):
            ceiling = max_pages() * validation.WORDS_CEILING

        self.assertLess(
            self.capacity(),
            ceiling,
            "the schema can hold more words than the budget allows; raise the "
            "page allowance or lower the item caps, but do not ship both",
        )
