"""Structural role naming across the outline shapes real books actually use.

Each case here is the level-and-title shape of a book in the corpus, reduced to
the entries that decide the classification. They are regression tests for two
opposite failures: naming roles by depth alone lost the chapters of every book
that groups them under Parts, and naming them by title alone lost the chapters
of every book that numbers them without the word "Chapter".
"""

import unittest

from parsing.outline_roles import (
    chapter_level,
    chapter_number,
    fallback_role,
    outline_roles,
)


def roles_for(entries):
    return outline_roles([level for level, _ in entries], [title for _, title in entries])


def chapters_in(entries):
    return [
        title
        for (_, title), role in zip(entries, roles_for(entries))
        if role == "chapter"
    ]


# "Designing Machine Learning Systems" / "AI Engineering": chapters at level 1,
# wrapped in front and back matter at the same depth.
OREILLY = [
    (1, "Cover"),
    (1, "Copyright"),
    (1, "Table of Contents"),
    (1, "Preface"),
    (2, "Who This Book Is For"),
    (1, "Chapter 1. Overview of Machine Learning Systems"),
    (2, "When to Use Machine Learning"),
    (1, "Chapter 2. Introduction to Machine Learning Systems Design"),
    (1, "Chapter 3. Data Engineering Fundamentals"),
    (1, "Epilogue"),
    (1, "Index"),
]

# "Designing Data-Intensive Applications": Parts at level 1, chapters at 2.
PARTS = [
    (1, "Copyright"),
    (1, "Table of Contents"),
    (1, "Preface"),
    (2, "Who Should Read This Book?"),
    (1, "Part I. Foundations of Data Systems"),
    (2, "Chapter 1. Reliable, Scalable, and Maintainable Applications"),
    (3, "Thinking About Data Systems"),
    (2, "Chapter 2. Data Models and Query Languages"),
    (1, "Part II. Distributed Data"),
    (2, "Chapter 3. Storage and Retrieval"),
    (2, "Chapter 4. Encoding and Evolution"),
    (1, "Glossary"),
    (1, "Index"),
]

# "An Introduction to Statistical Learning": chapters numbered without the word.
BARE_NUMBERS = [
    (1, "Preface"),
    (1, "Contents"),
    (1, "1 Introduction"),
    (2, "2.1 What Is Statistical Learning?"),
    (1, "2 Statistical Learning"),
    (1, "3 Linear Regression"),
    (1, "4 Classification"),
    (1, "Index"),
]

# "System Design Interview": uppercase, colon-separated chapter labels.
UPPERCASE = [
    (1, "System Design Interview: An Insider's Guide"),
    (1, "FORWARD"),
    (1, "CHAPTER 1: SCALE FROM ZERO TO MILLIONS OF USERS"),
    (1, "CHAPTER 2: BACK-OF-THE-ENVELOPE ESTIMATION"),
    (1, "CHAPTER 3: A FRAMEWORK FOR SYSTEM DESIGN INTERVIEWS"),
    (1, "AFTERWORD"),
]


class ChapterNumberTests(unittest.TestCase):
    def test_reads_a_number_with_or_without_the_word(self) -> None:
        for title, expected in (
            ("Chapter 5. Replication", 5),
            ("CHAPTER 12: DESIGN A CHAT SYSTEM", 12),
            ("Ch. 3 Storage", 3),
            ("5 Resampling Methods", 5),
            ("10 Deep Learning", 10),
        ):
            with self.subTest(title=title):
                self.assertEqual(chapter_number(title), expected)

    def test_refuses_numbering_that_is_not_a_chapter(self) -> None:
        """A section, a part, or an appendix must not claim a chapter number."""

        for title in (
            "2.1 What Is Statistical Learning?",
            "Part I. Foundations of Data Systems",
            "Part 2. Distributed Data",
            "Appendix A. Notation",
            "Preface",
            "Index",
        ):
            with self.subTest(title=title):
                self.assertIsNone(chapter_number(title))


class ChapterLevelTests(unittest.TestCase):
    def test_finds_the_depth_holding_the_numbered_run(self) -> None:
        for name, entries, expected in (
            ("o'reilly", OREILLY, 1),
            ("parts", PARTS, 2),
            ("bare numbers", BARE_NUMBERS, 1),
            ("uppercase", UPPERCASE, 1),
        ):
            with self.subTest(book=name):
                levels = [level for level, _ in entries]
                titles = [title for _, title in entries]
                self.assertEqual(chapter_level(levels, titles), expected)

    def test_no_credible_run_is_reported_as_none(self) -> None:
        """An essay collection has no numbering to find, and saying so is the
        honest answer: the caller falls back to depth rather than guessing."""

        entries = [(1, "Foreword"), (1, "On Writing"), (1, "On Reading")]
        levels = [level for level, _ in entries]
        titles = [title for _, title in entries]
        self.assertIsNone(chapter_level(levels, titles))


