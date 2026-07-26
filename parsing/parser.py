"""Parse a PDF into the TOC-aligned models defined in ``models.py``."""

import os
import tempfile
from pathlib import Path

import fitz
from unstructured.documents.elements import Footer, Header, Image, PageBreak, Table
from unstructured.partition.pdf import partition_pdf
from unstructured.staging.base import elements_from_json, elements_to_json

from .models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from .version import PARSER_VERSION


__all__ = [
    "PARSER_VERSION",
    "build_sections",
    "extract_elements",
    "extract_selective",
    "pages_needing_layout",
    "extract_toc",
    "load_parsed_book",
    "parse_book",
]

CACHE_DIR = Path("cache")
ELEMENTS_CACHE = CACHE_DIR / "elements.json"
BOOK_CACHE = CACHE_DIR / "parsed_book.json"


def load_parsed_book(path: str | Path = BOOK_CACHE) -> ParsedBook:
    """Load parser output without processing the PDF again."""

    return ParsedBook.model_validate_json(Path(path).read_text(encoding="utf-8"))


def extract_toc(pdf_path: str | Path) -> tuple[list[tuple[int, str, int]], int]:
    """Read the PDF's embedded outline and page count."""

    with fitz.open(pdf_path) as document:
        toc = [tuple(entry) for entry in document.get_toc()]
        page_count = document.page_count

    if not toc:
        raise ValueError(f"{pdf_path} has no embedded table of contents")
    return toc, page_count


def build_sections(toc: list[tuple[int, str, int]], page_count: int) -> list[Section]:
    """Create one section per TOC entry while preserving its ancestry."""

    sections: list[Section] = []
    path: list[str] = []

    for index, (level, title, start_page) in enumerate(toc):
        path[level - 1 :] = [title]
        next_page = toc[index + 1][2] if index + 1 < len(toc) else page_count + 1
        sections.append(
            Section(
                path=list(path),
                level=level,
                start_page=start_page,
                end_page=max(start_page, next_page - 1),
            )
        )

    return sections


def pages_needing_layout(pdf_path: str | Path) -> list[int]:
    """Zero-based pages that hold an embedded image or a detectable table.

    Only these pages need the layout model. Pages of plain prose carry their
    text in the PDF already, and rendering them to pixels to rediscover it is
    the bulk of a long parse.

    Detection is deliberately biased towards including a page: an unreadable
    page, or one whose table detection raises, is treated as needing layout.
    Extracting a page twice costs seconds; missing a table loses content the
    canonical store is supposed to hold losslessly.
    """

    rich: list[int] = []
    with fitz.open(pdf_path) as document:
        for index, page in enumerate(document):
            try:
                if page.get_images():
                    rich.append(index)
                    continue
                if page.find_tables().tables:
                    rich.append(index)
            except Exception:
                rich.append(index)
    return rich


def _subset(pdf_path: str | Path, pages: list[int], destination: Path) -> Path:
    """Write the given pages, in order, into their own PDF."""

    with fitz.open(pdf_path) as document:
        subset = fitz.open()
        for page in pages:
            subset.insert_pdf(document, from_page=page, to_page=page)
        subset.save(str(destination))
        subset.close()
    return destination


def _restore_page_numbers(elements, pages: list[int]) -> list:
    """Rewrite subset page numbers back to the original document's numbering.

    Chunks cite page numbers, so a page that survives extraction with the
    wrong number is worse than one that fails loudly.
    """

    restored = []
    for element in elements:
        number = element.metadata.page_number
        if number is None or not 1 <= number <= len(pages):
            raise ValueError(
                f"extracted element reports page {number} outside the "
                f"{len(pages)}-page subset it came from"
            )
        element.metadata.page_number = pages[number - 1] + 1
        restored.append(element)
    return restored


def _partition(path: Path, strategy: str):
    options: dict = {"filename": str(path), "strategy": strategy}
    if strategy == "hi_res":
        options.update(
            infer_table_structure=True,
            extract_image_block_types=["Image"],
            extract_image_block_to_payload=True,
        )
    return partition_pdf(**options)


