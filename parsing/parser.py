"""Parse a PDF into the TOC-aligned models defined in ``models.py``."""

from base64 import b64encode
from bisect import bisect_left, bisect_right
from collections import defaultdict
import logging
import math
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import fitz
from unstructured.documents.coordinates import PointSpace
from unstructured.documents.elements import (
    ElementMetadata,
    Footer,
    Header,
    Image,
    NarrativeText,
    PageBreak,
    Table,
)
from unstructured.partition.pdf import partition_pdf
from unstructured.staging.base import elements_from_json, elements_to_json

from .models import (
    DETECTED_FOOTER_CATEGORY,
    DETECTED_HEADER_CATEGORY,
    NON_CONTENT_CATEGORIES,
    ImageBlock,
    ParsedBook,
    Section,
    TableBlock,
    TextBlock,
)
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
MARGIN_HEADER_LIMIT = 0.12
MARGIN_FOOTER_LIMIT = 0.88
MINIMUM_REPEATED_MARGIN_PAGES = 3

_PAGE_NUMBER = re.compile(r"^(?:page\s+)?(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
_NUMBER_PREFIX = re.compile(
    r"^(?:(?:part|chapter|appendix)\s+[A-Z0-9IVXLC]+[.:]?\s*|"
    r"(?:[A-Z]|\d+)(?:\.\d+)*\.?\s+)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Batch size and pool width for parallel extraction. Four workers measured
# 3.9x on the deployed worker and keep peak memory well inside its limit,
# since each process holds its own copy of the layout model.
DEFAULT_BATCH_PAGES = 25
DEFAULT_PARSE_WORKERS = 4

# Some digitally generated plots contain hundreds of thousands of vector
# paths on one page. pdfminer and the hi-res layout path can spend tens of
# minutes interpreting that page even though PyMuPDF can recover its native
# text and render it in seconds. Keep the threshold far above ordinary
# diagrams and tables; the fallback preserves native text plus a full-page
# visual rather than silently dropping either representation.
DEFAULT_MAX_VECTOR_DRAWINGS = 20_000
VECTOR_FALLBACK_RENDER_SCALE = 2.0
VECTOR_FALLBACK_JPEG_QUALITY = 85

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


def _vector_complexity_pages(
    pdf_path: str | Path,
    *,
    maximum: int | None = None,
) -> list[int]:
    """Return zero-based pages too vector-heavy for the normal PDF parser."""

    maximum = maximum or _positive_env(
        "PARSER_MAX_VECTOR_DRAWINGS",
        DEFAULT_MAX_VECTOR_DRAWINGS,
    )
    pages: list[int] = []
    with fitz.open(pdf_path) as document:
        for index, page in enumerate(document):
            try:
                if len(page.get_drawings()) > maximum:
                    pages.append(index)
            except Exception:
                # Classification failure is not evidence that the normal,
                # lossless parser is unsafe. Let hi_res handle this page and
                # fail loudly if the document itself is unreadable.
                logger.warning(
                    "could not measure vector complexity on PDF page %s",
                    index + 1,
                    exc_info=True,
                )
    return pages


def _element_top(element) -> float:
    coordinates = getattr(element.metadata, "coordinates", None)
    points = getattr(coordinates, "points", None)
    if not points:
        return 0.0
    return min(float(point[1]) for point in points)


def _extract_vector_fallback_page(pdf_path: str | Path, page_index: int) -> list:
    """Preserve one pathological page as native text and a rendered visual.

    This intentionally does not call pdfminer or Unstructured's PDF
    partitioner: both interpret every vector path and are the source of the
    pathological runtime. PyMuPDF reads the existing text layer directly.
    The rendered page retains plots, tables, and other visual content that
    cannot be represented by text blocks alone.
    """

    with fitz.open(pdf_path) as document:
        page = document[page_index]
        width = float(page.rect.width)
        height = float(page.rect.height)
        coordinate_system = PointSpace(width=width, height=height)
        page_number = page_index + 1
        elements = []
        drawings = page.get_drawings()
        drawing_rects = [
            fitz.Rect(drawing["rect"])
            for drawing in drawings
            if drawing.get("rect") is not None
            and not fitz.Rect(drawing["rect"]).is_empty
            and not fitz.Rect(drawing["rect"]).is_infinite
        ]
        if drawing_rects:
            visual_rect = fitz.Rect(drawing_rects[0])
            for rectangle in drawing_rects[1:]:
                visual_rect.include_rect(rectangle)
            visual_rect = visual_rect & page.rect
        else:
            # A page can mutate between classification and parsing only if
            # the source file changed. The ingestion hash prevents that, but
            # retaining the whole visual is safer than dropping it here.
            visual_rect = fitz.Rect(page.rect)

        for block in page.get_text("blocks", sort=True):
            x0, y0, x1, y1, text, _, block_type = block[:7]
            text = str(text).strip()
            if block_type != 0 or not text:
                continue
            elements.append(
                NarrativeText(
                    text=text,
                    coordinates=(
                        (float(x0), float(y0)),
                        (float(x1), float(y0)),
                        (float(x1), float(y1)),
                        (float(x0), float(y1)),
                    ),
                    coordinate_system=coordinate_system,
                    metadata=ElementMetadata(page_number=page_number),
                )
            )

        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(
                VECTOR_FALLBACK_RENDER_SCALE,
                VECTOR_FALLBACK_RENDER_SCALE,
            ),
            clip=visual_rect,
            alpha=False,
        )
        encoded = b64encode(
            pixmap.tobytes(
                "jpeg",
                jpg_quality=VECTOR_FALLBACK_JPEG_QUALITY,
            )
        ).decode("ascii")
        elements.append(
            Image(
                text="",
                coordinates=(
                    (float(visual_rect.x0), float(visual_rect.y0)),
                    (float(visual_rect.x1), float(visual_rect.y0)),
                    (float(visual_rect.x1), float(visual_rect.y1)),
                    (float(visual_rect.x0), float(visual_rect.y1)),
                ),
                coordinate_system=coordinate_system,
                metadata=ElementMetadata(
                    page_number=page_number,
                    image_base64=encoded,
                    image_mime_type="image/jpeg",
                ),
            )
        )

    # Put the page visual before its native text, matching its full-page box.
    # Python's stable sort preserves PyMuPDF's reading order for text blocks.
    elements.sort(key=_element_top)
    return elements


def _page_ranges(
    page_count: int,
    batch_pages: int,
    excluded: set[int],
) -> list[tuple[int, int]]:
    """Build contiguous, size-bounded ranges without excluded pages."""

    ranges: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for page in range(page_count):
        if page in excluded:
            if start is not None and previous is not None:
                ranges.append((start, previous))
            start = previous = None
            continue
        if start is None:
            start = previous = page
            continue
        if previous is not None and page == previous + 1 and page - start < batch_pages:
            previous = page
            continue
        ranges.append((start, previous if previous is not None else start))
        start = previous = page
    if start is not None:
        ranges.append((start, previous if previous is not None else start))
    return ranges


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

    fallback_pages = _vector_complexity_pages(pdf_path)
    excluded = set(fallback_pages)
    ranges = _page_ranges(page_count, batch_pages, excluded)
    if not fallback_pages and (workers < 2 or len(ranges) < 2):
        # One batch, or parallelism disabled: no pool, no copies.
        return _partition(Path(pdf_path), "hi_res")

    source = str(pdf_path)
    collected: dict[int, str] = {}
    fallback_elements = []
    total_tasks = len(ranges) + len(fallback_pages)
    completed_tasks = 0
    for page in fallback_pages:
        fallback_elements.extend(_extract_vector_fallback_page(pdf_path, page))
        completed_tasks += 1
        if on_batch is not None:
            on_batch(completed_tasks, total_tasks)

    with tempfile.TemporaryDirectory(prefix="batched-extract-") as directory:
        tasks = [(source, first, last, directory) for first, last in ranges]
        if workers < 2 or len(tasks) < 2:
            for task in tasks:
                first, path = _parse_page_range(task)
                collected[first] = path
                completed_tasks += 1
                if on_batch is not None:
                    on_batch(completed_tasks, total_tasks)
        else:
            try:
                with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
                    futures = [pool.submit(_parse_page_range, task) for task in tasks]
                    for future in as_completed(futures):
                        first, path = future.result()
                        collected[first] = path
                        completed_tasks += 1
                        if on_batch is not None:
                            on_batch(completed_tasks, total_tasks)
            except BrokenProcessPool:
                if not fallback_pages:
                    # A worker died for a reason this process cannot see: the
                    # pool reports no cause. Parsing the whole document
                    # in-process is slower but produces the same result.
                    logger.warning(
                        "parse pool broke after %s of %s batches; "
                        "falling back to a single-process parse",
                        len(collected),
                        len(tasks),
                    )
                    return _partition(Path(pdf_path), "hi_res")

                # The whole document includes pages deliberately kept away
                # from pdfminer, so retry only the safe ranges in-process.
                logger.warning(
                    "parse pool broke after %s of %s safe batches; "
                    "retrying safe ranges in-process",
                    len(collected),
                    len(tasks),
                )
                collected.clear()
                for task in tasks:
                    first, path = _parse_page_range(task)
                    collected[first] = path

        elements = list(fallback_elements)
        # Reassemble in page order; batches finish out of order.
        for first, _ in ranges:
            elements.extend(elements_from_json(filename=collected[first]))
    elements.sort(key=lambda element: (element.metadata.page_number, _element_top(element)))
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


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return " ".join(normalized.split()).casefold()


def _heading_matches(title: str, candidate: str) -> bool:
    """Match one short extracted heading without treating body prose as one."""

    title_key = _normalized_text(title)
    candidate_key = _normalized_text(candidate)
    if not title_key or not candidate_key:
        return False
    if title_key == candidate_key:
        return True
    # The fast extractor commonly emits "Heading First paragraph..." as one
    # block. A title at the start is still an unambiguous boundary even when
    # the remainder is long body text.
    if candidate_key.startswith(title_key):
        return True

    title_without_number = _NUMBER_PREFIX.sub("", title_key)
    candidate_without_number = _NUMBER_PREFIX.sub("", candidate_key)
    if title_without_number and title_without_number == candidate_without_number:
        return True
    if (
        title_without_number
        and candidate_without_number.startswith(title_without_number)
    ):
        return True

    title_words = _WORD.findall(title_without_number or title_key)
    candidate_words = _WORD.findall(candidate_without_number or candidate_key)
    # A heading may carry a number or a short explanatory suffix, but a long
    # paragraph that happens to mention the title is not a boundary.
    if len(candidate_words) > len(title_words) + 4:
        return False
    title_tokens = set(title_words)
    candidate_tokens = set(candidate_words)
    required = max(1, math.ceil(len(title_tokens) * 0.80))
    return len(title_tokens & candidate_tokens) >= required


def _heading_boundaries(
    page_elements: list,
    starting_sections: list[tuple[int, Section]],
) -> list[tuple[int, int]] | None:
    """Locate same-page section starts in extracted reading order."""

    boundaries: list[tuple[int, int]] = []
    search_from = 0
    for section_index, section in starting_sections:
        found: tuple[int, int] | None = None
        # Prefer a complete extracted block wherever possible. Trying joined
        # blocks first can mistake the final body line before a heading for
        # part of that heading.
        for element_index in range(search_from, len(page_elements)):
            text = getattr(page_elements[element_index], "text", None)
            if text and _heading_matches(section.title, str(text)):
                found = element_index, 1
                break

        # Some PDFs split a heading number and title into separate blocks on
        # the same visual line. Join only those spatially adjacent fragments.
        if found is None:
            for element_index in range(search_from, len(page_elements)):
                fragments: list[str] = []
                for width in range(1, 4):
                    stop = element_index + width
                    if stop > len(page_elements):
                        break
                    if width > 1 and not _elements_share_line(
                        page_elements[stop - 2],
                        page_elements[stop - 1],
                    ):
                        break
                    text = getattr(page_elements[stop - 1], "text", None)
                    if not text:
                        break
                    fragments.append(str(text))
                    if width > 1 and _heading_matches(
                        section.title,
                        " ".join(fragments),
                    ):
                        found = element_index, width
                        break
                if found is not None:
                    break
        if found is None:
            return None
        element_index, width = found
        boundaries.append((element_index, section_index))
        search_from = element_index + width
    return boundaries


def _element_box(element) -> tuple[float, float, float, float, float] | None:
    coordinates = getattr(element.metadata, "coordinates", None)
    points = getattr(coordinates, "points", None)
    system = getattr(coordinates, "system", None)
    width = getattr(system, "width", None)
    if not points or not isinstance(width, (int, float)) or width <= 0:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (IndexError, TypeError, ValueError):
        return None
    return min(xs), min(ys), max(xs), max(ys), float(width)


def _elements_share_line(one, two) -> bool:
    if getattr(one, "category", None) == "Title" and getattr(
        two, "category", None
    ) == "Title":
        # Wrapped display headings are often emitted as consecutive Title
        # blocks on separate visual lines. Restrict this relaxation to Title
        # blocks so a body paragraph cannot be joined to the next heading.
        return True
    one_box = _element_box(one)
    two_box = _element_box(two)
    if one_box is None or two_box is None:
        return False
    one_x0, one_y0, one_x1, one_y1, width = one_box
    two_x0, two_y0, _, two_y1, _ = two_box
    same_baseline = abs(one_y0 - two_y0) <= max(
        3.0,
        min(one_y1 - one_y0, two_y1 - two_y0) * 0.25,
    )
    horizontal_gap = two_x0 - one_x1
    nearby = -3.0 <= horizontal_gap <= width * 0.10
    return same_baseline and nearby


def _element_margin(element) -> str | None:
    coordinates = getattr(element.metadata, "coordinates", None)
    points = getattr(coordinates, "points", None)
    system = getattr(coordinates, "system", None)
    height = getattr(system, "height", None)
    if not points or not isinstance(height, (int, float)) or height <= 0:
        return None
    try:
        ys = [float(point[1]) for point in points]
    except (IndexError, TypeError, ValueError):
        return None
    if max(ys) / height <= MARGIN_HEADER_LIMIT:
        return "header"
    if min(ys) / height >= MARGIN_FOOTER_LIMIT:
        return "footer"
    return None


def _detected_boilerplate(elements: list) -> dict[int, str]:
    """Classify repeated margin text without deleting canonical blocks."""

    observations: dict[int, tuple[str, str]] = {}
    pages_by_value: dict[tuple[str, str], set[int]] = defaultdict(set)
    for element in elements:
        page = element.metadata.page_number
        text = getattr(element, "text", None)
        margin = _element_margin(element)
        if page is None or not text or margin is None:
            continue
        value = _normalized_text(str(text))
        if not value:
            continue
        observations[id(element)] = margin, value
        pages_by_value[(margin, value)].add(page)

    categories: dict[int, str] = {}
    for element_id, (margin, value) in observations.items():
        repeated = (
            len(pages_by_value[(margin, value)])
            >= MINIMUM_REPEATED_MARGIN_PAGES
        )
        page_number = margin == "footer" and bool(_PAGE_NUMBER.fullmatch(value))
        if repeated or page_number:
            categories[element_id] = (
                DETECTED_HEADER_CATEGORY
                if margin == "header"
                else DETECTED_FOOTER_CATEGORY
            )
    return categories


def _append_element(
    element,
    section: Section,
    *,
    category: str,
) -> None:
    page = element.metadata.page_number
    if isinstance(element, (Header, Footer, PageBreak)):
        return
    if not element.text and not isinstance(element, Image):
        return

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
            return
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
                category=category,
                page=page,
            )
        )


