"""Make a book readable when its own bytes cannot be stored.

The reading pane opens the page an answer cites, which needs a PDF in Storage.
Supabase caps uploads at 50 MB on the free plan, and the books that most need a
reading pane are exactly the ones that exceed it — a 55.5 MB scan of a system
design book, where seeing the diagram is half the point.

A viewer copy is a re-render of the same pages at the resolution the scan
actually carries, small enough to store. It is **derived and never
authoritative**: the canonical text came from the original at full quality, and
`books.file_hash` still identifies those original bytes. Re-encoding does not
touch what the book *says*, only what a reader is shown.

The quality argument that blocks recompressing a source does not apply here.
Shrinking a file before OCR degrades the input to the stage whose whole job is
reading it. Shrinking it afterwards, for human eyes, costs nothing that
matters — these scans carry 110–160 dpi of real detail, so re-rendering at 130
dpi discards resolution the page never had.
"""

from dataclasses import dataclass
from pathlib import Path
import logging

import fitz


logger = logging.getLogger("study_partner.ingestion.viewer_copy")

__all__ = ["ViewerCopy", "build_viewer_copy", "needs_viewer_copy"]

# Tried in order until one fits. The first is generous enough to be
# indistinguishable from the scan; the last is the smallest still worth reading.
# Measured on the 427-page scan: 150/70 gives 64.5 MB, 150/60 gives 57.0 MB,
# and 130/65 gives 49.7 MB, which fits.
QUALITY_LADDER: tuple[tuple[int, int], ...] = (
    (150, 70),
    (150, 60),
    (130, 65),
    (110, 60),
    (100, 50),
)

# Leaves room under the platform ceiling for the multipart envelope.
SIZE_MARGIN_BYTES = 1_000_000


@dataclass(frozen=True)
class ViewerCopy:
    """A stored-size rendering of a book, and how it was produced."""

    path: Path
    size_bytes: int
    render_dpi: int
    jpeg_quality: int

    def provenance(self) -> dict[str, object]:
        return {
            "origin": "viewer_copy",
            "size_bytes": self.size_bytes,
            "render_dpi": self.render_dpi,
            "jpeg_quality": self.jpeg_quality,
        }


def needs_viewer_copy(size_bytes: int, *, ceiling_bytes: int) -> bool:
    """Whether a source is too large to be stored as it stands."""

    return size_bytes > ceiling_bytes - SIZE_MARGIN_BYTES


def build_viewer_copy(
    source: Path,
    destination: Path,
    *,
    ceiling_bytes: int,
) -> ViewerCopy | None:
    """Render a readable copy that fits, or None when none of the settings do.

    Returning None rather than storing something illegible is deliberate: the
    interface already explains an absent source, and a page too degraded to
    read is worse than a page the reader knows is unavailable.
    """

    budget = ceiling_bytes - SIZE_MARGIN_BYTES
    for render_dpi, jpeg_quality in QUALITY_LADDER:
        with fitz.open(source) as document, fitz.open() as rendered:
            for page in document:
                pixmap = page.get_pixmap(dpi=render_dpi)
                target = rendered.new_page(
                    width=page.rect.width, height=page.rect.height
                )
                target.insert_image(
                    target.rect,
                    stream=pixmap.tobytes("jpeg", jpg_quality=jpeg_quality),
                )
            rendered.save(destination, garbage=4, deflate=True)

        size = destination.stat().st_size
        logger.info(
            "viewer copy at %s dpi q%s is %.1f MB (budget %.1f MB)",
            render_dpi,
            jpeg_quality,
            size / 1e6,
            budget / 1e6,
        )
        if size <= budget:
            return ViewerCopy(
                path=destination,
                size_bytes=size,
                render_dpi=render_dpi,
                jpeg_quality=jpeg_quality,
            )

    destination.unlink(missing_ok=True)
    logger.warning("no viewer copy of %s fits under %s bytes", source, budget)
    return None
