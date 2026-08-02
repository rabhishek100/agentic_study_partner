"""Turn page transcriptions into the canonical block model.

The generative stage produced Markdown with explicit markup for the things that
matter structurally — headings, tables, figures, running margins. This module
reads that markup and nothing else. It never looks at the pixels again, so
every structural decision here is deterministic, re-runnable, and explainable
without reference to a model call.

What comes out is the same `ParsedBook` the PDF parser produces, so persistence,
chunking, retrieval, and citation are shared rather than forked.

One asymmetry with the PDF parser is worth naming. That parser has to *locate*
a heading among positioned elements to split a page between two sections, and
sometimes cannot. Here a heading is marked as a heading, so the split is a
string comparison. Transcription loses a great deal of layout; it keeps the one
thing sectioning actually needs.
"""

from base64 import b64encode
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
import logging
import re
import unicodedata
from pathlib import Path

import fitz

from .markup import FigureRegion, parse_page_markup
from .models import (
    DETECTED_FOOTER_CATEGORY,
    DETECTED_HEADER_CATEGORY,
    ImageBlock,
    ParsedBook,
    Section,
    TableBlock,
    TextBlock,
)


logger = logging.getLogger("study_partner.parsing.transcript")

__all__ = [
    "PageAnchor",
    "PrintedNumbering",
    "build_transcribed_book",
    "printed_numbering",
    "table_to_text",
]

TITLE = "Title"
NARRATIVE = "NarrativeText"
LIST_ITEM = "ListItem"

# Figures are cropped at this density. Captions are produced from the crop and
# a reader may open it, so it has to survive being looked at; the sources
# themselves only carry 110-160 dpi, and rendering past 200 enlarges the stored
# payload without adding detail.
FIGURE_RENDER_DPI = 200
FIGURE_JPEG_QUALITY = 85

# A crop smaller than this is a rule, a bullet glyph, or a misplaced box rather
# than a figure worth storing.
MINIMUM_FIGURE_FRACTION = 0.01

_TABLE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>\S.*?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

_ARABIC_PAGE = re.compile(r"\b(\d{1,4})\b")
_ROMAN_PAGE = re.compile(r"\b([ivxlcdm]{1,7})\b", re.IGNORECASE)

# One anchor is a coincidence: any integer in any margin produces one, and a
# whole book extrapolated from it is how a chapter number becomes a page
# number. Two that behave like page numbers relative to each other are not.
MINIMUM_ANCHORS = 2

# How fast a printed page number may move relative to the PDF page. One is the
# ideal; a scan that skipped pages runs a little above it and one that caught a
# page twice a little below. The band is deliberately wide, because its job is
# only to exclude numbers that are not page numbers at all: a chapter number
# advances by one every thirty pages, a rate of 0.03.
MINIMUM_RATE = 0.5
MAXIMUM_RATE = 2.0


def _roman_value(token: str) -> int | None:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    previous = 0
    for character in reversed(token.lower()):
        value = values.get(character)
        if value is None:
            return None
        total += -value if value < previous else value
        previous = max(previous, value)
    return total or None


