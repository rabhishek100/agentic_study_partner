"""Deterministic PDF outline normalization, assessment, and proposal.

Embedded PDF outlines are publisher metadata, not trusted canonical data.
This module separates three operations that have different safety properties:

* normalization repairs only harmless title encoding and whitespace;
* assessment records evidence that an outline is or is not trustworthy;
* proposal infers candidate headings from page typography for human review.

An inferred proposal is never an automatic replacement for source metadata.
"""

from collections import Counter
from dataclasses import dataclass, field
import math
import re
from statistics import median
import unicodedata

import fitz


OUTLINE_ANALYZER_VERSION = "outline-analysis-v1"
OUTLINE_PROPOSER_VERSION = "span-headings-v1"
MAXIMUM_ASSESSED_HEADINGS = 40
MAXIMUM_PROPOSED_ENTRIES = 500
MINIMUM_HEADING_MATCH_RATIO = 0.45
MINIMUM_LAST_PAGE_COVERAGE = 0.50

_ZERO_WIDTH_CATEGORIES = frozenset({"Cf"})
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?[ivxlcdm\d]+$", re.IGNORECASE)
_EXPLICIT_MAJOR = re.compile(
    r"^(?:part|chapter|appendix)\s+[A-Z0-9IVXLC]+(?:\\b|[.:])",
    re.IGNORECASE,
)
_NUMBERED_HEADING = re.compile(
    r"^(?P<number>(?:\d+(?:\.\d+)*|[A-Z]\.\d+(?:\.\d+)*))"
    r"(?:\.)?(?:\s+|\t+)(?P<title>.+)$"
)
_NUMBER_PREFIX = re.compile(
    r"^(?:(?:part|chapter|appendix)\s+[A-Z0-9IVXLC]+[.:]?\s*|"
    r"(?:[A-Z]|\d+)(?:\.\d+)*\.?\s+)",
    re.IGNORECASE,
)
_TITLE_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "vs",
        "with",
    }
)


@dataclass(frozen=True)
class OutlineEntry:
    """One normalized or proposed outline row."""

    level: int
    title: str
    page: int
    source_index: int

    def as_toc(self) -> tuple[int, str, int]:
        return self.level, self.title, self.page

    def provenance(self) -> dict[str, object]:
        return {
            "level": self.level,
            "title": self.title,
            "page": self.page,
            "source_index": self.source_index,
        }


@dataclass(frozen=True)
class DroppedOutlineEntry:
    source_index: int
    reason: str
    raw_title: str

    def provenance(self) -> dict[str, object]:
        return {
            "source_index": self.source_index,
            "reason": self.reason,
            "raw_title": self.raw_title,
        }


@dataclass(frozen=True)
class OutlineNormalization:
    entries: tuple[OutlineEntry, ...]
    dropped_entries: tuple[DroppedOutlineEntry, ...]
    normalized_title_count: int

    def as_toc(self) -> list[tuple[int, str, int]]:
        return [entry.as_toc() for entry in self.entries]

    def provenance(self) -> dict[str, object]:
        return {
            "entries": len(self.entries),
            "dropped_entries": len(self.dropped_entries),
            "normalized_titles": self.normalized_title_count,
            "dropped_reasons": dict(
                Counter(entry.reason for entry in self.dropped_entries)
            ),
        }


