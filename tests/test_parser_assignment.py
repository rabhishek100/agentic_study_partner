"""Content assignment at page boundaries and deterministic layout cleanup."""

from types import SimpleNamespace
import unittest

from parsing.models import (
    DETECTED_FOOTER_CATEGORY,
    DETECTED_HEADER_CATEGORY,
)
from parsing.parser import assign_elements, build_sections


class FakeElement:
    def __init__(
        self,
        text: str,
        *,
        page: int,
        category: str = "NarrativeText",
        top: float = 0.30,
        bottom: float = 0.40,
    ) -> None:
        self.text = text
        self.category = category
        self.metadata = SimpleNamespace(
            page_number=page,
            coordinates=SimpleNamespace(
                points=(
                    (100.0, top * 1000),
                    (900.0, top * 1000),
                    (900.0, bottom * 1000),
                    (100.0, bottom * 1000),
                ),
                system=SimpleNamespace(height=1000),
            ),
        )


def element(
    text: str,
    page: int,
    *,
    category: str = "NarrativeText",
    top: float = 0.30,
    bottom: float = 0.40,
) -> FakeElement:
    return FakeElement(
        text,
        page=page,
        category=category,
        top=top,
        bottom=bottom,
    )


class SamePageAssignmentTests(unittest.TestCase):
    def test_sibling_sections_are_split_at_their_ordered_heading_blocks(self):
        sections = build_sections(
            [
                (1, "Chapter 1", 1),
                (2, "First topic", 2),
                (2, "Second topic", 2),
                (1, "Chapter 2", 3),
            ],
            page_count=3,
        )
        elements = [
            element("Chapter 1", 1, category="Title"),
            element("Opening", 1),
            element("First topic", 2, category="Title"),
            element("First body", 2),
            element("Second topic", 2, category="Title"),
            element("Second body", 2),
            element("Chapter 2", 3, category="Title"),
            element("Closing", 3),
        ]

        assign_elements(elements, sections)

        self.assertEqual(
            [block.text for block in sections[1].texts],
            ["First topic", "First body"],
        )
        self.assertEqual(
            [block.text for block in sections[2].texts],
            ["Second topic", "Second body"],
        )

    def test_an_unresolved_same_page_collision_fails_instead_of_guessing(self):
        sections = build_sections(
            [
                (1, "Chapter 1", 1),
                (2, "First topic", 2),
                (2, "Second topic", 2),
            ],
            page_count=3,
        )

        with self.assertRaisesRegex(ValueError, "sharing page 2"):
            assign_elements(
                [
                    element("Chapter 1", 1, category="Title"),
                    element("First topic", 2, category="Title"),
                    element("Only the first body", 2),
                ],
                sections,
            )

    def test_collision_page_without_extracted_elements_fails(self):
        sections = build_sections(
            [
                (1, "Chapter 1", 1),
                (2, "First topic", 2),
                (2, "Second topic", 2),
            ],
            page_count=3,
        )

        with self.assertRaisesRegex(
            ValueError, "no extracted elements.*collision page"
        ):
            assign_elements(
                [element("Chapter 1", 1, category="Title")],
                sections,
            )

    def test_wrapped_title_blocks_form_one_heading_boundary(self):
        sections = build_sections(
            [
                (1, "Chapter 1", 1),
                (2, "Bottleneck in Reinforcement Learning", 2),
                (2, "The Solution: OpenEnv", 2),
            ],
            page_count=3,
        )

        assign_elements(
            [
                element("Chapter 1", 1, category="Title"),
                element("Bottleneck in Reinforcement", 2, category="Title"),
                element("Learning", 2, category="Title"),
                element("First body", 2),
                element("The Solution:", 2, category="Title"),
                element("OpenEnv", 2, category="Title"),
                element("Second body", 2),
            ],
            sections,
        )

        self.assertIn("First body", sections[1].full_text)
        self.assertNotIn("Second body", sections[1].full_text)
        self.assertIn("Second body", sections[2].full_text)

    def test_content_before_a_matched_heading_remains_with_the_prior_section(self):
        sections = build_sections(
            [(1, "Chapter 1", 1), (1, "Chapter 2", 2)],
            page_count=3,
        )

        assign_elements(
            [
                element("Chapter 1", 1, category="Title"),
                element("Continued from chapter one", 2),
                element("Chapter 2", 2, category="Title"),
                element("New chapter body", 2),
            ],
            sections,
        )

        self.assertIn("Continued from chapter one", sections[0].full_text)
        self.assertEqual(sections[0].end_page, 2)
        self.assertNotIn("Continued from chapter one", sections[1].full_text)


class BoilerplateClassificationTests(unittest.TestCase):
    def test_repeated_margin_text_and_page_numbers_are_retained_but_classified(self):
        sections = build_sections([(1, "Chapter 1", 1)], page_count=3)
        elements = []
        for page in range(1, 4):
            elements.extend(
                [
                    element(
                        "Publisher.example",
                        page,
                        category="UncategorizedText",
                        top=0.04,
                        bottom=0.08,
                    ),
                    element(f"Body {page}", page),
                    element(
                        str(page),
                        page,
                        category="UncategorizedText",
                        top=0.93,
                        bottom=0.97,
                    ),
                ]
            )

        assign_elements(elements, sections)

        headers = [
            block
            for block in sections[0].texts
            if block.text == "Publisher.example"
        ]
        footers = [
            block for block in sections[0].texts if block.text in {"1", "2", "3"}
        ]
        self.assertEqual(len(headers), 3)
        self.assertTrue(
            all(block.category == DETECTED_HEADER_CATEGORY for block in headers)
        )
        self.assertTrue(
            all(block.category == DETECTED_FOOTER_CATEGORY for block in footers)
        )
        self.assertIn("Publisher.example", sections[0].full_text)

    def test_repeated_body_text_is_not_mistaken_for_margin_boilerplate(self):
        sections = build_sections([(1, "Chapter 1", 1)], page_count=3)

        assign_elements(
            [element("Important repeated definition", page) for page in range(1, 4)],
            sections,
        )

        self.assertTrue(
            all(
                block.category == "NarrativeText"
                for block in sections[0].texts
            )
        )


if __name__ == "__main__":
    unittest.main()
