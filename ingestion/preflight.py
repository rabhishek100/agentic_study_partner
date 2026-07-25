"""Cheap PDF inspection before any expensive parsing.

PyMuPDF opens the file, proves it is a usable PDF, and classifies it. This
runs first because rejecting a 900-page scan costs milliseconds here and
minutes in the layout parser.

Classification thresholds are versioned. Changing them changes which
documents are accepted, so the version is recorded in job provenance.
"""

from dataclasses import dataclass, field
from pathlib import Path

import fitz

from .config import IngestionLimits
from .errors import ErrorCode, IngestionError


CLASSIFIER_VERSION = "preflight-v1"

# A page counts as having real text at this many characters. Page numbers and
# running headers alone should not make a scanned page look digital.
MINIMUM_PAGE_CHARACTERS = 48
# Sampling keeps preflight fast on long books while still seeing the shape of
# the document; pages are taken at an even stride across the whole file.
MAXIMUM_SAMPLED_PAGES = 40
STRUCTURED_TEXT_COVERAGE = 0.80
SCANNED_TEXT_COVERAGE = 0.20

STRUCTURED_DIGITAL = "structured_digital"
DIGITAL_WITHOUT_TOC = "digital_without_toc"
SCANNED = "scanned"
MIXED = "mixed"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PreflightReport:
    """What the source PDF is, decided before the parser is started."""

    page_count: int
    document_class: str
    toc: list[tuple[int, str, int]]
    metadata: dict[str, str]
    sampled_pages: int
    pages_with_text: int
    classifier_version: str = CLASSIFIER_VERSION
    warnings: list[str] = field(default_factory=list)

    @property
    def text_coverage(self) -> float:
        if not self.sampled_pages:
            return 0.0
        return self.pages_with_text / self.sampled_pages

    @property
    def supported(self) -> bool:
        return self.document_class == STRUCTURED_DIGITAL

    def provenance(self) -> dict[str, object]:
        return {
            "classifier_version": self.classifier_version,
            "document_class": self.document_class,
            "page_count": self.page_count,
            "toc_entries": len(self.toc),
            "text_coverage": round(self.text_coverage, 4),
            "sampled_pages": self.sampled_pages,
        }


def _sampled_page_numbers(page_count: int) -> list[int]:
    if page_count <= MAXIMUM_SAMPLED_PAGES:
        return list(range(page_count))
    stride = page_count / MAXIMUM_SAMPLED_PAGES
    return sorted({int(index * stride) for index in range(MAXIMUM_SAMPLED_PAGES)})


def validate_table_of_contents(
    toc: list[tuple[int, str, int]],
    page_count: int,
) -> None:
    """Reject a hierarchy the canonical model cannot represent.

    These are the same rules canonical ingestion enforces later. Checking them
    here turns a confusing failure deep inside the parser into an actionable
    message before any expensive work starts.
    """

    if not toc:
        raise IngestionError(
            ErrorCode.MISSING_TABLE_OF_CONTENTS, detail="no embedded outline"
        )

    previous_level = 0
    previous_page = 0
    for index, entry in enumerate(toc):
        level, title, start_page = entry
        if level < 1:
            raise IngestionError(
                ErrorCode.INVALID_HIERARCHY, detail=f"entry {index} has level {level}"
            )
        if level > previous_level + 1:
            # A jump from level 1 straight to level 3 has no parent to attach
            # to, so the section path could not be reconstructed losslessly.
            raise IngestionError(
                ErrorCode.INVALID_HIERARCHY,
                detail=f"entry {index} jumps from level {previous_level} to {level}",
            )
        if not title or not title.strip():
            raise IngestionError(
                ErrorCode.INVALID_HIERARCHY, detail=f"entry {index} has no title"
            )
        if not 1 <= start_page <= page_count:
            raise IngestionError(
                ErrorCode.INVALID_HIERARCHY,
                detail=f"entry {index} starts on page {start_page} of {page_count}",
            )
        if start_page < previous_page:
            raise IngestionError(
                ErrorCode.INVALID_HIERARCHY,
                detail=f"entry {index} moves backwards to page {start_page}",
            )
        previous_level = level
        previous_page = start_page