@dataclass(frozen=True)
class OutlineAssessment:
    """Measured outline health, independent of routing policy."""

    entry_count: int
    invalid_destination_count: int
    invalid_level_count: int
    orphan_level_jump_count: int
    backward_destination_count: int
    suspicious_title_count: int
    same_page_entry_count: int
    first_entry_page: int | None
    last_entry_page: int | None
    last_page_coverage: float
    heading_checks: int
    heading_matches: int
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def heading_match_ratio(self) -> float:
        if not self.heading_checks:
            return 0.0
        return self.heading_matches / self.heading_checks

    @property
    def needs_review(self) -> bool:
        return bool(self.reasons)

    def provenance(self) -> dict[str, object]:
        return {
            "entry_count": self.entry_count,
            "invalid_destination_count": self.invalid_destination_count,
            "invalid_level_count": self.invalid_level_count,
            "orphan_level_jump_count": self.orphan_level_jump_count,
            "backward_destination_count": self.backward_destination_count,
            "suspicious_title_count": self.suspicious_title_count,
            "same_page_entry_count": self.same_page_entry_count,
            "first_entry_page": self.first_entry_page,
            "last_entry_page": self.last_entry_page,
            "last_page_coverage": round(self.last_page_coverage, 4),
            "heading_checks": self.heading_checks,
            "heading_matches": self.heading_matches,
            "heading_match_ratio": round(self.heading_match_ratio, 4),
            "needs_review": self.needs_review,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class OutlineProposal:
    """Review-only hierarchy inferred from page typography."""

    entries: tuple[OutlineEntry, ...]
    proposer_version: str = OUTLINE_PROPOSER_VERSION
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_toc(self) -> list[tuple[int, str, int]]:
        return [entry.as_toc() for entry in self.entries]

    def provenance(self) -> dict[str, object]:
        return {
            "proposer_version": self.proposer_version,
            "entries": len(self.entries),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class OutlineAnalysis:
    normalization: OutlineNormalization
    assessment: OutlineAssessment
    proposal: OutlineProposal | None
    analyzer_version: str = OUTLINE_ANALYZER_VERSION

    def provenance(self) -> dict[str, object]:
        return {
            "analyzer_version": self.analyzer_version,
            "normalization": self.normalization.provenance(),
            "assessment": self.assessment.provenance(),
            "proposal": self.proposal.provenance() if self.proposal else None,
        }


@dataclass(frozen=True)
class _LineCandidate:
    page: int
    title: str
    size: float
    bold: bool
    top_ratio: float
    bottom_ratio: float
    y: float
    x: float
    explicit_level: int | None


def normalize_title(title: str) -> str:
    """Normalize encoding and whitespace without rewriting words."""

    normalized = unicodedata.normalize("NFKC", title)
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) not in _ZERO_WIDTH_CATEGORIES
    )
    return " ".join(normalized.split())


def normalize_outline(
    toc: list[tuple[int, str, int]],
) -> OutlineNormalization:
    """Repair harmless title defects and preserve all structural coordinates."""

    entries: list[OutlineEntry] = []
    dropped: list[DroppedOutlineEntry] = []
    normalized_titles = 0
    for index, (level, raw_title, page) in enumerate(toc):
        title = normalize_title(raw_title)
        if not title:
            dropped.append(
                DroppedOutlineEntry(
                    source_index=index,
                    reason="empty_title",
                    raw_title=raw_title,
                )
            )
            continue
        if title != raw_title:
            normalized_titles += 1
        entries.append(
            OutlineEntry(
                level=level,
                title=title,
                page=page,
                source_index=index,
            )
        )
    return OutlineNormalization(
        entries=tuple(entries),
        dropped_entries=tuple(dropped),
        normalized_title_count=normalized_titles,
    )


def _sample_entries(
    entries: tuple[OutlineEntry, ...],
    maximum: int = MAXIMUM_ASSESSED_HEADINGS,
) -> list[OutlineEntry]:
    if len(entries) <= maximum:
        return list(entries)
    stride = len(entries) / maximum
    return [entries[int(index * stride)] for index in range(maximum)]


def _meaningful_tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in _WORD.findall(text)
        if len(token) >= 3
    ]


def _heading_matches_page(title: str, page_text: str) -> bool:
    title_key = normalize_title(title).casefold()
    page_key = normalize_title(page_text).casefold()
    if title_key and title_key in page_key:
        return True

    without_number = _NUMBER_PREFIX.sub("", title_key)
    if without_number and without_number in page_key:
        return True

    title_tokens = _meaningful_tokens(without_number or title_key)
    if not title_tokens:
        return False
    page_tokens = set(_meaningful_tokens(page_key))
    matched = sum(token in page_tokens for token in set(title_tokens))
    required = max(1, math.ceil(len(set(title_tokens)) * 0.75))
    return matched >= required


