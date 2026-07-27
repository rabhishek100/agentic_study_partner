"""Parse a PDF into the TOC-aligned models defined in ``models.py``."""

import logging
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import fitz
from unstructured.documents.elements import Footer, Header, Image, PageBreak, Table
from unstructured.partition.pdf import partition_pdf
from unstructured.staging.base import elements_from_json, elements_to_json

from .models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from .threads import limit_inference_threads
from .version import PARSER_VERSION


__all__ = [
    "PARSER_VERSION",
    "build_sections",
    "extract_elements",
    "extract_batched",
    "extract_selective",
    "pages_needing_layout",
    "extract_toc",
    "load_parsed_book",
    "parse_book",
]

# A page whose only vector content is a rule or underline is not a table.
MINIMUM_DRAWINGS = 4

# Batch size and pool width for parallel extraction. Four workers measured
# 3.9x on the deployed worker and keep peak memory well inside its limit,
# since each process holds its own copy of the layout model.
DEFAULT_BATCH_PAGES = 25
DEFAULT_PARSE_WORKERS = 4

# `hi_res` shells out to Tesseract for every page by default
# ("entire_page"), even for a digital PDF that already carries its text.
# Restricting OCR to regions the text layer does not cover halves the
# per-page cost. Preflight only admits documents with a text layer, so the
# full-page pass has nothing to contribute that pdfminer has not already
# read - except text drawn inside a figure. See docs/parser-performance.md.
BLOCK_OCR = "individual_blocks"
FULL_PAGE_OCR = "entire_page"

logger = logging.getLogger("study_partner.parsing")


