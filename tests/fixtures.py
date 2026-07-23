"""Shared deterministic model fixtures for Postgres-backed tests."""

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock


FILE_HASH = "a" * 64


def sample_book() -> ParsedBook:
    """Return a small lossless hierarchy with text, table, and image blocks."""

    sections = [
        Section(
            path=["Chapter 1"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(text="Chapter introduction", category="NarrativeText", page=1)
            ],
        ),
        Section(
            path=["Chapter 1", "Core idea"],
            level=2,
            start_page=2,
            end_page=2,
            texts=[
                TextBlock(text="Before table", category="NarrativeText", page=2),
                TextBlock(text="[TABLE 0]", category="TablePlaceholder", page=2),
                TextBlock(text="After table", category="NarrativeText", page=2),
            ],
            tables=[
                TableBlock(
                    html="<table><tr><td>A</td></tr></table>",
                    text="A",
                    page=2,
                )
            ],
        ),
        Section(
            path=["Chapter 1", "Core idea", "Diagram"],
            level=3,
            start_page=3,
            end_page=5,
            texts=[TextBlock(text="[IMAGE 0]", category="ImagePlaceholder", page=3)],
            images=[ImageBlock(base64="aW1hZ2U=", mime="image/png", page=3)],
        ),
    ]
    return ParsedBook(
        source="sources/books/sample.pdf",
        toc=[
            (1, "Chapter 1", 1),
            (2, "Core idea", 2),
            (3, "Diagram", 3),
        ],
        sections=sections,
    )
