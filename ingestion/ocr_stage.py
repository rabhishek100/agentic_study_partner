"""The transcription stage: read a scanned book's pages, then pause for review.

Two properties shape this module.

*A page costs money.* Every completed page is committed before the next one
starts, so a worker that dies at page 700 resumes at 701 rather than paying
twice. A hard budget aborts the job with a named error instead of letting a
retry loop discover the number on a bill.

*Nothing here decides the hierarchy.* The stage ends by proposing headings and
handing them to a human. The proposal is evidence; only confirmation makes it
eligible for parsing.
"""

import logging
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID

import fitz

from parsing.markup import parse_page_markup

from .config import IngestionLimits
from .errors import ErrorCode, IngestionError
from .ocr import (
    FabricationAssessment,
    OcrBudget,
    OcrError,
    OcrProvider,
    PageTranscription,
    assess_fabrication,
    render_page,
)
from .ocr_store import OcrPageSummary, completed_pages, page_summary, record_page

logger = logging.getLogger("study_partner.ingestion.ocr_stage")

OCR_PROPOSER_VERSION = "transcribed-headings-v1"
CONTENTS_PROPOSER_VERSION = "printed-contents-v1"

# Fewer rows than this is not a contents listing that survived reading; the
# heading proposer covers more of the book than a two-entry table would.
MINIMUM_CONTENTS_OUTLINE = 3

# Same ceiling the typography proposer uses: past this, a proposal has stopped
# being a hierarchy a person can review.
MAXIMUM_PROPOSED_ENTRIES = 500

# A title shorter than this carries no word worth indexing; a stray digit read
# as a heading is the usual case.
MINIMUM_TITLE_CHARACTERS = 3

# Many books print the current section in a running footer. On a chapter's
# opening page there is no section yet, so that footer carries the page number
# alone. Measured against the published chapter list of one scanned book, this
# separated its eleven real chapters from four sections promoted beside them
# with no mistakes in either direction.
#
# It is only believed when the book demonstrably uses the convention, and it
# only ever demotes. Promoting on a missing footer would turn every page the
# transcription read badly into a chapter.
MINIMUM_SECTION_FOOTER_RATIO = 0.5
_FOOTER_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

_TITLE_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _comparable_title(title: str) -> str:
    """Normalize a heading so the same section reads the same everywhere."""

    return " ".join(
        _TITLE_WORD.findall(unicodedata.normalize("NFKD", title).casefold())
    )


# A Markdown heading, which is how the transcription marks a visual heading.
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>\S.*?)\s*$")


@dataclass(frozen=True)
class TranscriptionOutcome:
    """What one pass over a book's pages produced."""

    summary: OcrPageSummary
    pages_transcribed: int
    pages_reused: int

    def provenance(self) -> dict[str, object]:
        return {
            **self.summary.provenance(),
            "pages_transcribed": self.pages_transcribed,
            "pages_reused": self.pages_reused,
        }


