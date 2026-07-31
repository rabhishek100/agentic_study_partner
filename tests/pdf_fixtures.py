"""Generated PDF fixtures for ingestion tests.

Building these with PyMuPDF keeps the repository free of binary fixtures and
makes each document's defect explicit in code.
"""

from pathlib import Path

import fitz


BODY_TEXT = (
    "Training-serving skew happens when the data a model sees in production "
    "differs from the data it was trained on. Monitoring input distributions "
    "is the usual way to detect it before accuracy degrades."
)


def _write(document: fitz.Document, path: Path, **save_options) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path), **save_options)
    document.close()
    return path


def _document(page_count: int, *, with_text: bool = True) -> fitz.Document:
    document = fitz.open()
    for number in range(page_count):
        page = document.new_page()
        if with_text:
            page.insert_text((72, 96), f"Page {number + 1}", fontsize=11)
            page.insert_text((72, 130), BODY_TEXT, fontsize=11)
    return document


def structured_pdf(path: Path, *, page_count: int = 6) -> Path:
    """A digital PDF with embedded text and a well-formed outline."""

    document = _document(page_count)
    document.set_toc(
        [
            [1, "Chapter 1. Overview", 1],
            [2, "Why monitoring matters", 2],
            [1, "Chapter 2. Data Distribution Shifts", 4],
            [2, "Detecting skew", 5],
        ]
    )
    return _write(document, path)


def pdf_without_outline(path: Path, *, page_count: int = 4) -> Path:
    """Readable text, but no embedded table of contents."""

    return _write(_document(page_count), path)


def pdf_with_visual_headings(path: Path) -> Path:
    """Readable native PDF whose hierarchy exists only in page typography."""

    document = fitz.open()
    headings = {
        0: [("1 Introduction", 20), ("1.1 Why Parallelism Matters", 15)],
        2: [("2 Memory Systems", 20), ("2.1 Locality", 15)],
    }
    for number in range(4):
        page = document.new_page()
        y = 90
        for title, size in headings.get(number, []):
            page.insert_text((72, y), title, fontsize=size, fontname="hebo")
            y += 40
        page.insert_text((72, y + 20), BODY_TEXT, fontsize=11)
    return _write(document, path)


def scanned_pdf(path: Path, *, page_count: int = 4) -> Path:
    """Pages with no extractable text, as a scan would produce."""

    document = _document(page_count, with_text=False)
    document.set_toc([[1, "Chapter 1", 1]])
    return _write(document, path)


def mixed_pdf(path: Path, *, page_count: int = 8) -> Path:
    """Half the pages carry text; the rest are image-only."""

    document = fitz.open()
    for number in range(page_count):
        page = document.new_page()
        if number % 2 == 0:
            page.insert_text((72, 96), BODY_TEXT, fontsize=11)
    document.set_toc([[1, "Chapter 1", 1]])
    return _write(document, path)


def ocr_backed_pdf(path: Path, *, page_count: int = 4) -> Path:
    """Full-page raster images with a selectable OCR text overlay."""

    document = fitz.open()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 612, 792), False)
    pixmap.clear_with(245)
    image = pixmap.tobytes("png")
    for number in range(page_count):
        page = document.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=image)
        page.insert_text((72, 96), f"Page {number + 1}", fontsize=11)
        page.insert_text((72, 130), BODY_TEXT, fontsize=11)
    document.set_toc([[1, "Chapter 1", 1]])
    return _write(document, path)


def encrypted_pdf(path: Path) -> Path:
    """A password-protected PDF, which is out of scope by design."""

    document = _document(2)
    document.set_toc([[1, "Chapter 1", 1]])
    return _write(
        document,
        path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )


def corrupt_pdf(path: Path) -> Path:
    """Bytes that claim to be a PDF and are not."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nthis is not a valid cross-reference table\n")
    return path


def outline_pdf(path: Path, toc: list[list], *, page_count: int = 6) -> Path:
    """A readable PDF carrying a deliberately malformed outline."""

    document = _document(page_count)
    document.set_toc(toc)
    return _write(document, path)
