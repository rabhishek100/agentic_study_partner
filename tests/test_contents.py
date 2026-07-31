"""Reading a book's own printed contents page, and refusing a poisoned outline."""

import unittest

from ingestion.outlines import detect_poisoning
from parsing.contents import (
    contents_to_outline,
    find_contents_pages,
    parse_printed_contents,
)
from parsing.transcript import printed_numbering


# The contents page of one of the scans, as the transcription returns it. Plain
# OCR reads this two-column layout column by column and destroys the pairing;
# the table form is what makes it usable at all.
SDI_CONTENTS = """# Contents

<table>
  <tr><td>Foreword</td><td>iii</td></tr>
  <tr><td>Acknowledgements</td><td>v</td></tr>
  <tr><td>Chapter 1 Proximity Service</td><td>1</td></tr>
  <tr><td>Chapter 2 Nearby Friends</td><td>35</td></tr>
  <tr><td>Chapter 3 Google Maps</td><td>59</td></tr>
  <tr><td>Chapter 4 Distributed Message Queue</td><td>91</td></tr>
</table>
"""

# The other common form: a dotted leader instead of a table.
LEADER_CONTENTS = """# Table of Contents

Chapter 1. Machine Learning Basics ........... 16
1.1. AI and Machine Learning .................. 16
1.2. Model ................................... 20
Chapter 2. Language Modeling Basics .......... 50
2.1. Bag of Words ........................... 50
"""


class FindContentsTests(unittest.TestCase):
    def test_a_listing_page_is_found_by_its_heading_and_rows(self) -> None:
        self.assertEqual(
            find_contents_pages([(1, "Cover"), (4, SDI_CONTENTS), (5, "Foreword")]),
            [4],
        )

    def test_a_mention_of_the_word_is_not_a_listing(self) -> None:
        """A body page discussing contents has no rows to offer."""

        self.assertEqual(
            find_contents_pages([(9, "# Contents\n\nThis chapter covers a lot.")]),
            [],
        )

    def test_a_listing_continuing_onto_the_next_page_is_included(self) -> None:
        overflow = (
            "<table>"
            "<tr><td>Chapter 5 Metrics</td><td>131</td></tr>"
            "<tr><td>Chapter 6 Ad Click</td><td>159</td></tr>"
            "<tr><td>Chapter 7 Hotel</td><td>195</td></tr>"
            "</table>"
        )
        self.assertEqual(
            find_contents_pages([(4, SDI_CONTENTS), (5, overflow), (6, "Foreword")]),
            [4, 5],
        )


class ParsePrintedContentsTests(unittest.TestCase):
    def test_a_table_listing_keeps_titles_paired_with_pages(self) -> None:
        contents = parse_printed_contents([(4, SDI_CONTENTS)])

        self.assertEqual(len(contents.entries), 6)
        by_title = {entry.title: entry for entry in contents.entries}
        self.assertEqual(by_title["Chapter 2 Nearby Friends"].printed_page, 35)
        self.assertFalse(by_title["Chapter 2 Nearby Friends"].roman)

    def test_roman_front_matter_is_read_as_roman(self) -> None:
        contents = parse_printed_contents([(4, SDI_CONTENTS)])

        foreword = next(e for e in contents.entries if e.title == "Foreword")
        self.assertEqual(foreword.printed_page, 3)
        self.assertTrue(foreword.roman)

    def test_a_dotted_leader_listing_is_read_too(self) -> None:
        contents = parse_printed_contents([(7, LEADER_CONTENTS)])

        titles = [entry.title for entry in contents.entries]
        self.assertIn("Chapter 1. Machine Learning Basics", titles)
        self.assertIn("1.2. Model", titles)

    def test_numbering_declares_depth(self) -> None:
        """Indentation survives neither OCR nor a table cell; numbering does."""

        contents = parse_printed_contents([(7, LEADER_CONTENTS)])
        by_title = {entry.title: entry.level for entry in contents.entries}

        self.assertEqual(by_title["Chapter 1. Machine Learning Basics"], 1)
        self.assertEqual(by_title["1.1. AI and Machine Learning"], 2)

    def test_the_listings_own_heading_is_not_a_row(self) -> None:
        titles = [e.title for e in parse_printed_contents([(4, SDI_CONTENTS)]).entries]
        self.assertNotIn("Contents", titles)

    def test_a_book_without_a_listing_yields_nothing(self) -> None:
        contents = parse_printed_contents([(1, "# Chapter One\n\nBody text.")])

        self.assertFalse(contents)
        self.assertEqual(contents.entries, ())


