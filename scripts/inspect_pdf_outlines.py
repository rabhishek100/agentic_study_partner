"""Print deterministic preflight and outline diagnostics for PDFs.

Usage:
    uv run python -m scripts.inspect_pdf_outlines path/to/book.pdf
    uv run python -m scripts.inspect_pdf_outlines path/to/pdf-directory
"""

import argparse
from pathlib import Path

from ingestion.config import load_limits
from ingestion.preflight import inspect_pdf


def _pdfs(paths: list[Path]) -> list[Path]:
    sources: list[Path] = []
    for path in paths:
        if path.is_dir():
            sources.extend(sorted(path.glob("*.pdf")))
        elif path.is_file():
            sources.append(path)
        else:
            raise FileNotFoundError(path)
    return sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--show-proposal",
        action="store_true",
        help="print every review-only proposed outline row",
    )
    arguments = parser.parse_args()

    try:
        sources = _pdfs(arguments.paths)
    except FileNotFoundError as error:
        parser.error(f"no such file or directory: {error.args[0]}")
    if not sources:
        parser.error("no PDFs found")

    limits = load_limits()
    for source in sources:
        report = inspect_pdf(source, limits=limits)
        normalization = report.outline.normalization
        assessment = report.outline.assessment
        proposal = report.outline.proposal
        print(source.name)
        print(
            f"  class={report.document_class} action={report.decision.action} "
            f"reasons={','.join(report.decision.reasons) or 'none'}"
        )
        print(
            f"  outline raw={len(report.toc)} "
            f"normalized={len(normalization.entries)} "
            f"dropped={len(normalization.dropped_entries)} "
            f"same_page={assessment.same_page_entry_count} "
            f"suspicious={assessment.suspicious_title_count}"
        )
        print(
            f"  heading_match={assessment.heading_matches}/"
            f"{assessment.heading_checks} "
            f"last_page_coverage={assessment.last_page_coverage:.1%} "
            f"proposal={len(proposal.entries) if proposal else 0}"
        )
        if arguments.show_proposal and proposal:
            for entry in proposal.entries:
                print(
                    f"    L{entry.level} p.{entry.page}: {entry.title}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