def _positive_env(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value

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
                    continue
                # Vector drawings catch what the two detectors above miss:
                # borderless tables and diagrams drawn as paths rather than
                # embedded rasters. The threshold ignores a page whose only
                # drawing is a header rule or an underline.
                if len(page.get_drawings()) >= MINIMUM_DRAWINGS:
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


def _subset_range(pdf_path: str | Path, first: int, last: int, destination: Path) -> Path:
    """Copy one contiguous page range into its own PDF."""

    with fitz.open(pdf_path) as document:
        subset = fitz.open()
        subset.insert_pdf(document, from_page=first, to_page=last)
        subset.save(str(destination))
        subset.close()
    return destination


def _parse_page_range(task: tuple[str, int, int, str]) -> tuple[int, str]:
    """Parse one page range in a worker process and return its JSON file.

    Elements are handed back through a file rather than returned directly:
    they cross a process boundary, and the parser's own JSON form is the one
    representation already known to round-trip.

    Top-level so it can be pickled by a process pool.
    """

    source, first, last, workspace = task
    directory = Path(workspace)
    batch = _subset_range(source, first, last, directory / f"batch-{first:05d}.pdf")
    elements = _partition(batch, "hi_res")
    _restore_page_numbers(elements, list(range(first, last + 1)))
    destination = directory / f"batch-{first:05d}.json"
    elements_to_json(elements, filename=str(destination))
    batch.unlink(missing_ok=True)
    return first, str(destination)


def extract_batched(
    pdf_path: str | Path,
    *,
    batch_pages: int | None = None,
    workers: int | None = None,
    on_batch=None,
):
    """Parse a document as page batches across a process pool.

    Every page still goes through ``hi_res``, so this changes only how the
    work is scheduled: it cannot lose content the way choosing a cheaper
    parser for some pages does.

    Measured on the deployed worker over 24 pages of a real book: 96.7s
    serially, 24.7s across four processes, 16.8s across six, with an
    identical element count every time.

    ``on_batch(completed, total)`` is called as each batch lands, which is
    what turns a long parse from an unmoving bar into real page progress.
    """

    batch_pages = batch_pages or _positive_env("PARSER_BATCH_PAGES", DEFAULT_BATCH_PAGES)
    workers = workers or _positive_env("PARSER_WORKERS", DEFAULT_PARSE_WORKERS)

    with fitz.open(pdf_path) as document:
        page_count = document.page_count

    ranges = [
        (first, min(first + batch_pages - 1, page_count - 1))
        for first in range(0, page_count, batch_pages)
    ]
    if workers < 2 or len(ranges) < 2:
        # One batch, or parallelism disabled: no pool, no copies.
        return _partition(Path(pdf_path), "hi_res")

    source = str(pdf_path)
    collected: dict[int, str] = {}
    with tempfile.TemporaryDirectory(prefix="batched-extract-") as directory:
        tasks = [(source, first, last, directory) for first, last in ranges]
        try:
            with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
                futures = [pool.submit(_parse_page_range, task) for task in tasks]
                for done, future in enumerate(as_completed(futures), start=1):
                    first, path = future.result()
                    collected[first] = path
                    if on_batch is not None:
                        on_batch(done, len(tasks))
        except BrokenProcessPool:
            # A worker died for a reason this process cannot see: the pool
            # reports no cause. Parsing the whole document in-process is
            # slower but produces the same result, which beats failing a job
            # that was going to succeed.
            logger.warning(
                "parse pool broke after %s of %s batches; "
                "falling back to a single-process parse",
                len(collected),
                len(tasks),
            )
            return _partition(Path(pdf_path), "hi_res")

        elements = []
        # Reassemble in page order; batches finish out of order.
        for first, _ in ranges:
            elements.extend(elements_from_json(filename=collected[first]))
    return elements


def ocr_mode() -> str:
    """Which pages Tesseract reads.

    ``PARSER_FULL_PAGE_OCR=1`` restores Unstructured's default for a document
    whose text has to be read off the page rather than out of the file.
    """

    if os.getenv("PARSER_FULL_PAGE_OCR", "0").strip() == "1":
        return FULL_PAGE_OCR
    return BLOCK_OCR


def _partition(path: Path, strategy: str):
    # Before any model is built: both libraries fix their pool width when a
    # session is constructed, and in a batched parse this call is the first
    # thing the worker process does.
    limit_inference_threads()

    options: dict = {"filename": str(path), "strategy": strategy}
    if strategy == "hi_res":
        options.update(
            infer_table_structure=True,
            extract_image_block_types=["Image"],
            extract_image_block_to_payload=True,
            ocr_mode=ocr_mode(),
        )
    return partition_pdf(**options)


def extract_selective(pdf_path: str | Path):
    """Extract with the layout model only where it is needed.

    Pages holding images or tables go through ``hi_res``; the rest go through
    the text-only parser, which is roughly fifty times quicker.

    Opt-in only, and unsafe for a document whose tables matter: measured
    against a full parse of the reference book this is 2.3x quicker but finds
    24 of its 32 tables, because borderless tables are visible to the layout
    model and to no cheap detector. ``docs/parser-performance.md`` records the
    comparison. Use it for a document known to have no tables, or not at all.
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
    on_batch=None,
):
    """Run the expensive layout parser once, then reuse its JSON cache.

    Long documents are parsed as page batches across a process pool, which is
    several times quicker and reports progress as batches land. Every page
    still goes through the same parser.
    """

    cache_path = Path(cache_path)
    if cache_path.exists():
        return elements_from_json(filename=str(cache_path))

    if selective is None:
        # Off by default. Whole-book validation on the reference book found
        # selective extraction still misses 8 of 32 tables even with vector
        # drawings included: hi_res finds borderless tables visually and no
        # cheap classifier predicts that. Losing a quarter of a book's tables
        # is not a trade worth 2.3x. See docs/parser-performance.md.
        selective = os.getenv("PARSER_SELECTIVE_LAYOUT", "0").strip() == "1"
    elements = (
        extract_selective(pdf_path)
        if selective
        else extract_batched(pdf_path, on_batch=on_batch)
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
    on_batch=None,
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
    assign_elements(
        extract_elements(pdf_path, elements_cache, on_batch=on_batch), sections
    )

    book = ParsedBook(source=str(pdf_path), toc=toc, sections=sections)
    book_cache.parent.mkdir(parents=True, exist_ok=True)
    book_cache.write_text(book.model_dump_json(), encoding="utf-8")
    return book