def _suspicious_title(title: str) -> bool:
    if len(title) > 180:
        return True
    letters = [character for character in title if character.isalpha()]
    digits = [character for character in title if character.isdigit()]
    if not letters:
        return True

    tokens = [token.casefold() for token in _WORD.findall(title)]
    if tokens:
        repetitions = Counter(tokens)
        if max(repetitions.values()) >= 3:
            return True

    vowels = sum(character.casefold() in "aeiou" for character in letters)
    if len(letters) >= 5 and vowels / len(letters) < 0.18:
        return True

    visible = [character for character in title if not character.isspace()]
    non_alphanumeric = sum(not character.isalnum() for character in visible)
    if visible and non_alphanumeric / len(visible) > 0.30:
        return True
    if visible and len(digits) / len(visible) > 0.45:
        return True
    return False


def _looks_like_title(title: str) -> bool:
    """Distinguish typographic headings from enlarged body/equation lines."""

    if _suspicious_title(title) or title.rstrip().endswith((".", ",", ";")):
        return False
    words = _WORD.findall(title)
    content_words = [
        word
        for word in words
        if word.casefold() not in _TITLE_STOP_WORDS and len(word) > 1
    ]
    if not content_words:
        return False
    capitalized = sum(word[0].isupper() for word in content_words)
    return capitalized / len(content_words) >= 0.50


def _numbered_remainder_looks_like_title(title: str) -> bool:
    if _suspicious_title(title):
        return False
    math_symbols = sum(
        character in "=≠≤≥←→∑∏√|ÎÕ" for character in title
    )
    visible = sum(not character.isspace() for character in title)
    if visible and math_symbols / visible > 0.08:
        return False
    first_letter = next(
        (character for character in title if character.isalpha()), ""
    )
    return bool(first_letter) and (
        first_letter.isupper() or _looks_like_title(title)
    )


def assess_outline(
    document: fitz.Document,
    normalization: OutlineNormalization,
) -> OutlineAssessment:
    """Score normalized entries against hierarchy and their destination pages."""

    entries = normalization.entries
    page_count = document.page_count
    invalid_destinations = 0
    invalid_levels = 0
    orphan_jumps = 0
    backward_destinations = 0
    suspicious_titles = 0
    previous_level = 0
    previous_page = 0
    valid_pages: list[int] = []

    for entry in entries:
        if entry.level < 1:
            invalid_levels += 1
        elif entry.level > previous_level + 1:
            orphan_jumps += 1

        if not 1 <= entry.page <= page_count:
            invalid_destinations += 1
        else:
            valid_pages.append(entry.page)
            if previous_page and entry.page < previous_page:
                backward_destinations += 1
            previous_page = entry.page

        if _suspicious_title(entry.title):
            suspicious_titles += 1
        if entry.level >= 1:
            previous_level = entry.level

    page_counts = Counter(valid_pages)
    same_page_entries = sum(max(0, count - 1) for count in page_counts.values())
    first_page = valid_pages[0] if valid_pages else None
    last_page = valid_pages[-1] if valid_pages else None
    last_page_coverage = last_page / page_count if last_page else 0.0

    heading_checks = 0
    heading_matches = 0
    assessable = tuple(
        entry for entry in entries if 1 <= entry.page <= page_count
    )
    for entry in _sample_entries(assessable):
        try:
            page_text = document.load_page(entry.page - 1).get_text("text")
        except Exception:
            continue
        heading_checks += 1
        if _heading_matches_page(entry.title, page_text):
            heading_matches += 1

    reasons: list[str] = []
    if not entries:
        reasons.append("missing_outline")
    if invalid_destinations:
        reasons.append("invalid_destinations")
    if invalid_levels:
        reasons.append("invalid_levels")
    if orphan_jumps:
        reasons.append("orphan_level_jumps")
    if backward_destinations:
        reasons.append("backward_destinations")
    if (
        entries
        and last_page_coverage < MINIMUM_LAST_PAGE_COVERAGE
        and len(entries) < 10
    ):
        reasons.append("incomplete_coverage")
    suspicious_threshold = max(3, math.ceil(len(entries) * 0.05))
    if suspicious_titles >= suspicious_threshold:
        reasons.append("suspicious_titles")
    if (
        heading_checks >= 5
        and heading_matches / heading_checks < MINIMUM_HEADING_MATCH_RATIO
    ):
        reasons.append("low_heading_match")

    return OutlineAssessment(
        entry_count=len(entries),
        invalid_destination_count=invalid_destinations,
        invalid_level_count=invalid_levels,
        orphan_level_jump_count=orphan_jumps,
        backward_destination_count=backward_destinations,
        suspicious_title_count=suspicious_titles,
        same_page_entry_count=same_page_entries,
        first_entry_page=first_page,
        last_entry_page=last_page,
        last_page_coverage=last_page_coverage,
        heading_checks=heading_checks,
        heading_matches=heading_matches,
        reasons=tuple(reasons),
    )


