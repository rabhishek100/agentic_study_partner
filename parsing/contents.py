"""Read a book's own printed table of contents.

A publisher's embedded outline is metadata about the file. The contents page is
the book's own statement of its structure, typeset by the people who wrote it,
and for a scan it is often the only such statement that exists.

It is also the one page where transcription earns its cost outright. A contents
page is two columns — titles on the left, page numbers on the right — and plain
OCR reads it column by column, returning every title followed by every number
with the pairing destroyed. Measured on one of these books: the deterministic
engine produced thirteen chapter names and then thirteen page numbers, and no
amount of downstream cleverness recovers which went with which. The
transcription returns it as a table with the rows intact.

What comes out is still a *proposal*. The printed page numbers it carries have
to be mapped onto PDF pages through a measured offset, and a reviewer confirms
the result before anything is parsed against it.
"""

from dataclasses import dataclass
from html.parser import HTMLParser
import re

from .markup import parse_page_markup


__all__ = [
    "ContentsEntry",
    "PrintedContents",
    "find_contents_pages",
    "parse_printed_contents",
]

# A contents page names itself. Both spellings appear in this corpus.
_CONTENTS_HEADING = re.compile(
    r"^#{1,6}\s*(?:table\s+of\s+)?contents\s*$", re.IGNORECASE | re.MULTILINE
)

# "Chapter 1 Proximity Service", "Chapter 1. Machine Learning Basics"
_CHAPTER = re.compile(r"^\s*chapter\s+(\d+)\s*[.:]?\s*(?P<rest>.*)$", re.IGNORECASE)
_PART = re.compile(r"^\s*part\s+(?:[ivxlcdm]+|\d+)\b", re.IGNORECASE)
_APPENDIX = re.compile(r"^\s*appendix\b", re.IGNORECASE)
# "1.2. Model", "4.2.3 Step 3 of Self-Attention"
_DOTTED = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)+)\.?\s+(?P<rest>\S.*)$")
_BARE_NUMBER = re.compile(r"^\s*(?P<number>\d+)\.?\s+(?P<rest>\S.*)$")

# A line of the form "Title .......... 42", "Title    42", or "Title 42".
_LEADER_LINE = re.compile(
    r"^(?P<title>.*?\S)[\s.·•…_-]{2,}(?P<page>[ivxlcdm]+|\d{1,4})\s*$",
    re.IGNORECASE,
)
_TRAILING_NUMBER = re.compile(
    r"^(?P<title>.*?\S)\s+(?P<page>[ivxlcdm]+|\d{1,4})\s*$", re.IGNORECASE
)
_ROMAN = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HEADING_LINE = re.compile(r"^\s*#{1,6}\s+")

# Below this a "contents page" is a mention of the word, not a listing.
MINIMUM_CONTENTS_ENTRIES = 3
# A contents listing runs over at most this many consecutive pages. Beyond it
# the match is picking up body text that happens to end in numbers.
MAXIMUM_CONTENTS_PAGES = 6


@dataclass(frozen=True)
class ContentsEntry:
    """One row of a printed contents listing, in the book's own numbering."""

    level: int
    title: str
    printed_page: int
    roman: bool = False


@dataclass(frozen=True)
class PrintedContents:
    """A book's printed structure, before it is mapped onto PDF pages."""

    entries: tuple[ContentsEntry, ...] = ()
    source_pages: tuple[int, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.entries)

    def provenance(self) -> dict[str, object]:
        return {
            "entries": len(self.entries),
            "source_pages": list(self.source_pages),
            "roman_entries": sum(1 for entry in self.entries if entry.roman),
        }


