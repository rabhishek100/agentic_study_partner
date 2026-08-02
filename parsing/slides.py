"""Recover the structure of a slide deck from its own typography.

A deck is a book-shaped thing the outline machinery handles badly. It has no
embedded outline, so it falls to the span-heading proposer — which reads every
enlarged line as a heading and, on a 550-slide course, produced 500 flat
entries including bullet fragments before hitting its cap, leaving the last 141
slides unreachable.

The structure is nevertheless completely determined, and by plain typography
rather than anything a model needs to read. A deck repeats a footer on every
slide, and a course-style deck numbers its sections there. Each slide carries
exactly one line at the largest size on the page: its title. Both signals are
measurable, so this is deterministic in the sense `AGENTS.md` means it.

What comes out is *sections*, not slides. One entry per slide would be 550
review rows and a citation unit of 370 characters; the sections are what the
deck is actually organised by, and the existing token-based chunker already
packs six or seven slides into a chunk without being told they are slides.
"""

from collections import Counter
from dataclasses import dataclass
import logging
import re

import fitz


logger = logging.getLogger("study_partner.parsing.slides")

__all__ = ["SlideProfile", "profile_slides", "synthesize_slide_outline"]

# A slide is wider than it is tall. Every deck in this corpus is 4:3 or 16:9;
# no paginated book is landscape.
MINIMUM_LANDSCAPE_RATIO = 0.90
# Slides carry a fraction of a book page's text. Measured on the deck: 368
# characters a page against 1,554 for a prose book.
MAXIMUM_MEDIAN_CHARACTERS = 900
# A deck titles its slides. Below this the largest-font heuristic is measuring
# noise rather than a layout convention.
MINIMUM_TITLED_RATIO = 0.80
# The title must actually stand out; a page whose largest text is body size has
# no title.
TITLE_SIZE_MULTIPLE = 1.4

# The section marker a course deck prints in its footer: "3-", "3 -", "3.".
_SECTION_MARKER = re.compile(r"^\s*(\d{1,2})\s*[-–—.]")
# Where a footer lives, as a fraction of page height.
FOOTER_BAND = 0.86

# Two sections is a coincidence of numbering; three is a scheme. The same
# threshold `outline_roles` uses to believe a chapter run.
MINIMUM_SECTIONS = 3


@dataclass(frozen=True)
class SlideProfile:
    """Measured evidence that a document is a deck, and how it is titled."""

    pages: int
    landscape_pages: int
    titled_pages: int
    median_characters: int
    body_size: float
    title_size: float

    @property
    def landscape_ratio(self) -> float:
        return self.landscape_pages / self.pages if self.pages else 0.0

    @property
    def titled_ratio(self) -> float:
        return self.titled_pages / self.pages if self.pages else 0.0

    @property
    def is_deck(self) -> bool:
        return (
            self.pages > 0
            and self.landscape_ratio >= MINIMUM_LANDSCAPE_RATIO
            and self.titled_ratio >= MINIMUM_TITLED_RATIO
            and self.median_characters <= MAXIMUM_MEDIAN_CHARACTERS
        )

    def provenance(self) -> dict[str, object]:
        return {
            "pages": self.pages,
            "landscape_ratio": round(self.landscape_ratio, 4),
            "titled_ratio": round(self.titled_ratio, 4),
            "median_characters": self.median_characters,
            "body_size": round(self.body_size, 2),
            "title_size": round(self.title_size, 2),
            "is_deck": self.is_deck,
        }


def _spans(page: fitz.Page) -> list[dict]:
    collected: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span["text"].strip():
                    collected.append(span)
    return collected


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def profile_slides(document: fitz.Document) -> SlideProfile:
    """Measure whether this document behaves like a slide deck."""

    landscape = 0
    characters: list[int] = []
    sizes: Counter[float] = Counter()
    page_maximum: list[float] = []

    for page in document:
        if page.rect.width > page.rect.height:
            landscape += 1
        characters.append(len(page.get_text("text")))
        spans = _spans(page)
        for span in spans:
            sizes[round(span["size"], 1)] += len(span["text"])
        page_maximum.append(max((span["size"] for span in spans), default=0.0))

    # Body size is whichever size the most *characters* are set in; a title
    # occupies few characters however large it is.
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    title = _median([size for size in page_maximum if size > 0]) if page_maximum else 0.0
    titled = sum(
        1 for size in page_maximum if body and size >= body * TITLE_SIZE_MULTIPLE
    )

    return SlideProfile(
        pages=document.page_count,
        landscape_pages=landscape,
        titled_pages=titled,
        median_characters=int(_median([float(count) for count in characters])),
        body_size=body,
        title_size=title,
    )


def _page_title(page: fitz.Page, *, body_size: float) -> str:
    """The one line set at the largest size on the slide."""

    spans = _spans(page)
    if not spans:
        return ""
    largest = max(span["size"] for span in spans)
    if body_size and largest < body_size * TITLE_SIZE_MULTIPLE:
        return ""
    words = [
        span["text"]
        for span in spans
        if abs(span["size"] - largest) < 0.5
    ]
    return " ".join(" ".join(words).split())


def _section_marker(page: fitz.Page) -> int | None:
    """The section number printed in the slide's footer, if it prints one."""

    threshold = page.rect.y0 + page.rect.height * FOOTER_BAND
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0 or block["bbox"][1] < threshold:
            continue
        text = " ".join(
            span["text"] for line in block["lines"] for span in line["spans"]
        )
        match = _SECTION_MARKER.match(text.strip())
        if match:
            return int(match.group(1))
    return None


def synthesize_slide_outline(
    document: fitz.Document,
) -> list[tuple[int, str, int]] | None:
    """Return one entry per section of a deck, or None when it is not one.

    The section's title is the title of the slide that opens it, which is the
    convention a course deck follows: a section begins with a slide naming it.

    Returning None rather than a rough answer is deliberate. The caller already
    has a general proposer; a wrong deck structure would be harder to review
    than an honestly generic one.
    """

    profile = profile_slides(document)
    if not profile.is_deck:
        logger.info("not a slide deck: %s", profile.provenance())
        return None

    markers: list[tuple[int, int | None]] = []
    for index, page in enumerate(document, start=1):
        markers.append((index, _section_marker(page)))

    numbered = [(page, marker) for page, marker in markers if marker is not None]
    if not numbered:
        logger.info("deck has no footer section markers; no outline synthesized")
        return None

    # A section is a contiguous run of pages sharing a marker. Markers must not
    # go backwards: a deck that renumbers is not sectioned the way this assumes.
    entries: list[tuple[int, str, int]] = []
    seen: set[int] = set()
    previous = 0
    for page, marker in numbered:
        if marker in seen:
            continue
        if marker < previous:
            logger.info("deck section markers go backwards at page %s", page)
            return None
        seen.add(marker)
        previous = marker
        title = _page_title(document[page - 1], body_size=profile.body_size)
        entries.append((1, f"{marker}. {title}" if title else f"Section {marker}", page))

    if len(entries) < MINIMUM_SECTIONS:
        logger.info("deck yielded only %s sections; not a scheme", len(entries))
        return None
    return entries