def assign_elements(elements, sections: list[Section]) -> list[int]:
    """Assign elements using page ranges and ordered same-page headings.

    A page number alone cannot distinguish sibling sections whose headings
    share that page, so the headings are located in extracted reading order
    and the page is split between them.

    When they cannot be located the page is not split, and the first section
    starting on it takes the whole page. That is exactly the precision the
    outline itself carries, and it is what every book ingested before same-page
    resolution existed already has. Refusing the book instead cost more than it
    bought: one unlocatable heading on one page rejected a 279-page book whose
    other 25 shared pages resolved perfectly.

    Returns the pages that could not be split, for the caller to record. A
    citation still lands on the right page; only sub-page attribution is lost.
    """

    if not sections:
        raise ValueError("cannot assign elements without sections")
    extracted = list(elements)
    boilerplate = _detected_boilerplate(extracted)
    by_page: dict[int, list] = defaultdict(list)
    first_content_page = sections[0].start_page
    for element in extracted:
        page = element.metadata.page_number
        if page is None or page < first_content_page:
            continue
        by_page[page].append(element)

    starts = [section.start_page for section in sections]
    collision_pages = {
        page
        for page in set(starts)
        if bisect_right(starts, page) - bisect_left(starts, page) > 1
    }
    unsplit_pages = sorted(collision_pages - by_page.keys())
    if unsplit_pages:
        logger.warning(
            "no extracted elements for outline collision page(s) %s; "
            "those pages keep outline-level precision",
            ", ".join(str(page) for page in unsplit_pages),
        )

    for page in sorted(by_page):
        page_elements = by_page[page]
        first_start = bisect_left(starts, page)
        after_start = bisect_right(starts, page)
        starting = [
            (index, sections[index])
            for index in range(first_start, after_start)
        ]

        if not starting:
            current_index = bisect_right(starts, page) - 1
            if current_index < 0:
                continue
            boundaries: dict[int, int] = {}
        else:
            located = _heading_boundaries(page_elements, starting)
            if located is None and len(starting) > 1:
                titles = ", ".join(repr(section.title) for _, section in starting)
                logger.warning(
                    "could not resolve %s outline headings sharing page %s "
                    "(%s); the page is not split",
                    len(starting),
                    page,
                    titles,
                )
                unsplit_pages.append(page)
            if located is None:
                # No split: the whole page starts the first section beginning
                # on it, which is no less precise than the raw outline.
                current_index = starting[0][0]
                boundaries = {}
            else:
                boundaries = dict(located)
                previous_index = first_start - 1
                current_index = (
                    previous_index if previous_index >= 0 else starting[0][0]
                )

        first_boundary = min(boundaries) if boundaries else None
        for element_index, element in enumerate(page_elements):
            if element_index in boundaries:
                current_index = boundaries[element_index]
            category = boilerplate.get(id(element), element.category)
            target_index = current_index
            if (
                first_boundary is not None
                and element_index < first_boundary
                and category in NON_CONTENT_CATEGORIES
            ):
                # A running header before the first heading should not extend
                # the previous section's page range.
                target_index = starting[0][0]
            elif (
                first_boundary is not None
                and element_index < first_boundary
                and current_index >= 0
            ):
                sections[current_index].end_page = max(
                    sections[current_index].end_page,
                    page,
                )
            if target_index < 0:
                continue
            _append_element(
                element,
                sections[target_index],
                category=category,
            )
    return sorted(set(unsplit_pages))


