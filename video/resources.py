"""Fetch and losslessly split the documents a lecture points at.

This reuses the book pipeline's proven parsing concepts — PyMuPDF text
extraction and the measured slide-deck typography profile — but produces
page-located records instead of a book hierarchy. A slide is not a chapter,
and a PDF page is never assumed to align with a video timestamp: alignment is
a separate evaluated question, so pages keep their own identity and citations
stay separate from timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import fitz
import httpx

from parsing.slides import slide_page_titles


PARSER_VERSION = "video-pdf-pages-v1"
PDF_MEDIA_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF-"
DEFAULT_MAXIMUM_RESOURCE_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 256 * 1024
MAXIMUM_PAGE_CHARACTERS = 20_000


class ResourceAcquisitionError(RuntimeError):
    """The document could not be fetched as a usable PDF."""


class ResourceParseError(RuntimeError):
    """The fetched bytes are not a readable PDF."""


@dataclass(frozen=True)
class DownloadedResource:
    path: Path
    media_type: str
    size_bytes: int
    final_url: str


@dataclass(frozen=True)
class ResourcePage:
    page_number: int
    text: str
    title: str | None
    layout: dict[str, object]
    content_hash: str


@dataclass(frozen=True)
class ParsedDocument:
    page_count: int
    pages: tuple[ResourcePage, ...]
    is_slide_deck: bool
    provenance: dict[str, object]


def maximum_resource_bytes() -> int:
    raw = os.getenv("VIDEO_RESOURCE_MAX_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAXIMUM_RESOURCE_BYTES
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("VIDEO_RESOURCE_MAX_BYTES must be an integer") from error
    if value <= 0:
        raise ValueError("VIDEO_RESOURCE_MAX_BYTES must be positive")
    return value


def parser_config_hash() -> str:
    """Identify the parsing behavior a stored page was produced by."""

    return sha256(
        json.dumps(
            {
                "parser_version": PARSER_VERSION,
                "extraction": "pymupdf-text",
                "deck_titles": True,
                "maximum_page_characters": MAXIMUM_PAGE_CHARACTERS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def download_pdf(
    url: str,
    destination: Path,
    *,
    maximum_bytes: int | None = None,
    timeout: float = 60.0,
) -> DownloadedResource:
    """Stream a remote PDF to disk under an explicit byte ceiling."""

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ResourceAcquisitionError("resource URL must use http or https")
    limit = maximum_bytes or maximum_resource_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    size = 0
    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=timeout
        ) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared and int(declared) > limit:
                raise ResourceAcquisitionError("resource exceeds the size limit")
            media_type = (
                response.headers.get("content-type", "").split(";", 1)[0].strip()
            )
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                    size += len(chunk)
                    if size > limit:
                        raise ResourceAcquisitionError(
                            "resource exceeds the size limit"
                        )
                    handle.write(chunk)
            final_url = str(response.url)
    except httpx.HTTPError as error:
        destination.unlink(missing_ok=True)
        raise ResourceAcquisitionError(f"resource download failed: {error}") from error
    except ResourceAcquisitionError:
        destination.unlink(missing_ok=True)
        raise

    if size == 0:
        destination.unlink(missing_ok=True)
        raise ResourceAcquisitionError("resource download returned no bytes")
    # Servers mislabel PDFs as octet-stream often enough that the declared type
    # cannot be the gate; the file's own header can.
    with destination.open("rb") as handle:
        if handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
            destination.unlink(missing_ok=True)
            raise ResourceAcquisitionError("resource is not a PDF")
    return DownloadedResource(
        path=destination,
        media_type=PDF_MEDIA_TYPE if media_type != PDF_MEDIA_TYPE else media_type,
        size_bytes=size,
        final_url=final_url,
    )


def parse_pdf_pages(path: str | Path) -> ParsedDocument:
    """Return one lossless record per page, titled when the file is a deck."""

    source = Path(path)
    try:
        document = fitz.open(source)
    except Exception as error:  # noqa: BLE001 - any fitz failure is unreadable input
        raise ResourceParseError(f"could not open the PDF: {error}") from error
    try:
        if document.page_count <= 0:
            raise ResourceParseError("the PDF has no pages")
        if document.needs_pass:
            raise ResourceParseError("the PDF is password protected")
        titles = slide_page_titles(document)
        pages = []
        empty_pages = 0
        for index, page in enumerate(document):
            raw = page.get_text("text") or ""
            text = raw.strip()[:MAXIMUM_PAGE_CHARACTERS]
            if not text:
                empty_pages += 1
            title = (titles[index] or None) if titles else None
            layout = {
                "width": round(float(page.rect.width), 2),
                "height": round(float(page.rect.height), 2),
                "characters": len(text),
                "is_slide": titles is not None,
            }
            if title:
                layout["title"] = title
            pages.append(
                ResourcePage(
                    page_number=index + 1,
                    text=text,
                    title=title,
                    layout=layout,
                    content_hash=_page_hash(title, text),
                )
            )
        return ParsedDocument(
            page_count=document.page_count,
            pages=tuple(pages),
            is_slide_deck=titles is not None,
            provenance={
                "parser_version": PARSER_VERSION,
                "page_count": document.page_count,
                # A deck of scanned images would extract as empty pages; the
                # count is recorded so a silent no-text ingest is visible
                # rather than looking like a successful parse.
                "empty_page_count": empty_pages,
                "is_slide_deck": titles is not None,
            },
        )
    finally:
        document.close()


def page_evidence_text(
    *, resource_title: str, page_number: int, title: str | None, text: str
) -> str:
    """Name the document and page so retrieved text keeps its own identity."""

    label = f"{resource_title} page {page_number}"
    parts = [f"{label}: {title}" if title else label, text]
    return " ".join(" ".join(part.split()) for part in parts if part.strip())


def _page_hash(title: str | None, text: str) -> str:
    payload = json.dumps(
        {"title": title or "", "text": text}, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode()).hexdigest()