def classify(
    *,
    has_toc: bool,
    text_coverage: float,
) -> str:
    """Name the document class from measured text coverage."""

    if text_coverage <= SCANNED_TEXT_COVERAGE:
        return SCANNED
    if text_coverage < STRUCTURED_TEXT_COVERAGE:
        return MIXED
    return STRUCTURED_DIGITAL if has_toc else DIGITAL_WITHOUT_TOC


def preflight(source: Path, *, limits: IngestionLimits) -> PreflightReport:
    """Inspect a source PDF and decide whether this pipeline can ingest it.

    Raises for anything permanently unusable. A readable PDF of an unsupported
    class returns a report instead, so the caller can record the class it
    detected rather than a bare rejection.
    """

    try:
        document = fitz.open(source)
    except Exception as error:
        raise IngestionError(
            ErrorCode.INVALID_PDF, detail=f"could not open source: {error!r}"
        ) from error

    with document:
        # needs_pass means the content is encrypted and unreadable without a
        # password. is_encrypted alone can be true for a decrypted-on-open file.
        if document.needs_pass:
            raise IngestionError(
                ErrorCode.ENCRYPTED_PDF, detail="source requires a password"
            )
        if not document.is_pdf:
            raise IngestionError(
                ErrorCode.INVALID_PDF, detail="source is not a PDF document"
            )

        page_count = document.page_count
        if page_count < 1:
            raise IngestionError(ErrorCode.INVALID_PDF, detail="source has no pages")
        if page_count > limits.max_pages:
            raise IngestionError(
                ErrorCode.TOO_MANY_PAGES,
                detail=f"{page_count} pages exceeds the {limits.max_pages} limit",
            )

        metadata = {
            key: value
            for key, value in (document.metadata or {}).items()
            if isinstance(value, str) and value.strip()
        }

        try:
            toc = [tuple(entry[:3]) for entry in document.get_toc()]
        except Exception as error:
            raise IngestionError(
                ErrorCode.INVALID_HIERARCHY,
                detail=f"outline could not be read: {error!r}",
            ) from error

        sampled = _sampled_page_numbers(page_count)
        pages_with_text = 0
        for number in sampled:
            try:
                text = document.load_page(number).get_text("text")
            except Exception as error:
                raise IngestionError(
                    ErrorCode.INVALID_PDF,
                    detail=f"page {number} could not be read: {error!r}",
                ) from error
            if len(text.strip()) >= MINIMUM_PAGE_CHARACTERS:
                pages_with_text += 1

    coverage = pages_with_text / len(sampled) if sampled else 0.0
    document_class = classify(has_toc=bool(toc), text_coverage=coverage)

    warnings: list[str] = []
    if document_class == STRUCTURED_DIGITAL:
        validate_table_of_contents(toc, page_count)
        if toc[0][2] > 1:
            warnings.append("content before the first outline entry is not ingested")

    return PreflightReport(
        page_count=page_count,
        document_class=document_class,
        toc=toc,
        metadata=metadata,
        sampled_pages=len(sampled),
        pages_with_text=pages_with_text,
        warnings=warnings,
    )


def require_supported(report: PreflightReport) -> None:
    """Stop anything the first release cannot ingest safely.

    Scanned and TOC-less books are a separate workflow: OCR alone cannot
    establish trustworthy chapter boundaries, and inventing them would put
    wrong citations in front of a reader.
    """

    if report.supported:
        return
    if report.document_class == DIGITAL_WITHOUT_TOC:
        raise IngestionError(
            ErrorCode.MISSING_TABLE_OF_CONTENTS,
            detail="digital PDF without an embedded outline",
        )
    raise IngestionError(
        ErrorCode.UNSUPPORTED_DOCUMENT_CLASS,
        detail=(
            f"document class {report.document_class} "
            f"(text coverage {report.text_coverage:.2f})"
        ),
    )