class OutlineRoleTests(unittest.TestCase):
    def test_parts_do_not_become_chapters(self) -> None:
        """The reported bug: `list the chapters` answered with Copyright,
        Table of Contents, Preface, Part I, Part II, Glossary and Index."""

        self.assertEqual(
            chapters_in(PARTS),
            [
                "Chapter 1. Reliable, Scalable, and Maintainable Applications",
                "Chapter 2. Data Models and Query Languages",
                "Chapter 3. Storage and Retrieval",
                "Chapter 4. Encoding and Evolution",
            ],
        )
        roles = dict(zip([title for _, title in PARTS], roles_for(PARTS)))
        self.assertEqual(roles["Part I. Foundations of Data Systems"], "part")
        self.assertEqual(roles["Copyright"], "front_matter")
        self.assertEqual(roles["Index"], "back_matter")

    def test_sections_are_named_by_distance_from_the_chapter_level(self) -> None:
        """A book with Parts must not shift every section one name deeper."""

        roles = dict(zip([title for _, title in PARTS], roles_for(PARTS)))
        self.assertEqual(roles["Thinking About Data Systems"], "section")

    def test_chapters_numbered_without_the_word_are_still_chapters(self) -> None:
        """Regression: this shape lost all thirteen of one book's chapters."""

        self.assertEqual(
            chapters_in(BARE_NUMBERS),
            ["1 Introduction", "2 Statistical Learning", "3 Linear Regression",
             "4 Classification"],
        )
        roles = dict(zip([title for _, title in BARE_NUMBERS], roles_for(BARE_NUMBERS)))
        self.assertEqual(roles["2.1 What Is Statistical Learning?"], "section")

    def test_front_and_back_matter_are_not_chapters(self) -> None:
        self.assertEqual(
            chapters_in(OREILLY),
            [
                "Chapter 1. Overview of Machine Learning Systems",
                "Chapter 2. Introduction to Machine Learning Systems Design",
                "Chapter 3. Data Engineering Fundamentals",
            ],
        )
        roles = dict(zip([title for _, title in OREILLY], roles_for(OREILLY)))
        self.assertEqual(roles["Cover"], "front_matter")
        self.assertEqual(roles["Epilogue"], "back_matter")

    def test_uppercase_chapter_labels(self) -> None:
        self.assertEqual(len(chapters_in(UPPERCASE)), 3)

    def test_every_entry_keeps_a_searchable_role(self) -> None:
        """The property that makes this safe: the previous title-based rule
        was destructive because unmatched entries landed in a class that scope
        search filtered out, hiding whole books."""

        from parsing.outline_roles import SEARCHABLE_ROLES

        for name, entries in (
            ("o'reilly", OREILLY),
            ("parts", PARTS),
            ("bare numbers", BARE_NUMBERS),
            ("uppercase", UPPERCASE),
        ):
            with self.subTest(book=name):
                roles = roles_for(entries)
                self.assertEqual(len(roles), len(entries))
                for role in roles:
                    self.assertIn(role, SEARCHABLE_ROLES)

    def test_an_unnumbered_outline_falls_back_to_depth(self) -> None:
        entries = [(1, "Foreword"), (1, "On Writing"), (2, "A Digression")]
        self.assertEqual(roles_for(entries), ["chapter", "chapter", "section"])

    def test_fallback_names_appendices_and_depth(self) -> None:
        self.assertEqual(fallback_role(1, "Appendix A Data"), "appendix")
        self.assertEqual(fallback_role(1, "1 Introduction"), "chapter")
        self.assertEqual(fallback_role(2, "Anything"), "section")
        self.assertEqual(fallback_role(3, "Anything"), "subsection")
        self.assertEqual(fallback_role(4, "Anything"), "nested_section")

    def test_empty_outline(self) -> None:
        self.assertEqual(outline_roles([], []), [])

    def test_mismatched_inputs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            outline_roles([1, 2], ["only one title"])


if __name__ == "__main__":
    unittest.main()


class ChapterRunMustSpanTheBookTests(unittest.TestCase):
    """A numbered list inside a page is not a chapter sequence.

    One scanned book's chapters are unnumbered titles, so no depth carried a
    chapter run — except a numbered list of four diffusion-model steps on pages
    288 to 291, which formed a perfect 1..4. That depth was elected, those four
    list items became the book's chapters, and all 302 entries before them
    became front matter. `list the chapters` answered with four sub-steps.
    """

    def _book(self):
        levels = [1] * 31 + [3, 3, 3, 3]
        titles = [f"Topic {index}" for index in range(31)] + [
            "1. Noise addition",
            "2. Preparation of conditioning signals",
            "3. Noise prediction",
            "4. ML objective and loss calculation",
        ]
        pages = list(range(1, 312, 10))[:31] + [288, 289, 289, 290]
        return levels, titles, pages

    def test_a_run_confined_to_a_few_pages_is_refused(self) -> None:
        levels, titles, pages = self._book()

        self.assertIsNone(chapter_level(levels, titles, pages))

    def test_without_pages_the_same_run_is_wrongly_elected(self) -> None:
        """Why passing pages matters, stated as a test rather than a comment."""

        levels, titles, _ = self._book()

        self.assertEqual(chapter_level(levels, titles), 3)

    def test_refusing_it_returns_the_book_to_the_depth_rule(self) -> None:
        levels, titles, pages = self._book()

        roles = outline_roles(levels, titles, pages)

        self.assertEqual(roles[:31], ["chapter"] * 31)
        self.assertNotIn("front_matter", roles[:31])

    def test_a_real_chapter_sequence_still_wins(self) -> None:
        """Chapters spread across the book, which is the whole distinction."""

        levels = [1] * 6
        titles = [f"Chapter {number}. Something" for number in range(1, 7)]
        pages = [10, 60, 110, 160, 210, 260]

        self.assertEqual(chapter_level(levels, titles, pages), 1)

    def test_pages_must_describe_the_same_entries(self) -> None:
        with self.assertRaises(ValueError):
            chapter_level([1, 1], ["Chapter 1", "Chapter 2"], [1])
