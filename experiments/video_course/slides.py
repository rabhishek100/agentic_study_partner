"""Official slide-deck acquisition and page extraction for linked evidence."""

from __future__ import annotations

from pathlib import Path

import fitz
import httpx

from .models import SlidePage


def download_slides(url: str, destination: Path) -> Path:
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = httpx.get(url, follow_redirects=True, timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError("slide URL did not return a PDF")
    destination.write_bytes(response.content)
    return destination


def extract_slides(pdf_path: Path, output_dir: Path) -> list[SlidePage]:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(pdf_path)
    pages: list[SlidePage] = []
    try:
        for index, page in enumerate(document):
            image_path = output_dir / f"slide-{index + 1:03d}.jpg"
            if not image_path.exists():
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
                pixmap.save(image_path)
            pages.append(
                SlidePage(
                    page=index + 1,
                    text=" ".join(page.get_text("text").split()),
                    image_path=str(image_path),
                )
            )
    finally:
        document.close()
    return pages

