"""Cheap PDF inspection before any expensive parsing.

PyMuPDF opens the file, proves it is a usable PDF, and classifies it. This
runs first because rejecting a 900-page scan costs milliseconds here and
minutes in the layout parser.

Classification thresholds are versioned. Changing them changes which
documents are accepted, so the version is recorded in job provenance.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median

import fitz

from .config import IngestionLimits
from .errors import ErrorCode, IngestionError
from .outlines import OutlineAnalysis, analyze_outline


CLASSIFIER_VERSION = "preflight-v1"
PROFILE_VERSION = "document-profile-v1"

# A page counts as having real text at this many characters. Page numbers and
# running headers alone should not make a scanned page look digital.
MINIMUM_PAGE_CHARACTERS = 48
# Sampling keeps preflight fast on long books while still seeing the shape of
# the document; pages are taken at an even stride across the whole file.
MAXIMUM_SAMPLED_PAGES = 40
STRUCTURED_TEXT_COVERAGE = 0.80
SCANNED_TEXT_COVERAGE = 0.20
# A raster covering almost the whole page with extractable text drawn over it
# is the common shape of an OCR-backed scan. Requiring the pattern on most
# sampled pages avoids mistaking occasional full-page illustrations for scans.
FULL_PAGE_IMAGE_AREA_RATIO = 0.80
OCR_BACKED_PAGE_COVERAGE = 0.80

STRUCTURED_DIGITAL = "structured_digital"
DIGITAL_WITHOUT_TOC = "digital_without_toc"
SCANNED = "scanned"
MIXED = "mixed"
UNSUPPORTED = "unsupported"

PARSE = "parse"
REVIEW = "review"
REJECT = "reject"
# Transcribe the pages first, then propose a hierarchy from the text and send
# it to review. A scan has no text layer to route on and an OCR-backed source
# has one somebody else produced, so both reach the outline question only after
# this stage has answered the text question.
OCR = "ocr"


@dataclass(frozen=True)
class DocumentProfile:
    """Cheap, deterministic measurements used to route a PDF safely.

    The profile deliberately records evidence instead of deciding whether an
    outline is trustworthy. Outline quality is a separate concern: a native
    digital PDF may still have a broken outline, while an OCR-backed PDF may
    carry a syntactically valid but unusable one.
    """

    source_size_bytes: int
    page_count: int
    sampled_pages: int
    pages_with_text: int
    pages_with_images: int
    pages_with_full_page_images: int
    pages_with_ocr_overlay: int
    median_extracted_characters: int
    sampled_image_pixels: int
    profile_version: str = PROFILE_VERSION

    @staticmethod
    def _coverage(count: int, total: int) -> float:
        return count / total if total else 0.0

    @property
    def text_coverage(self) -> float:
        return self._coverage(self.pages_with_text, self.sampled_pages)

    @property
    def image_page_coverage(self) -> float:
        return self._coverage(self.pages_with_images, self.sampled_pages)

    @property
    def full_page_image_coverage(self) -> float:
        return self._coverage(
            self.pages_with_full_page_images, self.sampled_pages
        )

    @property
    def ocr_overlay_coverage(self) -> float:
        return self._coverage(self.pages_with_ocr_overlay, self.sampled_pages)

    @property
    def likely_ocr_backed(self) -> bool:
        return self.ocr_overlay_coverage >= OCR_BACKED_PAGE_COVERAGE

    @property
    def estimated_total_image_pixels(self) -> int:
        """Extrapolate sampled image work across the document.

        This is an observability signal, not a rejection threshold. Keeping it
        in pixels avoids pretending compressed PDF bytes predict parser memory.
        """

        if not self.sampled_pages:
            return 0
        return round(
            self.sampled_image_pixels * self.page_count / self.sampled_pages
        )

    def provenance(self) -> dict[str, object]:
        return {
            "profile_version": self.profile_version,
            "source_size_bytes": self.source_size_bytes,
            "page_count": self.page_count,
            "sampled_pages": self.sampled_pages,
            "pages_with_text": self.pages_with_text,
            "pages_with_images": self.pages_with_images,
            "pages_with_full_page_images": self.pages_with_full_page_images,
            "pages_with_ocr_overlay": self.pages_with_ocr_overlay,
            "text_coverage": round(self.text_coverage, 4),
            "image_page_coverage": round(self.image_page_coverage, 4),
            "full_page_image_coverage": round(
                self.full_page_image_coverage, 4
            ),
            "ocr_overlay_coverage": round(self.ocr_overlay_coverage, 4),
            "likely_ocr_backed": self.likely_ocr_backed,
            "median_extracted_characters": self.median_extracted_characters,
            "sampled_image_pixels": self.sampled_image_pixels,
            "estimated_total_image_pixels": self.estimated_total_image_pixels,
        }


@dataclass(frozen=True)
class PreflightDecision:
    """Parser routing decision derived from measured profile and outline data."""

    action: str
    reasons: tuple[str, ...]
    outline_source: str | None

    def provenance(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reasons": list(self.reasons),
            "outline_source": self.outline_source,
        }


@dataclass(frozen=True)
class PreflightReport:
    """What the source PDF is, decided before the parser is started."""

    page_count: int
    document_class: str
    toc: list[tuple[int, str, int]]
    metadata: dict[str, str]
    sampled_pages: int
    pages_with_text: int
    profile: DocumentProfile
    outline: OutlineAnalysis
    decision: PreflightDecision
    classifier_version: str = CLASSIFIER_VERSION
    warnings: list[str] = field(default_factory=list)

    @property
    def text_coverage(self) -> float:
        if not self.sampled_pages:
            return 0.0
        return self.pages_with_text / self.sampled_pages

    @property
    def supported(self) -> bool:
        return self.decision.action == PARSE

    @property
    def normalized_toc(self) -> list[tuple[int, str, int]]:
        return self.outline.normalization.as_toc()

    def provenance(self) -> dict[str, object]:
        return {
            "classifier_version": self.classifier_version,
            "document_class": self.document_class,
            "page_count": self.page_count,
            "toc_entries": len(self.toc),
            "text_coverage": round(self.text_coverage, 4),
            "sampled_pages": self.sampled_pages,
            "profile": self.profile.provenance(),
            "outline": self.outline.provenance(),
            "decision": self.decision.provenance(),
        }


def _sampled_page_numbers(page_count: int) -> list[int]:
    if page_count <= MAXIMUM_SAMPLED_PAGES:
        return list(range(page_count))
    stride = page_count / MAXIMUM_SAMPLED_PAGES
    return sorted({int(index * stride) for index in range(MAXIMUM_SAMPLED_PAGES)})


def _image_measurements(page: fitz.Page) -> tuple[bool, bool, int]:
    """Return image presence, full-page presence, and decoded pixel work."""

    try:
        images = page.get_image_info()
    except Exception:
        # Image metadata is diagnostic. Text extraction and page readability
        # remain the hard preflight contract, so unusual image objects must not
        # make an otherwise supported existing book fail.
        return False, False, 0

    page_area = page.rect.get_area()
    has_full_page_image = False
    image_pixels = 0
    for image in images:
        width = image.get("width")
        height = image.get("height")
        if isinstance(width, int) and isinstance(height, int):
            image_pixels += max(0, width) * max(0, height)

        bbox = image.get("bbox")
        if not bbox or page_area <= 0:
            continue
        try:
            covered_area = (fitz.Rect(bbox) & page.rect).get_area()
        except Exception:
            continue
        if covered_area / page_area >= FULL_PAGE_IMAGE_AREA_RATIO:
            has_full_page_image = True

    return bool(images), has_full_page_image, image_pixels


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


def decide_preflight(
    *,
    document_class: str,
    profile: DocumentProfile,
    outline: OutlineAnalysis,
) -> PreflightDecision:
    """Route measured evidence without invoking a model."""

    if document_class == UNSUPPORTED:
        return PreflightDecision(
            action=REJECT,
            reasons=(f"unsupported_document_class:{document_class}",),
            outline_source=None,
        )

    # A page with no text cannot be routed on its text, and a text layer
    # somebody else's OCR produced is evidence about that engine rather than
    # about the book: this corpus contains one whose outline is populated with
    # OCR'd equations. Both go to transcription first, and the hierarchy
    # question is answered afterwards from text this pipeline produced and can
    # account for.
    if (
        document_class in {SCANNED, MIXED}
        or profile.likely_ocr_backed
        or outline.poisoned
    ):
        reasons = [f"document_class:{document_class}"]
        if profile.likely_ocr_backed:
            reasons.append("ocr_backed_source")
        if outline.poisoned:
            # The embedded rows are discarded rather than repaired. They were
            # harvested off the pages by OCR software, so the actual chapter
            # headings are absent from them entirely and there is nothing in
            # them to repair towards.
            reasons.append("poisoned_outline")
        return PreflightDecision(
            action=OCR, reasons=tuple(reasons), outline_source=None
        )

    reasons = list(outline.assessment.reasons)
    if document_class == DIGITAL_WITHOUT_TOC and "missing_outline" not in reasons:
        reasons.append("missing_outline")

    if reasons:
        proposal = outline.proposal
        if proposal is not None and proposal.entries:
            source = "deterministic_proposal"
        elif outline.normalization.entries:
            source = "normalized_embedded"
        else:
            source = None
        return PreflightDecision(
            action=REVIEW,
            reasons=tuple(dict.fromkeys(reasons)),
            outline_source=source,
        )

    return PreflightDecision(
        action=PARSE,
        reasons=(),
        outline_source="normalized_embedded",
    )


def inspect_pdf(source: Path, *, limits: IngestionLimits) -> PreflightReport:
    """Measure a source PDF without applying outline acceptance policy.

    This boundary lets diagnostics and future repair logic inspect readable
    PDFs whose embedded outline is missing or malformed. File readability,
    encryption, and bounded page count are still hard safety requirements.
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
        pages_with_images = 0
        pages_with_full_page_images = 0
        pages_with_ocr_overlay = 0
        extracted_character_counts: list[int] = []
        sampled_image_pixels = 0
        for number in sampled:
            try:
                page = document.load_page(number)
                text = page.get_text("text")
            except Exception as error:
                raise IngestionError(
                    ErrorCode.INVALID_PDF,
                    detail=f"page {number} could not be read: {error!r}",
                ) from error
            extracted_characters = len(text.strip())
            extracted_character_counts.append(extracted_characters)
            has_text = extracted_characters >= MINIMUM_PAGE_CHARACTERS
            if has_text:
                pages_with_text += 1

            has_images, has_full_page_image, image_pixels = _image_measurements(page)
            sampled_image_pixels += image_pixels
            if has_images:
                pages_with_images += 1
            if has_full_page_image:
                pages_with_full_page_images += 1
                if has_text:
                    pages_with_ocr_overlay += 1

        coverage = pages_with_text / len(sampled) if sampled else 0.0
        document_class = classify(has_toc=bool(toc), text_coverage=coverage)
        profile = DocumentProfile(
            source_size_bytes=source.stat().st_size,
            page_count=page_count,
            sampled_pages=len(sampled),
            pages_with_text=pages_with_text,
            pages_with_images=pages_with_images,
            pages_with_full_page_images=pages_with_full_page_images,
            pages_with_ocr_overlay=pages_with_ocr_overlay,
            median_extracted_characters=(
                round(median(extracted_character_counts))
                if extracted_character_counts
                else 0
            ),
            sampled_image_pixels=sampled_image_pixels,
        )
        outline = analyze_outline(
            document,
            toc,
            likely_ocr_backed=profile.likely_ocr_backed,
        )
        decision = decide_preflight(
            document_class=document_class,
            profile=profile,
            outline=outline,
        )

    return PreflightReport(
        page_count=page_count,
        document_class=document_class,
        toc=toc,
        metadata=metadata,
        sampled_pages=len(sampled),
        pages_with_text=pages_with_text,
        profile=profile,
        outline=outline,
        decision=decision,
        warnings=[],
    )