def _explicit_level(title: str) -> int | None:
    if _EXPLICIT_MAJOR.match(title):
        return 1
    match = _NUMBERED_HEADING.match(title)
    if not match:
        return None
    if not _numbered_remainder_looks_like_title(match.group("title")):
        return None
    number = match.group("number")
    return min(4, number.count(".") + 1)


def _span_is_bold(span: dict[str, object]) -> bool:
    flags = span.get("flags", 0)
    font = str(span.get("font", "")).casefold()
    return (isinstance(flags, int) and bool(flags & 16)) or "bold" in font


def _body_font_size(document: fitz.Document) -> float:
    sizes: list[float] = []
    page_count = document.page_count
    if not page_count:
        return 11.0
    maximum_pages = min(40, page_count)
    stride = page_count / maximum_pages
    sampled = sorted({int(index * stride) for index in range(maximum_pages)})
    for page_number in sampled:
        page = document.load_page(page_number)
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = normalize_title(str(span.get("text", "")))
                    size = span.get("size")
                    if text and isinstance(size, (int, float)) and size > 0:
                        sizes.append(float(size))
    return median(sizes) if sizes else 11.0


def _line_candidates(
    document: fitz.Document,
    body_size: float,
) -> list[_LineCandidate]:
    candidates: list[_LineCandidate] = []
    for page_number, page in enumerate(document, start=1):
        page_height = max(1.0, page.rect.height)
        raw_lines: list[dict[str, object]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                title = normalize_title(
                    " ".join(str(span.get("text", "")) for span in spans)
                )
                bbox = line.get("bbox") or block.get("bbox")
                if not title or not bbox or len(bbox) != 4:
                    continue
                sizes = [
                    float(span["size"])
                    for span in spans
                    if isinstance(span.get("size"), (int, float))
                ]
                if not sizes:
                    continue
                x0, y0, x1, y1 = (float(value) for value in bbox)
                raw_lines.append(
                    {
                        "title": title,
                        "size": max(sizes),
                        "bold": any(_span_is_bold(span) for span in spans),
                        "x0": x0,
                        "y0": y0,
                        "x1": x1,
                        "y1": y1,
                    }
                )

        # Some PDFs store a heading number and its title as separate text
        # lines even though they share a visual baseline. Merge only nearby
        # fragments; this turns "1.1" + "Overview" into one candidate without
        # joining unrelated columns.
        raw_lines.sort(key=lambda item: (float(item["y0"]), float(item["x0"])))
        merged_lines: list[dict[str, object]] = []
        for raw in raw_lines:
            if merged_lines:
                previous = merged_lines[-1]
                same_baseline = abs(
                    float(raw["y0"]) - float(previous["y0"])
                ) <= 2.0
                nearby = (
                    -2.0
                    <= float(raw["x0"]) - float(previous["x1"])
                    <= max(48.0, page.rect.width * 0.08)
                )
                if same_baseline and nearby:
                    previous["title"] = normalize_title(
                        f'{previous["title"]} {raw["title"]}'
                    )
                    previous["size"] = max(
                        float(previous["size"]), float(raw["size"])
                    )
                    previous["bold"] = bool(previous["bold"]) or bool(raw["bold"])
                    previous["x1"] = max(
                        float(previous["x1"]), float(raw["x1"])
                    )
                    previous["y1"] = max(
                        float(previous["y1"]), float(raw["y1"])
                    )
                    continue
            merged_lines.append(dict(raw))

        for line in merged_lines:
            title = str(line["title"])
            if (
                len(title) < 2
                or len(title) > 180
                or len(title.split()) > 20
                or _PAGE_NUMBER.fullmatch(title)
            ):
                continue

            size = float(line["size"])
            bold = bool(line["bold"])
            explicit_level = _explicit_level(title)
            numbered_evidence = (
                explicit_level is not None
                and (
                    _EXPLICIT_MAJOR.match(title) is not None
                    or bold
                    or size >= body_size * 1.05
                )
            )
            if not (
                numbered_evidence
                or (
                    _looks_like_title(title)
                    and (
                        size >= body_size * 1.40
                        or (bold and size >= body_size * 1.15)
                    )
                )
            ):
                continue

            x0 = float(line["x0"])
            y0 = float(line["y0"])
            y1 = float(line["y1"])
            candidates.append(
                _LineCandidate(
                    page=page_number,
                    title=title,
                    size=size,
                    bold=bold,
                    top_ratio=y0 / page_height,
                    bottom_ratio=y1 / page_height,
                    y=y0,
                    x=x0,
                    explicit_level=explicit_level,
                )
            )
    return candidates


def propose_outline(document: fitz.Document) -> OutlineProposal:
    """Infer review-only headings from numbering and typography."""

    body_size = _body_font_size(document)
    candidates = _line_candidates(document, body_size)

    margin_occurrences: dict[str, set[int]] = {}
    all_occurrences: dict[str, set[int]] = {}
    for candidate in candidates:
        all_occurrences.setdefault(candidate.title.casefold(), set()).add(
            candidate.page
        )
        if candidate.top_ratio <= 0.12 or candidate.bottom_ratio >= 0.90:
            margin_occurrences.setdefault(
                candidate.title.casefold(), set()
            ).add(candidate.page)

    repeated_margin_titles = {
        title
        for title, pages in margin_occurrences.items()
        if len(pages) >= 3
    }
    repeated_display_titles = {
        candidate.title.casefold()
        for candidate in candidates
        if candidate.size >= body_size * 1.60
        and len(all_occurrences[candidate.title.casefold()]) >= 3
    }
    candidates = [
        candidate
        for candidate in candidates
        if candidate.title.casefold()
        not in repeated_margin_titles | repeated_display_titles
    ]
    candidates.sort(key=lambda item: (item.page, item.y, item.x, item.title))

    entries: list[OutlineEntry] = []
    seen: set[tuple[int, str]] = set()
    previous_level = 0
    truncated = False
    for candidate in candidates:
        key = candidate.page, candidate.title.casefold()
        if key in seen:
            continue
        seen.add(key)

        if candidate.explicit_level is not None:
            level = candidate.explicit_level
        elif candidate.size >= body_size * 1.40:
            level = 1
        elif candidate.size >= body_size * 1.15:
            level = 2
        else:
            level = 3
        level = min(level, previous_level + 1) if previous_level else 1
        entries.append(
            OutlineEntry(
                level=level,
                title=candidate.title,
                page=candidate.page,
                source_index=len(entries),
            )
        )
        previous_level = level
        if len(entries) >= MAXIMUM_PROPOSED_ENTRIES:
            truncated = True
            break

    warnings: list[str] = []
    if not entries:
        warnings.append("no_heading_candidates")
    if truncated:
        warnings.append("proposal_truncated")
    return OutlineProposal(entries=tuple(entries), warnings=tuple(warnings))


def analyze_outline(
    document: fitz.Document,
    toc: list[tuple[int, str, int]],
    *,
    likely_ocr_backed: bool,
) -> OutlineAnalysis:
    """Build all deterministic outline evidence for one open PDF."""

    normalization = normalize_outline(toc)
    assessment = assess_outline(document, normalization)
    proposal = None
    if assessment.needs_review and not likely_ocr_backed:
        proposal = propose_outline(document)
    return OutlineAnalysis(
        normalization=normalization,
        assessment=assessment,
        proposal=proposal,
    )