class _TableText(HTMLParser):
    """Flatten table markup into the plain-text fallback the model requires."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[list[str]] = []
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._rows.append([])
        elif tag in {"td", "th"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            if not self._rows:
                self._rows.append([])
            self._rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = []

    def handle_data(self, data: str) -> None:
        self._cell.append(data)

    @property
    def text(self) -> str:
        return "\n".join(
            " | ".join(cell for cell in row) for row in self._rows if any(row)
        )


def table_to_text(html: str) -> str:
    """A readable rendering of a table, for search and for a text-only reader."""

    parser = _TableText()
    parser.feed(html)
    parser.close()
    return parser.text


@dataclass(frozen=True)
class _Piece:
    """One block of a page, in reading order."""

    kind: str
    text: str
    html: str | None = None


def _split_body(body: str) -> list[_Piece]:
    """Break one page's Markdown into ordered blocks.

    Tables are extracted first because their markup spans lines and would
    otherwise be shredded by paragraph splitting.
    """

    pieces: list[_Piece] = []
    position = 0
    for match in _TABLE.finditer(body):
        pieces.extend(_split_prose(body[position : match.start()]))
        html = match.group(0)
        pieces.append(_Piece(kind="table", text=table_to_text(html), html=html))
        position = match.end()
    pieces.extend(_split_prose(body[position:]))
    return pieces


def _split_prose(text: str) -> list[_Piece]:
    pieces: list[_Piece] = []
    paragraph: list[str] = []

    def flush() -> None:
        joined = "\n".join(paragraph).strip()
        paragraph.clear()
        if joined:
            pieces.append(_Piece(kind="text", text=joined))

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        heading = _HEADING.match(stripped)
        if heading is not None:
            flush()
            pieces.append(
                _Piece(kind="heading", text=" ".join(heading.group("title").split()))
            )
            continue
        if _LIST_ITEM.match(line):
            # Each item is its own block, matching what the PDF parser emits,
            # so a chunk boundary can fall between items rather than only
            # between whole lists.
            flush()
            pieces.append(_Piece(kind="list", text=stripped))
            continue
        paragraph.append(stripped)
    flush()
    return pieces


def _comparable(title: str) -> str:
    """Normalize a heading for comparison against an outline entry."""

    folded = unicodedata.normalize("NFKD", title).casefold()
    return " ".join(_WORD.findall(folded))


def _heading_matches(heading: str, title: str) -> bool:
    """Whether a page heading is the outline entry it is supposed to open.

    Compared on words rather than characters. An outline entry and the printed
    heading it points at routinely disagree about punctuation, dashes, and
    whether the number is joined to the title, and none of that changes which
    section a page belongs to.
    """

    left, right = _comparable(heading), _comparable(title)
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def _crop_figure(
    document: fitz.Document, page: int, figure: FigureRegion
) -> tuple[bytes, str] | None:
    """Render the region a figure occupies, or the whole page if unlocated."""

    source = document[page - 1]
    rect = source.rect
    if figure.bbox is not None:
        left, top, right, bottom = figure.bbox
        if (right - left) * (bottom - top) < MINIMUM_FIGURE_FRACTION:
            return None
        clip = fitz.Rect(
            rect.x0 + left * rect.width,
            rect.y0 + top * rect.height,
            rect.x0 + right * rect.width,
            rect.y0 + bottom * rect.height,
        )
    else:
        clip = rect
    try:
        pixmap = source.get_pixmap(dpi=FIGURE_RENDER_DPI, clip=clip)
    except Exception:  # noqa: BLE001 - a bad box must not lose the page
        logger.warning("could not crop figure on page %s", page, exc_info=True)
        return None
    if not pixmap.width or not pixmap.height:
        return None
    return pixmap.tobytes("jpeg", jpg_quality=FIGURE_JPEG_QUALITY), "image/jpeg"


def _append_margin(section: Section, text: str, *, page: int, category: str) -> None:
    if text:
        section.texts.append(TextBlock(text=text, category=category, page=page))


def build_transcribed_book(
    source: str | Path,
    *,
    toc: list[tuple[int, str, int]],
    pages: Iterable[tuple[int, str]],
    page_count: int | None = None,
) -> ParsedBook:
    """Assemble a canonical book from a confirmed outline and page transcriptions.

    ``toc`` is the outline a reviewer confirmed. Nothing here may alter it: the
    hierarchy is the one thing about a scanned book a human vouched for, and
    quietly improving it would make the confirmation meaningless.
    """

    from .parser import build_sections

    transcribed = sorted(dict(pages).items())
    if not toc:
        raise ValueError("cannot build a book without a confirmed outline")
    if not transcribed:
        raise ValueError("cannot build a book without transcribed pages")

    with fitz.open(source) as document:
        total = page_count or document.page_count
        sections = build_sections(toc, total)
        starts = [section.start_page for section in sections]
        current = 0
        # Pages before the first outline entry belong to no section. The PDF
        # parser drops them the same way, and the preflight warning about
        # content preceding the outline already covers it.
        first_page = starts[0]

        for page, text in transcribed:
            if page < first_page or page > total:
                continue
            markup = parse_page_markup(text)

            # Sections whose entry points at this page, in outline order. The
            # first is claimed by its heading if that heading appears; until
            # then the page continues whatever section was already open.
            pending = [
                index
                for index in range(len(sections))
                if sections[index].start_page == page
            ]
            if pending and page == first_page:
                current = pending.pop(0)

            _append_margin(
                sections[current],
                markup.header,
                page=page,
                category=DETECTED_HEADER_CATEGORY,
            )

            for piece in _split_body(markup.body):
                if piece.kind == "heading" and pending:
                    if _heading_matches(piece.text, sections[pending[0]].title):
                        current = pending.pop(0)
                section = sections[current]
                if piece.kind == "table":
                    index = len(section.tables)
                    section.texts.append(
                        TextBlock(
                            text=f"[TABLE {index}]",
                            category="TablePlaceholder",
                            page=page,
                        )
                    )
                    section.tables.append(
                        TableBlock(html=piece.html, text=piece.text, page=page)
                    )
                    continue
                category = {
                    "heading": TITLE,
                    "list": LIST_ITEM,
                }.get(piece.kind, NARRATIVE)
                section.texts.append(
                    TextBlock(text=piece.text, category=category, page=page)
                )

            for figure in markup.figures:
                cropped = _crop_figure(document, page, figure)
                if cropped is None:
                    continue
                payload, mime = cropped
                section = sections[current]
                index = len(section.images)
                section.texts.append(
                    TextBlock(
                        text=f"[IMAGE {index}]",
                        category="ImagePlaceholder",
                        page=page,
                    )
                )
                section.images.append(
                    ImageBlock(
                        base64=b64encode(payload).decode("ascii"),
                        mime=mime,
                        page=page,
                    )
                )
                if figure.caption:
                    section.texts.append(
                        TextBlock(
                            text=figure.caption, category=NARRATIVE, page=page
                        )
                    )

            _append_margin(
                sections[current],
                markup.footer,
                page=page,
                category=DETECTED_FOOTER_CATEGORY,
            )

            # An outline entry whose heading never appeared on its page still
            # owns the rest of the book from here; leaving it unopened would
            # attribute its content to the section above it.
            for index in pending:
                current = max(current, index)

    return ParsedBook(source=str(source), toc=list(toc), sections=sections)




# --- printed page numbering -------------------------------------------------

@dataclass(frozen=True)
class PageAnchor:
    """One page whose printed number was read off its margin."""

    pdf_page: int
    printed: int
    roman: bool = False


@dataclass(frozen=True)
class PrintedNumbering:
    """How a book's printed page numbers line up with its PDF pages.

    Not an offset. Measured on a phone-scanned book in this corpus, the offset
    runs from 8 near the front to 1 at the back: seven printed pages are simply
    absent from the scan. A single global offset placed that book's last
    chapter seven pages wrong, and a citation seven pages wrong is worse than
    no citation, because it looks right.

    So the mapping is a list of anchors — pages whose printed number was
    actually read — and anything between them is interpolated. Roman front
    matter is anchored separately, because it restarts at arabic 1 when the
    body begins.
    """

    anchors: tuple[PageAnchor, ...] = ()
    sampled_pages: int = 0

    @property
    def arabic(self) -> tuple[PageAnchor, ...]:
        return tuple(anchor for anchor in self.anchors if not anchor.roman)

    @property
    def roman(self) -> tuple[PageAnchor, ...]:
        return tuple(anchor for anchor in self.anchors if anchor.roman)

    @property
    def matched_pages(self) -> int:
        return len(self.anchors)

    @property
    def confidence(self) -> float:
        if not self.sampled_pages:
            return 0.0
        return self.matched_pages / self.sampled_pages

    @property
    def offset_range(self) -> tuple[int, int] | None:
        """The span of offsets observed, which is how page loss shows up."""

        offsets = [anchor.pdf_page - anchor.printed for anchor in self.arabic]
        return (min(offsets), max(offsets)) if offsets else None

    @property
    def drifts(self) -> bool:
        span = self.offset_range
        return span is not None and span[1] > span[0]

    def printed(self, pdf_page: int) -> int | None:
        """The number printed on a PDF page."""

        return _interpolate(
            self.arabic,
            pdf_page,
            key=lambda anchor: anchor.pdf_page,
            value=lambda anchor: anchor.printed,
        )

    def pdf_page(self, printed: int, *, roman: bool = False) -> int | None:
        """The PDF page a printed number refers to, in its own scheme."""

        anchors = self.roman if roman else self.arabic
        return _interpolate(
            anchors,
            printed,
            key=lambda anchor: anchor.printed,
            value=lambda anchor: anchor.pdf_page,
        )

    def as_stored(self) -> list[dict[str, object]]:
        """The anchors, small enough to keep on the book itself.

        A citation names the page a reader sees, so the mapping has to outlive
        the ingestion job that measured it.
        """

        return [
            {"pdf": anchor.pdf_page, "printed": anchor.printed, "roman": anchor.roman}
            for anchor in self.anchors
        ]

    @classmethod
    def from_stored(
        cls, anchors: list[dict] | None, *, sampled_pages: int = 0
    ) -> "PrintedNumbering":
        """Rebuild a mapping from what was stored with the book."""

        if not anchors:
            return cls()
        return cls(
            anchors=tuple(
                PageAnchor(
                    pdf_page=int(entry["pdf"]),
                    printed=int(entry["printed"]),
                    roman=bool(entry.get("roman")),
                )
                for entry in anchors
            ),
            sampled_pages=sampled_pages or len(anchors),
        )

    def provenance(self) -> dict[str, object]:
        span = self.offset_range
        return {
            "anchors": self.matched_pages,
            "arabic_anchors": len(self.arabic),
            "roman_anchors": len(self.roman),
            "sampled_pages": self.sampled_pages,
            "confidence": round(self.confidence, 4),
            "offset_range": list(span) if span else None,
            "drifts": self.drifts,
        }


def _interpolate(anchors, target: int, *, key, value) -> int | None:
    """Read a value between two anchors, or extrapolate past the ends.

    Extrapolation holds the nearest anchor's offset constant. Past the last
    measured page that is the only defensible assumption available, and it is
    right whenever no further pages are missing.
    """

    if not anchors:
        return None
    ordered = sorted(anchors, key=key)
    if target <= key(ordered[0]):
        result = value(ordered[0]) - (key(ordered[0]) - target)
    elif target >= key(ordered[-1]):
        result = value(ordered[-1]) + (target - key(ordered[-1]))
    else:
        result = None
        for lower, upper in zip(ordered, ordered[1:], strict=False):
            if key(lower) <= target <= key(upper):
                if target == key(lower):
                    result = value(lower)
                elif target == key(upper):
                    result = value(upper)
                else:
                    # Straight-line between the two, which assumes whatever
                    # pages are missing are missing evenly across the gap.
                    span = key(upper) - key(lower)
                    rise = value(upper) - value(lower)
                    result = value(lower) + round(rise * (target - key(lower)) / span)
                break
    return result if result is not None and result >= 1 else None


def _longest_consistent_chain(
    candidates: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Pick the reading of the margins that behaves like a page number.

    Every integer in a margin is a candidate, and a footer commonly holds two:
    "308 | Chapter 10" offers both the page and the chapter. They are told
    apart by how fast they move. A printed page number advances at roughly the
    rate the PDF page does — a little faster where the scan skipped pages, a
    little slower where it caught one twice. A chapter number advances by one
    every thirty pages, and no plausible rate band contains both.

    The rate band is what does the work here, not the direction. An earlier
    version required a page number never to gain more than one per PDF page,
    on the reasoning that a scan can omit pages but not invent them. That is
    backwards: omitting a page makes the printed number advance *faster*, and
    the rule rejected every genuine anchor in the second half of the book while
    admitting the chapter numbers, which drift slowly enough to look valid.
    """

    if not candidates:
        return []
    ordered = sorted(set(candidates))
    best_length = [1] * len(ordered)
    previous = [-1] * len(ordered)

    for index in range(len(ordered)):
        pdf, printed = ordered[index]
        for earlier in range(index):
            prior_pdf, prior_printed = ordered[earlier]
            advance = printed - prior_printed
            span = pdf - prior_pdf
            if advance <= 0 or span <= 0:
                continue
            if not MINIMUM_RATE * span <= advance <= MAXIMUM_RATE * span:
                continue
            if best_length[earlier] + 1 > best_length[index]:
                best_length[index] = best_length[earlier] + 1
                previous[index] = earlier

    end = max(range(len(ordered)), key=lambda index: best_length[index])
    chain: list[tuple[int, int]] = []
    while end != -1:
        chain.append(ordered[end])
        end = previous[end]
    return list(reversed(chain))


