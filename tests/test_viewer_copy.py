"""A readable copy for books whose own bytes cannot be stored."""

import tempfile
import unittest
from pathlib import Path

import fitz

from ingestion.viewer_copy import (
    QUALITY_LADDER,
    SIZE_MARGIN_BYTES,
    build_viewer_copy,
    needs_viewer_copy,
)


def _scan_like_pdf(path: Path, *, page_count: int = 6) -> Path:
    """Pages that are images, as a scan is, so re-rendering is meaningful."""

    document = fitz.open()
    for number in range(page_count):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 96), f"Page {number + 1} of a scanned book", fontsize=14)
        pixmap = page.get_pixmap(dpi=150)
        page.insert_image(page.rect, stream=pixmap.tobytes("jpeg", jpg_quality=95))
    document.save(path, garbage=4, deflate=True)
    document.close()
    return path


class NeedsViewerCopyTests(unittest.TestCase):
    def test_a_source_over_the_ceiling_needs_one(self) -> None:
        self.assertTrue(needs_viewer_copy(55_500_000, ceiling_bytes=52_428_800))

    def test_a_comfortable_source_does_not(self) -> None:
        self.assertFalse(needs_viewer_copy(20_000_000, ceiling_bytes=52_428_800))

    def test_a_source_just_under_the_ceiling_still_needs_one(self) -> None:
        """The multipart envelope has to fit too, so the margin is real."""

        ceiling = 52_428_800
        self.assertTrue(
            needs_viewer_copy(ceiling - SIZE_MARGIN_BYTES + 1, ceiling_bytes=ceiling)
        )


class BuildViewerCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)
        self.source = _scan_like_pdf(self.directory / "scan.pdf")

    def test_a_copy_keeps_every_page(self) -> None:
        """A citation names a page, so the rendering must not renumber them."""

        copy = build_viewer_copy(
            self.source, self.directory / "viewer.pdf", ceiling_bytes=50_000_000
        )

        assert copy is not None
        with fitz.open(self.source) as original, fitz.open(copy.path) as rendered:
            self.assertEqual(rendered.page_count, original.page_count)
            self.assertEqual(
                [round(page.rect.width) for page in rendered],
                [round(page.rect.width) for page in original],
            )

    def test_the_most_generous_setting_that_fits_is_chosen(self) -> None:
        copy = build_viewer_copy(
            self.source, self.directory / "viewer.pdf", ceiling_bytes=50_000_000
        )

        assert copy is not None
        self.assertEqual((copy.render_dpi, copy.jpeg_quality), QUALITY_LADDER[0])

    def test_a_tight_ceiling_forces_a_smaller_rendering(self) -> None:
        generous = build_viewer_copy(
            self.source, self.directory / "big.pdf", ceiling_bytes=50_000_000
        )
        assert generous is not None

        tight = build_viewer_copy(
            self.source,
            self.directory / "small.pdf",
            ceiling_bytes=SIZE_MARGIN_BYTES + generous.size_bytes // 2,
        )

        assert tight is not None
        self.assertLess(tight.size_bytes, generous.size_bytes)
        self.assertLessEqual(tight.render_dpi, generous.render_dpi)

    def test_an_impossible_ceiling_yields_nothing_rather_than_mush(self) -> None:
        """A page too degraded to read is worse than one known to be missing.

        The interface already explains an absent source, so refusing is the
        honest outcome.
        """

        destination = self.directory / "viewer.pdf"
        copy = build_viewer_copy(
            self.source, destination, ceiling_bytes=SIZE_MARGIN_BYTES + 10
        )

        self.assertIsNone(copy)
        self.assertFalse(destination.exists())

    def test_the_copy_records_how_it_was_made(self) -> None:
        """It is derived, so what produced it has to travel with it."""

        copy = build_viewer_copy(
            self.source, self.directory / "viewer.pdf", ceiling_bytes=50_000_000
        )

        assert copy is not None
        provenance = copy.provenance()
        self.assertEqual(provenance["origin"], "viewer_copy")
        self.assertIn("render_dpi", provenance)
        self.assertIn("jpeg_quality", provenance)

    def test_the_copy_is_never_the_source(self) -> None:
        """books.file_hash names the ingested bytes; a re-encode is not them."""

        copy = build_viewer_copy(
            self.source, self.directory / "viewer.pdf", ceiling_bytes=50_000_000
        )

        assert copy is not None
        self.assertNotEqual(copy.path.read_bytes(), self.source.read_bytes())


if __name__ == "__main__":
    unittest.main()