class ContentsToOutlineTests(unittest.TestCase):
    def _numbering(self):
        return printed_numbering(
            [
                (5, "<!-- footer: iii -->"),
                (7, "<!-- footer: v -->"),
                (9, "<!-- footer: | 1 -->"),
                (10, "<!-- footer: | 2 -->"),
                (43, "<!-- footer: 35 | Chapter 2 -->"),
                (67, "<!-- footer: 59 | Chapter 3 -->"),
            ]
        )

    def test_printed_pages_are_mapped_onto_pdf_pages(self) -> None:
        contents = parse_printed_contents([(4, SDI_CONTENTS)])

        outline, warnings = contents_to_outline(
            contents, numbering=self._numbering(), page_count=427
        )

        placed = {title: page for _, title, page in outline}
        self.assertEqual(placed["Chapter 1 Proximity Service"], 9)
        self.assertEqual(placed["Chapter 2 Nearby Friends"], 43)
        # Front matter resolves through its own roman anchors.
        self.assertEqual(placed["Foreword"], 5)
        self.assertEqual(warnings, [])

    def test_an_entry_that_cannot_be_placed_is_named_not_guessed(self) -> None:
        """Inventing a page would put a citation somewhere it does not belong."""

        contents = parse_printed_contents([(4, SDI_CONTENTS)])

        outline, warnings = contents_to_outline(
            contents, numbering=self._numbering(), page_count=12
        )

        self.assertTrue(warnings)
        self.assertIn("Chapter 4 Distributed Message Queue", warnings[0])
        self.assertNotIn(
            "Chapter 4 Distributed Message Queue", [title for _, title, _ in outline]
        )

    def test_levels_never_jump(self) -> None:
        contents = parse_printed_contents([(7, LEADER_CONTENTS)])
        numbering = printed_numbering(
            [(17, "<!-- footer: 16 -->"), (21, "<!-- footer: 20 -->")]
        )

        outline, _ = contents_to_outline(
            contents, numbering=numbering, page_count=209
        )

        previous = 0
        for level, _, _ in outline:
            self.assertLessEqual(level, previous + 1)
            previous = level


class OutlinePoisoningTests(unittest.TestCase):
    """OCR software builds an outline by promoting styled lines, equations too."""

    # Entries taken verbatim from the embedded outline of one of the books.
    POISONED = [
        (1, "Contents", 7),
        (2, "1.1. AI and Machine Learning", 17),
        (1, "derr3 derr3 dd3 dw dd3 dw", 26),
        (2, "dem", 27),
        (2, "O = 2d.", 27),
        (2, "Sen-! dw »", 27),
        (1, "dd±", 27),
        (2, "derr2 dw", 27),
        (2, "-^=2d3 dw", 27),
        (2, "• 150 = 300 • (150w + b - 200), dd±", 27),
        (2, "• 200 = 400 • (200w + b - 600),", 27),
        (2, "• 260 = 520 • (260iv + b - 500)", 27),
        (2, "1.3. Four-Step Machine Learning Process", 29),
    ]

    def test_an_outline_harvested_off_the_pages_is_detected(self) -> None:
        poisoning = detect_poisoning(self.POISONED)

        self.assertTrue(poisoning.poisoned)
        self.assertIn(27, poisoning.crowded_pages)
        self.assertGreater(poisoning.junk_ratio, 0.10)

    def test_a_well_formed_outline_is_not_poisoned(self) -> None:
        clean = [
            (1, "Chapter 1. Machine Learning Basics", 16),
            (2, "1.1. AI and Machine Learning", 16),
            (2, "1.2. Model", 20),
            (1, "Chapter 2. Language Modeling Basics", 50),
        ]

        self.assertFalse(detect_poisoning(clean).poisoned)

    def test_odd_titles_alone_do_not_condemn_an_outline(self) -> None:
        """Both signals are required, and this is why.

        Front matter is routinely punctuated in ways that fail a title test —
        a copyright line ends in a full stop — and a chapter opening may
        legitimately have several subsections starting on its first page.
        Neither on its own describes an outline harvested off a page.
        """

        odd = [
            (1, "Copyright © 2025 Someone. All rights reserved.", 4),
            (1, "Chapter 1. Basics", 16),
            (2, "1.1. Beginnings", 16),
            (1, "Chapter 2. More", 50),
        ]

        poisoning = detect_poisoning(odd)
        self.assertGreater(poisoning.junk_ratio, 0.10)
        self.assertEqual(poisoning.crowded_pages, ())
        self.assertFalse(poisoning.poisoned)

    def test_an_empty_outline_is_not_poisoned(self) -> None:
        self.assertFalse(detect_poisoning([]).poisoned)


if __name__ == "__main__":
    unittest.main()