def transcribe_book(
    source: Path,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    limits: IngestionLimits,
    provider: OcrProvider,
    reference: OcrProvider | None,
    open_connection: Callable[[], object],
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> TranscriptionOutcome:
    """Transcribe every page not already read under this exact reading.

    ``open_connection`` is a context-manager factory rather than a live
    connection: a page is committed in its own short transaction, and holding
    one open across hundreds of provider calls is exactly what the rest of this
    pipeline avoids.
    """

    with fitz.open(source) as document:
        page_count = document.page_count

        with open_connection() as connection:
            done = completed_pages(
                connection,
                owner_id=owner_id,
                job_id=job_id,
                model_id=provider.model_id,
                prompt_hash=provider.prompt_hash,
            )

        pending = [page for page in range(1, page_count + 1) if page not in done]
        budget = OcrBudget(
            max_pages=limits.max_pages,
            max_cost_usd=limits.max_ocr_cost_usd,
            pages_done=len(done),
            cost_usd=sum(page.cost_usd for page in done.values()),
        )
        logger.info(
            "transcribing job %s: %s pages, %s already read, %s pending",
            job_id,
            page_count,
            len(done),
            len(pending),
        )
        if on_progress is not None:
            on_progress(len(done), page_count)

        if not pending:
            with open_connection() as connection:
                summary = page_summary(connection, owner_id=owner_id, job_id=job_id)
            return TranscriptionOutcome(
                summary=summary, pages_transcribed=0, pages_reused=len(done)
            )

        # PyMuPDF is not safe to use from several threads against one document,
        # but rendering is a small fraction of a page's wall clock next to a
        # network round trip. One lock around the render keeps every thread
        # busy waiting on the provider instead of on each other.
        render_lock = threading.Lock()
        budget_lock = threading.Lock()
        completed = len(done)
        progress_lock = threading.Lock()

        def read(page: int) -> tuple[PageTranscription, FabricationAssessment]:
            with render_lock:
                image, actual_dpi = render_page(
                    document, page - 1, limits.ocr_render_dpi
                )
            transcription = provider.transcribe(image, "image/png", page)
            transcription = replace(transcription, render_dpi=actual_dpi)
            assessment = FabricationAssessment(verdict="unassessable")
            if reference is not None:
                try:
                    corroboration = reference.transcribe(image, "image/png", page)
                    assessment = assess_fabrication(
                        transcription.text, corroboration.text
                    )
                except Exception:  # noqa: BLE001 - the check is not the product
                    # A reference engine that fails leaves the page
                    # unassessable, which is what it is. Failing the book
                    # because its cross-check failed would be the gate
                    # blocking, which it is explicitly not allowed to do.
                    logger.warning("reference OCR failed on page %s", page)
            return transcription, assessment

        with ThreadPoolExecutor(max_workers=limits.ocr_concurrency) as pool:
            futures = {pool.submit(read, page): page for page in pending}
            try:
                for future in as_completed(futures):
                    page = futures[future]
                    transcription, assessment = future.result()

                    with budget_lock:
                        budget.charge(transcription)

                    with open_connection() as connection:
                        record_page(
                            connection,
                            owner_id=owner_id,
                            job_id=job_id,
                            transcription=transcription,
                            fabrication=assessment,
                        )

                    with progress_lock:
                        completed += 1
                        current = completed
                    if on_progress is not None:
                        on_progress(current, page_count)
                    if assessment.flagged:
                        logger.info(
                            "page %s flagged for review: %s uncorroborated words",
                            page,
                            assessment.longest_unsupported_run,
                        )
                    if should_stop is not None and should_stop():
                        raise OcrError("transcription stopped before completion")
            except BaseException:
                # Checkpoints already committed stay committed; a resumed
                # attempt picks up from them. Cancel only what has not started.
                for future in futures:
                    future.cancel()
                raise

    with open_connection() as connection:
        summary = page_summary(connection, owner_id=owner_id, job_id=job_id)
    return TranscriptionOutcome(
        summary=summary,
        pages_transcribed=len(pending),
        pages_reused=len(done),
    )


def propose_outline_from_transcription(
    pages: list[tuple[int, str]],
) -> list[tuple[int, str, int]]:
    """Read a candidate hierarchy off the transcription's heading markup.

    Two properties of transcribed headings shape this, and neither applies to a
    publisher's outline.

    *Heading depth is not globally consistent.* Pages are transcribed in
    isolation — deliberately, so one bad page cannot poison its neighbours —
    so nothing makes page 40 agree with page 200 about whether a given heading
    is `#` or `##`. Measured on one book: "Clarifying Requirements" appears at
    both depths, "Training" at three.

    *A recurring heading is a section, not a chapter.* A chapter title is
    unique to its chapter. In that same book eleven chapters each contain
    "Data Preparation", "Model Development" and "Evaluation", because every
    case study follows one template. Taking depth at face value put those at
    the top level beside the real chapters, so `list the chapters` answered
    with 31 entries of which half were template steps.

    Counting titles first fixes both: anything that recurs is refused the top
    level, which leaves the chapters to the titles that are actually unique.
    """

    counts: Counter[str] = Counter()
    headings: list[tuple[int, int, str]] = []
    footers: dict[int, str] = {}
    for page, text in pages:
        markup = parse_page_markup(text)
        footers[page] = markup.footer
        for line in markup.body.splitlines():
            match = _HEADING.match(line.strip())
            if match is None:
                continue
            title = " ".join(match.group("title").split())
            comparable = _comparable_title(title)
            if len(comparable) < MINIMUM_TITLE_CHARACTERS:
                continue
            headings.append((page, len(match.group("hashes")), title))
            counts[comparable] += 1

    named_footers = sum(1 for text in footers.values() if _FOOTER_WORD.search(text))
    section_footers = bool(footers) and (
        named_footers / len(footers) >= MINIMUM_SECTION_FOOTER_RATIO
    )

    entries: list[tuple[int, str, int]] = []
    # Observed heading depths in order of first appearance. Markdown depth is
    # not outline level: a book whose headings start at ## must still produce a
    # level-1 root, and one that skips from # to ### must not leave an entry
    # with no parent to attach to.
    depths: list[int] = []
    emitted_level = 0
    previous_page = 0

    for page, depth, title in headings:
        page = max(page, previous_page)
        comparable = _comparable_title(title)

        while depths and depths[-1] > depth:
            depths.pop()
        if not depths or depths[-1] < depth:
            depths.append(depth)
        level = depths.index(depth) + 1

        if counts[comparable] > 1:
            # It appears elsewhere in the book, so it names a part of something
            # rather than the whole of one.
            level = max(level, 2)

        if section_footers and _FOOTER_WORD.search(footers.get(page, "")):
            # This page's running footer names the section it sits inside, so
            # the page is not opening a chapter.
            level = max(level, 2)

        # Clamp to one deeper than the last entry actually emitted; the depth
        # stack tracks headings that were skipped, so it can advance while
        # nothing is emitted and orphan the next entry.
        level = min(level, emitted_level + 1)

        if entries and _comparable_title(entries[-1][1]) == comparable:
            # The same heading continuing across a page break, not a new
            # section. Only an immediate repeat: the same title under a
            # different chapter is a different section and is kept.
            continue

        emitted_level = level
        previous_page = page
        entries.append((level, title, page))

    return _within_review_budget(entries)


def _within_review_budget(
    entries: list[tuple[int, str, int]],
) -> list[tuple[int, str, int]]:
    """Trim the deepest levels until a reviewer can work through the list.

    Trimming by depth rather than by position matters. Cutting the tail leaves
    the end of the book with no entries at all and therefore unaddressable,
    while dropping the deepest level costs granularity evenly and keeps every
    chapter.
    """

    if len(entries) <= MAXIMUM_PROPOSED_ENTRIES:
        return entries

    trimmed = list(entries)
    while len(trimmed) > MAXIMUM_PROPOSED_ENTRIES:
        deepest = max(level for level, _, _ in trimmed)
        if deepest <= 1:
            break
        logger.info(
            "proposal has %s entries; dropping level %s to fit the review budget",
            len(trimmed),
            deepest,
        )
        trimmed = [entry for entry in trimmed if entry[0] < deepest]
    return trimmed[:MAXIMUM_PROPOSED_ENTRIES]


@dataclass(frozen=True)
class OutlineProposal:
    """A candidate hierarchy for a transcribed book, and where it came from."""

    entries: tuple[tuple[int, str, int], ...]
    source: str
    proposer_version: str
    warnings: tuple[str, ...] = ()
    provenance_json: dict[str, object] = field(default_factory=dict)


def propose_outline(
    pages: list[tuple[int, str]], *, page_count: int | None = None
) -> OutlineProposal:
    """Prefer the book's own contents page; fall back to its headings.

    A contents listing is the book's own statement of its structure, typeset by
    the people who wrote it, and it names chapters the body pages never repeat
    in a recognisable form. Heading detection is what remains when there is no
    listing — for one scan in this corpus there is none, because the copy it
    was made from had its front matter removed.
    """

    from parsing.contents import contents_to_outline, parse_printed_contents
    from parsing.transcript import printed_numbering

    numbering = printed_numbering(pages)
    contents = parse_printed_contents(pages)
    # The book's length, not the last page transcribed: an entry pointing
    # past the end must be refused, and inferring the bound from the input
    # makes that check vacuous.
    last_page = page_count or max((page for page, _ in pages), default=0)

    if contents and numbering.matched_pages:
        entries, warnings = contents_to_outline(
            contents, numbering=numbering, page_count=last_page
        )
        if len(entries) >= MINIMUM_CONTENTS_OUTLINE:
            if numbering.drifts:
                span = numbering.offset_range
                warnings.append(
                    f"printed page numbers drift against PDF pages (offset "
                    f"{span[0]} to {span[1]}), so this scan is missing pages"
                )
            return OutlineProposal(
                entries=tuple(entries),
                source="printed_contents",
                proposer_version=CONTENTS_PROPOSER_VERSION,
                warnings=tuple(warnings),
                provenance_json={
                    "contents": contents.provenance(),
                    "printed_numbering": numbering.provenance(),
                },
            )

    return OutlineProposal(
        entries=tuple(propose_outline_from_transcription(pages)),
        source="transcribed_headings",
        proposer_version=OCR_PROPOSER_VERSION,
        provenance_json={"printed_numbering": numbering.provenance()},
    )


def require_proposable(
    entries: list[tuple[int, str, int]], *, page_count: int
) -> list[tuple[int, str, int]]:
    """Refuse to pause for review on a proposal a reviewer cannot work with.

    An empty proposal means the transcription found no headings at all, which
    is a real outcome for a book of running prose. Failing loudly beats parking
    the job in a review state with nothing in it to review.
    """

    if not entries:
        raise IngestionError(
            ErrorCode.MISSING_TABLE_OF_CONTENTS,
            detail="transcription produced no headings to propose",
        )
    return [
        (level, title, min(max(page, 1), page_count)) for level, title, page in entries
    ]