def extract_selective(pdf_path: str | Path):
    """Extract with the layout model only where it is needed.

    Pages holding images or tables go through ``hi_res``; the rest go through
    the text-only parser, which is roughly fifty times quicker. Every element
    is mapped back to its original page and the two sets are merged in page
    order, so the result is indistinguishable downstream from a whole-document
    ``hi_res`` parse.
    """

    with fitz.open(pdf_path) as document:
        total = document.page_count
    rich = pages_needing_layout(pdf_path)
    plain = [page for page in range(total) if page not in set(rich)]

    if not plain:
        # Nothing to save; avoid the copy and parse the original directly.
        return _partition(Path(pdf_path), "hi_res")

    elements = []
    with tempfile.TemporaryDirectory(prefix="selective-extract-") as directory:
        workspace = Path(directory)
        for pages, strategy in ((rich, "hi_res"), (plain, "fast")):
            if not pages:
                continue
            subset = _subset(pdf_path, pages, workspace / f"{strategy}.pdf")
            elements.extend(_restore_page_numbers(_partition(subset, strategy), pages))

    # Stable sort keeps each page's reading order as its parser produced it.
    elements.sort(key=lambda element: element.metadata.page_number)
    return elements


def extract_elements(
    pdf_path: str | Path,
    cache_path: str | Path = ELEMENTS_CACHE,
    selective: bool | None = None,
):
    """Run the expensive layout parser once, then reuse its JSON cache."""

    cache_path = Path(cache_path)
    if cache_path.exists():
        return elements_from_json(filename=str(cache_path))

    if selective is None:
        selective = os.getenv("PARSER_SELECTIVE_LAYOUT", "1").strip() != "0"
    elements = (
        extract_selective(pdf_path)
        if selective
        else _partition(Path(pdf_path), "hi_res")
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Write through a sibling and rename, so a process killed mid-write leaves
    # no truncated cache that a later run would mistake for a complete parse.
    partial_path = cache_path.with_name(cache_path.name + ".partial")
    elements_to_json(elements, filename=str(partial_path))
    partial_path.replace(cache_path)
    return elements


def assign_elements(elements, sections: list[Section]) -> None:
    """Assign extracted elements to the latest TOC entry that has started."""

    first_content_page = sections[0].start_page

    for element in elements:
        page = element.metadata.page_number
        if page is None or page < first_content_page:
            continue
        if isinstance(element, (Header, Footer, PageBreak)):
            continue
        if not element.text and not isinstance(element, Image):
            continue

        section = next(
            (item for item in reversed(sections) if item.start_page <= page),
            None,
        )
        if section is None:
            continue

        if isinstance(element, Table):
            table_index = len(section.tables)
            section.texts.append(
                TextBlock(
                    text=f"[TABLE {table_index}]",
                    category="TablePlaceholder",
                    page=page,
                )
            )
            section.tables.append(
                TableBlock(
                    html=element.metadata.text_as_html,
                    text=element.text,
                    page=page,
                )
            )
        elif isinstance(element, Image):
            if not element.metadata.image_base64:
                continue
            image_index = len(section.images)
            section.texts.append(
                TextBlock(
                    text=f"[IMAGE {image_index}]",
                    category="ImagePlaceholder",
                    page=page,
                )
            )
            section.images.append(
                ImageBlock(
                    base64=element.metadata.image_base64,
                    mime=element.metadata.image_mime_type or "image/jpeg",
                    page=page,
                )
            )
        else:
            section.texts.append(
                TextBlock(
                    text=element.text,
                    category=element.category,
                    page=page,
                )
            )


def parse_book(
    pdf_path: str | Path,
    *,
    force: bool = False,
    book_cache: str | Path = BOOK_CACHE,
    elements_cache: str | Path = ELEMENTS_CACHE,
) -> ParsedBook:
    """Return cached parser output or parse and cache the source PDF."""

    book_cache = Path(book_cache)
    if book_cache.exists() and not force:
        cached = load_parsed_book(book_cache)
        current_toc, _ = extract_toc(pdf_path)
        if cached.source != str(pdf_path) or cached.toc != current_toc:
            raise ValueError(
                "parsed-book cache does not match the source PDF; "
                "delete it or run with force=True"
            )
        return cached

    toc, page_count = extract_toc(pdf_path)
    sections = build_sections(toc, page_count)
    assign_elements(extract_elements(pdf_path, elements_cache), sections)

    book = ParsedBook(source=str(pdf_path), toc=toc, sections=sections)
    book_cache.parent.mkdir(parents=True, exist_ok=True)
    book_cache.write_text(book.model_dump_json(), encoding="utf-8")
    return book