class _Rows(HTMLParser):
    """Collect table rows as lists of cell strings."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _page_value(token: str) -> tuple[int, bool] | None:
    """Read a printed page number, arabic or roman."""

    stripped = token.strip()
    if stripped.isdigit():
        value = int(stripped)
        return (value, False) if 1 <= value <= 9999 else None
    if not _ROMAN.match(stripped):
        return None
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    previous = 0
    for character in reversed(stripped.lower()):
        current = values[character]
        total += -current if current < previous else current
        previous = max(previous, current)
    return (total, True) if total else None


def _entry_level(title: str) -> int:
    """The depth a contents title declares about itself.

    Read from the numbering the book prints, which is the only depth signal a
    contents listing reliably carries: indentation survives neither OCR nor
    transcription, and a table cell has none to begin with.
    """

    if _PART.match(title):
        return 1
    if _APPENDIX.match(title) or _CHAPTER.match(title):
        return 1
    dotted = _DOTTED.match(title)
    if dotted is not None:
        return min(1 + dotted.group("number").count("."), 6)
    return 1


def _clean_title(title: str) -> str:
    text = " ".join(title.split())
    text = _HEADING_LINE.sub("", text)
    text = _LIST_MARKER.sub("", text)
    return text.strip(" .·•…_-\t")


def _rows_from_tables(body: str) -> list[tuple[str, str]]:
    """Title/page pairs from the table form a transcription usually produces."""

    pairs: list[tuple[str, str]] = []
    for match in re.finditer(r"<table\b.*?</table>", body, re.DOTALL | re.IGNORECASE):
        parser = _Rows()
        parser.feed(match.group(0))
        parser.close()
        for row in parser.rows:
            cells = [cell for cell in row if cell]
            if len(cells) < 2:
                continue
            # The page number is the last cell that is only a number; the title
            # is everything before it. A three-column listing puts the chapter
            # label and its name in separate cells.
            if _page_value(cells[-1]) is None:
                continue
            pairs.append((" ".join(cells[:-1]), cells[-1]))
    return pairs


def _rows_from_lines(body: str) -> list[tuple[str, str]]:
    """Title/page pairs from the dotted-leader form."""

    pairs: list[tuple[str, str]] = []
    without_tables = re.sub(
        r"<table\b.*?</table>", " ", body, flags=re.DOTALL | re.IGNORECASE
    )
    for line in without_tables.splitlines():
        stripped = line.strip()
        if not stripped or _CONTENTS_HEADING.match(stripped):
            continue
        match = _LEADER_LINE.match(stripped) or _TRAILING_NUMBER.match(stripped)
        if match is None:
            continue
        if _page_value(match.group("page")) is None:
            continue
        pairs.append((match.group("title"), match.group("page")))
    return pairs


def find_contents_pages(pages: list[tuple[int, str]]) -> list[int]:
    """Pages that carry the printed contents listing.

    A listing runs over consecutive pages, so once one is found its immediate
    successors are admitted on evidence of rows alone. A "Contents" heading
    only appears on the first.
    """

    found: list[int] = []
    for page, text in pages:
        body = parse_page_markup(text).body
        rows = _rows_from_tables(body) + _rows_from_lines(body)
        heading = _CONTENTS_HEADING.search(body) is not None
        continuation = bool(found) and page == found[-1] + 1

        if heading and len(rows) >= MINIMUM_CONTENTS_ENTRIES:
            found = [page]
        elif continuation and len(rows) >= MINIMUM_CONTENTS_ENTRIES:
            found.append(page)
        elif found and not continuation:
            break
        if len(found) >= MAXIMUM_CONTENTS_PAGES:
            break
    return found


def parse_printed_contents(pages: list[tuple[int, str]]) -> PrintedContents:
    """Read the book's printed structure off its contents pages."""

    ordered = sorted(dict(pages).items())
    contents_pages = find_contents_pages(ordered)
    if not contents_pages:
        return PrintedContents()

    by_page = dict(ordered)
    entries: list[ContentsEntry] = []
    for page in contents_pages:
        body = parse_page_markup(by_page[page]).body
        for raw_title, raw_page in _rows_from_tables(body) + _rows_from_lines(body):
            title = _clean_title(raw_title)
            value = _page_value(raw_page)
            if not title or value is None:
                continue
            # The listing's own heading is not one of its rows.
            if title.casefold() in {"contents", "table of contents"}:
                continue
            printed, roman = value
            entries.append(
                ContentsEntry(
                    level=_entry_level(title),
                    title=title,
                    printed_page=printed,
                    roman=roman,
                )
            )

    if len(entries) < MINIMUM_CONTENTS_ENTRIES:
        return PrintedContents()
    return PrintedContents(
        entries=tuple(entries), source_pages=tuple(contents_pages)
    )


def contents_to_outline(
    contents: PrintedContents,
    *,
    numbering,
    page_count: int,
) -> tuple[list[tuple[int, str, int]], list[str]]:
    """Map a printed listing onto PDF pages, and say what could not be mapped.

    Entries whose printed number belongs to a scheme with no measured offset
    are dropped rather than guessed at, and named in the warnings so a reviewer
    can place them by hand. Inventing a page for a front-matter entry would put
    a citation on the wrong page, which is the failure this whole path exists
    to avoid.
    """

    outline: list[tuple[int, str, int]] = []
    warnings: list[str] = []
    unmapped: list[str] = []
    previous_page = 1
    depths: list[int] = []

    for entry in contents.entries:
        pdf_page = numbering.pdf_page(entry.printed_page, roman=entry.roman)
        if pdf_page is None or not 1 <= pdf_page <= page_count:
            unmapped.append(entry.title)
            continue
        # Levels must not jump, and a listing may open at any depth.
        while depths and depths[-1] > entry.level:
            depths.pop()
        if not depths or depths[-1] < entry.level:
            depths.append(entry.level)
        level = depths.index(entry.level) + 1

        page = max(pdf_page, previous_page)
        previous_page = page
        outline.append((level, entry.title, page))

    if unmapped:
        warnings.append(
            f"{len(unmapped)} contents entries could not be placed and were "
            f"left out: {', '.join(unmapped[:6])}"
            + ("..." if len(unmapped) > 6 else "")
        )
    return outline, warnings