def printed_numbering(pages: Iterable[tuple[int, str]]) -> PrintedNumbering:
    """Map printed page numbers onto PDF pages, from the running margins.

    A citation names the page a reader sees. For a scan that differs from the
    PDF index by however much front matter was included *and* by whatever pages
    the scanner missed, so the relationship is measured page by page rather
    than assumed to be one number.

    Roman and arabic numerals are chained separately: front matter restarts at
    arabic 1 when the body begins, so one sequence containing both is not
    monotonic and could not be chained at all.
    """

    arabic: list[tuple[int, int]] = []
    roman: list[tuple[int, int]] = []
    sampled = 0

    for page, text in pages:
        markup = parse_page_markup(text)
        margin = f"{markup.footer} {markup.header}".strip()
        if not margin:
            continue
        sampled += 1

        numbers = {int(value) for value in _ARABIC_PAGE.findall(margin)}
        if numbers:
            arabic.extend((page, value) for value in numbers if value >= 1)
            continue
        for token in _ROMAN_PAGE.findall(margin):
            value = _roman_value(token)
            if value is not None:
                roman.append((page, value))

    anchors = [
        PageAnchor(pdf_page=pdf, printed=printed)
        for pdf, printed in _longest_consistent_chain(arabic)
    ]
    anchors.extend(
        PageAnchor(pdf_page=pdf, printed=printed, roman=True)
        for pdf, printed in _longest_consistent_chain(roman)
    )
    # A single anchor is a coincidence: any integer in any margin produces one,
    # and extrapolating a whole book from it is how a chapter number becomes a
    # page number.
    if len(anchors) < MINIMUM_ANCHORS:
        return PrintedNumbering(anchors=(), sampled_pages=sampled)
    return PrintedNumbering(anchors=tuple(anchors), sampled_pages=sampled)