def preflight(source: Path, *, limits: IngestionLimits) -> PreflightReport:
    """Inspect a source PDF and validate an automatically accepted outline.

    Only harmless normalization may flow through automatically. A review or
    reject decision remains measurable and is returned so the pipeline can
    persist its provenance before :func:`require_supported` stops the job.
    """

    report = inspect_pdf(source, limits=limits)
    warnings = list(report.warnings)
    if report.decision.action == PARSE:
        validate_table_of_contents(report.normalized_toc, report.page_count)
        if report.normalized_toc[0][2] > 1:
            warnings.append("content before the first outline entry is not ingested")
    return replace(report, warnings=warnings)


def require_supported(report: PreflightReport) -> None:
    """Stop anything this pipeline cannot ingest safely.

    Scanned and OCR-backed sources are no longer stopped here: they route to
    transcription and reach a hierarchy through review. What remains refused is
    a source whose structure cannot be established at all, because inventing
    chapter boundaries would put wrong citations in front of a reader and
    nothing downstream would catch it.
    """

    if report.decision.action in {PARSE, OCR}:
        return
    if report.document_class == DIGITAL_WITHOUT_TOC:
        raise IngestionError(
            ErrorCode.MISSING_TABLE_OF_CONTENTS,
            detail="digital PDF without an embedded outline",
        )
    if report.decision.action == REVIEW:
        raise IngestionError(
            ErrorCode.INVALID_HIERARCHY,
            detail=(
                "embedded outline requires review: "
                + ", ".join(report.decision.reasons)
            ),
        )
    raise IngestionError(
        ErrorCode.UNSUPPORTED_DOCUMENT_CLASS,
        detail=(
            f"document class {report.document_class} "
            f"(text coverage {report.text_coverage:.2f})"
        ),
    )
