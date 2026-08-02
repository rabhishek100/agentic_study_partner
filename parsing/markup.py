"""The markup a page transcription carries, and how to read it back.

This is the contract between the generative stage that produces text from
pixels and the deterministic stage that produces structure from text. It lives
in `parsing` because reading structure out of a document is what this package
does, and because the dependency has to point this way: ingestion may import
parsing, never the reverse.

Four things are marked, and they are the four a transcription would otherwise
destroy: heading level, table structure, running margins, and where a figure
sits on the page.
"""

from dataclasses import dataclass
import re


__all__ = [
    "BBOX_GRID",
    "FigureRegion",
    "PageMarkup",
    "parse_page_markup",
]

_HEADER = re.compile(r"<!--\s*header:\s*(?P<text>.*?)-->", re.DOTALL | re.IGNORECASE)
_FOOTER = re.compile(r"<!--\s*footer:\s*(?P<text>.*?)-->", re.DOTALL | re.IGNORECASE)
_FIGURE = re.compile(
    r"<figure(?P<attributes>[^>]*)>(?P<caption>.*?)</figure>",
    re.DOTALL | re.IGNORECASE,
)
_BBOX = re.compile(r"data-bbox\s*=\s*[\"'](?P<box>[^\"']+)[\"']", re.IGNORECASE)

# Gemini reports boxes on a 0-1000 grid regardless of what the prompt asks for,
# and measuring one against a page confirmed the values are accurate, just
# scaled. Rather than fight the model, both conventions are accepted: anything
# above 1 is divided down. Nothing legitimate is a 0-1 box wider than the page.
BBOX_GRID = 1000.0


@dataclass(frozen=True)
class FigureRegion:
    """A figure the model located on the page, in fractions of the page."""

    caption: str
    bbox: tuple[float, float, float, float] | None

    @property
    def locatable(self) -> bool:
        return self.bbox is not None


@dataclass(frozen=True)
class PageMarkup:
    """One transcription split into the parts structure extraction consumes."""

    body: str
    header: str = ""
    footer: str = ""
    figures: tuple[FigureRegion, ...] = ()


def _normalize_bbox(raw: str) -> tuple[float, float, float, float] | None:
    parts = [
        piece.strip() for piece in raw.replace(" ", ",").split(",") if piece.strip()
    ]
    if len(parts) != 4:
        return None
    try:
        values = [float(piece) for piece in parts]
    except ValueError:
        return None
    if any(value > 1.0 for value in values):
        values = [value / BBOX_GRID for value in values]
    if any(value < 0.0 or value > 1.0 for value in values):
        return None
    left, top, right, bottom = values
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def parse_page_markup(text: str) -> PageMarkup:
    """Split a transcription into body, running margins, and figure regions.

    The margins matter as much as the body: a running footer is where the
    printed page number lives, which is what anchors a printed-page citation to
    a PDF page, and in one of these books it also names the chapter.
    """

    header_match = _HEADER.search(text)
    footer_match = _FOOTER.search(text)

    figures = []
    for match in _FIGURE.finditer(text):
        bbox_match = _BBOX.search(match.group("attributes") or "")
        figures.append(
            FigureRegion(
                caption=" ".join(match.group("caption").split()),
                bbox=_normalize_bbox(bbox_match.group("box")) if bbox_match else None,
            )
        )

    body = _FIGURE.sub(" ", _FOOTER.sub(" ", _HEADER.sub(" ", text)))
    return PageMarkup(
        body=body.strip(),
        header=" ".join(header_match.group("text").split()) if header_match else "",
        footer=" ".join(footer_match.group("text").split()) if footer_match else "",
        figures=tuple(figures),
    )
