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
    "PrintedNumbering",
    "build_transcribed_book",
    "printed_numbering",
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

# Two pages must agree before an offset is believed. On a single page every
# integer in the margin ties, so the winner would be whichever sorted first.
MINIMUM_OFFSET_VOTES = 2


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


@dataclass(frozen=True)
class PrintedNumbering:
    """How a book's printed page numbers line up with its PDF pages."""

    offset: int | None
    matched_pages: int
    sampled_pages: int
    roman_pages: int = 0

    @property
    def confidence(self) -> float:
        if not self.sampled_pages:
            return 0.0
        return self.matched_pages / self.sampled_pages

    def printed(self, pdf_page: int) -> int | None:
        """The number printed on a PDF page, if the offset is known."""

        if self.offset is None:
            return None
        printed = pdf_page - self.offset
        return printed if printed >= 1 else None

    def provenance(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "matched_pages": self.matched_pages,
            "sampled_pages": self.sampled_pages,
            "roman_pages": self.roman_pages,
            "confidence": round(self.confidence, 4),
        }


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


def printed_numbering(pages: Iterable[tuple[int, str]]) -> PrintedNumbering:
    """Infer the printed-page offset from the running margins.

    A citation names the page a reader sees, not the index of a byte range in a
    PDF, and for a scan those differ by however much front matter the scanner
    included. The offset is *measured* across pages and agreed on by a majority
    rather than assumed from where chapter one lands.

    Only arabic numerals vote. Front matter is commonly numbered in roman and
    restarts at 1 when the body begins, so admitting roman pages would put two
    incompatible offsets in the same tally. They are counted and reported, so a
    reviewer can see that the front matter is separately numbered.

    Every integer in the margin proposes an offset, and the offset agreed on by
    the most pages wins. That is what separates a page number from the other
    numbers printed beside it: a real page number advances in step with the
    page, so its offset is constant, while a chapter number stays put and its
    implied offset drifts by one on every page.

    The offset can be negative. One scan in this corpus was made from a copy
    with its front matter removed, so its printed numbers run *ahead* of the
    PDF index — printed 288 on PDF page 280. An earlier version assumed a
    printed number could never exceed its PDF page, which ruled the true number
    out and elected the chapter number instead.
    """

    votes: dict[int, int] = {}
    sampled = 0
    roman = 0

    for page, text in pages:
        markup = parse_page_markup(text)
        margin = f"{markup.footer} {markup.header}".strip()
        if not margin:
            continue
        sampled += 1

        candidates = {int(value) for value in _ARABIC_PAGE.findall(margin)}
        if not candidates:
            if any(
                _roman_value(token) is not None
                for token in _ROMAN_PAGE.findall(margin)
            ):
                roman += 1
            continue
        # One vote per page per distinct offset, so a number repeated in a
        # margin cannot outweigh a page that names it once.
        for value in candidates:
            if value < 1:
                continue
            votes[page - value] = votes.get(page - value, 0) + 1

    # A single page cannot establish an offset: every integer on it ties, and
    # the winner would be whichever number happened to sort first.
    agreed = {
        offset: count for offset, count in votes.items() if count >= MINIMUM_OFFSET_VOTES
    }
    if not agreed:
        return PrintedNumbering(
            offset=None, matched_pages=0, sampled_pages=sampled, roman_pages=roman
        )

    offset, matched = max(agreed.items(), key=lambda item: (item[1], -abs(item[0])))
    return PrintedNumbering(
        offset=offset,
        matched_pages=matched,
        sampled_pages=sampled,
        roman_pages=roman,
    )
