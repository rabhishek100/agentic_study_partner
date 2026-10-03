"""Smoke-test a PDF through the production preflight and parser contracts.

This deliberately stops before database persistence, captions, chunks, and
embeddings. It is the inexpensive way to prove that a real PDF's approved
outline and extracted sections agree:

    uv run python -m scripts.parse_book path/to/book.pdf
"""


import argparse
from collections import defaultdict
import tempfile
from pathlib import Path

from observability import traced
from ingestion.config import load_limits
from ingestion.outlines import normalize_title
from ingestion.pipeline import evaluate_extraction
from ingestion.preflight import (
    preflight,
    require_supported,
    validate_table_of_contents,
)
from parsing.models import NON_CONTENT_CATEGORIES
from parsing.parser import parse_book


@traced("scripts.parse_book.main", flow="cli")
def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("source", type=Path)
    arguments.add_argument(
        "--workspace",
        type=Path,
        help="retain parser caches here; defaults to an isolated temporary directory",
    )
    arguments.add_argument(
        "--review-proposal",
        action="store_true",
        help=(
            "smoke-test the deterministic review proposal instead of requiring "
            "an automatically approved outline; never confirms an ingestion job"
        ),
    )
    arguments.add_argument(
        "--drop-proposal-entry",
        type=int,
        action="append",
        default=[],
        metavar="N",
        help=(
            "remove one 1-based proposal row before the smoke test; repeat for "
            "each correction"
        ),
    )
    options = arguments.parse_args()

    source = options.source.resolve()
    if not source.is_file():
        arguments.error(f"no such PDF: {source}")

    report = preflight(source, limits=load_limits())
    if report.supported:
        approved_toc = report.normalized_toc
    elif options.review_proposal:
        proposal = report.outline.proposal
        if proposal is None or not proposal.entries:
            arguments.error("this PDF has no deterministic outline proposal")
        invalid_drops = [
            index
            for index in options.drop_proposal_entry
            if not 1 <= index <= len(proposal.entries)
        ]
        if invalid_drops:
            arguments.error(
                "proposal row numbers are out of range: "
                + ", ".join(str(index) for index in invalid_drops)
            )
        dropped = set(options.drop_proposal_entry)
        approved_toc = [
            entry
            for index, entry in enumerate(proposal.as_toc(), start=1)
            if index not in dropped
        ]
        validate_table_of_contents(approved_toc, report.page_count)
    else:
        require_supported(report)
        raise AssertionError("require_supported returned for a review-only PDF")
    print(
        f"{source.name}: {report.page_count} pages, "
        f"{len(report.toc)} raw outline rows, "
        f"{len(approved_toc)} approved rows",
        flush=True,
    )

    temporary = None
    if options.workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="parse-smoke-")
        workspace = Path(temporary.name)
    else:
        workspace = options.workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=True)

    def progress(completed: int, total: int) -> None:
        print(f"parsed batches: {completed}/{total}", flush=True)

    try:
        book = parse_book(
            source,
            force=True,
            book_cache=workspace / "parsed_book.json",
            elements_cache=workspace / "elements.json",
            on_batch=progress,
            toc_override=approved_toc,
        )
        metrics = evaluate_extraction(
            book,
            report,
            approved_toc=approved_toc,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()

    print("extraction quality gate: PASS")
    for name, value in metrics.items():
        print(f"  {name}: {value}")

    misplaced_same_page_sections = []
    for index, section in enumerate(book.sections[:-1]):
        following = book.sections[index + 1]
        if section.full_text or section.start_page != following.start_page:
            continue
        title = normalize_title(section.title).casefold()
        if any(
            title in normalize_title(block.text).casefold()
            for block in following.texts
            if block.page == section.start_page
        ):
            misplaced_same_page_sections.append((section, following))

    block_pages: dict[str, set[int]] = defaultdict(set)
    for section in book.sections:
        for block in section.texts:
            if (
                block.category not in NON_CONTENT_CATEGORIES
                and block.text
                and not block.text.startswith(("[IMAGE", "[TABLE"))
            ):
                block_pages[normalize_title(block.text)].add(block.page)
    repeated_boilerplate = [
        (text, len(pages))
        for text, pages in block_pages.items()
        if text and len(pages) >= max(5, report.page_count // 2)
    ]
    repeated_boilerplate.sort(key=lambda item: (-item[1], item[0]))

    if misplaced_same_page_sections:
        print("structural sanity check: FAIL")
        for section, following in misplaced_same_page_sections:
            print(
                f"  {section.label!r} is empty on p.{section.start_page}, "
                f"but its heading was assigned to {following.label!r}"
            )
    else:
        print("structural sanity check: PASS")

    if repeated_boilerplate:
        print("retrieval-noise warning:")
        for text, count in repeated_boilerplate:
            print(f"  repeated on {count} pages: {text[:120]}")

    print("top-level outline:")
    top_level_indices = [
        index for index, section in enumerate(book.sections) if section.level == 1
    ]
    for position, index in enumerate(top_level_indices):
        section = book.sections[index]
        if section.level != 1:
            continue
        stop = (
            top_level_indices[position + 1]
            if position + 1 < len(top_level_indices)
            else len(book.sections)
        )
        subtree = book.sections[index:stop]
        print(
            f"  {section.title} "
            f"(pp. {section.start_page}-{subtree[-1].end_page}) "
            f"| {sum(len(item.full_text) for item in subtree)} subtree chars"
        )
    return 1 if misplaced_same_page_sections else 0


if __name__ == "__main__":
    raise SystemExit(main())