def parse_book(
    pdf_path: str | Path,
    *,
    force: bool = False,
    book_cache: str | Path = BOOK_CACHE,
    elements_cache: str | Path = ELEMENTS_CACHE,
    on_batch=None,
    toc_override: list[tuple[int, str, int]] | None = None,
) -> ParsedBook:
    """Return cached parser output or parse and cache the source PDF.

    ``toc_override`` is the preflight-approved outline. Supplying it keeps the
    expensive parser aligned with the exact normalized hierarchy that passed
    validation instead of re-reading untrusted publisher metadata and silently
    switching back to its raw form.
    """

    book_cache = Path(book_cache)
    if toc_override is None:
        selected_toc, page_count = extract_toc(pdf_path)
    else:
        selected_toc = list(toc_override)
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
    if not selected_toc:
        raise ValueError("the selected table of contents is empty")
    if book_cache.exists() and not force:
        cached = load_parsed_book(book_cache)
        if cached.source != str(pdf_path) or cached.toc != selected_toc:
            raise ValueError(
                "parsed-book cache does not match the source PDF; "
                "delete it or run with force=True"
            )
        return cached

    sections = build_sections(selected_toc, page_count)
    unsplit_pages = assign_elements(
        extract_elements(pdf_path, elements_cache, on_batch=on_batch), sections
    )
    if unsplit_pages:
        # Not stored on the book: it is derived from the parse, not part of the
        # canonical content, and ParsedBook round-trips losslessly out of
        # Postgres, which has no column for it.
        logger.warning(
            "%s of %s outline collision page(s) kept outline-level precision: %s",
            len(unsplit_pages),
            len({section.start_page for section in sections}),
            ", ".join(str(page) for page in unsplit_pages),
        )

    book = ParsedBook(source=str(pdf_path), toc=selected_toc, sections=sections)
    book_cache.parent.mkdir(parents=True, exist_ok=True)
    book_cache.write_text(book.model_dump_json(), encoding="utf-8")
    return book
