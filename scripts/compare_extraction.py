"""Compare selective layout extraction against a whole-document parse.

Selective extraction skips the layout model on pages that hold neither an
image nor a table. That is only acceptable if it finds the same content, so
this script parses a real book both ways and reports the difference in
sections, blocks, tables, images, characters, and per-page text.

Run it against any book whose extraction you intend to trust:

    uv run python -m scripts.compare_extraction sources/books/example.pdf
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

from parsing.parser import (
    assign_elements,
    build_sections,
    extract_elements,
    extract_toc,
    pages_needing_layout,
)
from parsing.models import ParsedBook


def parse_with(source: Path, *, selective: bool, workspace: Path) -> ParsedBook:
    toc, page_count = extract_toc(source)
    sections = build_sections(toc, page_count)
    cache = workspace / f"{'selective' if selective else 'full'}.json"
    elements = extract_elements(source, cache_path=cache, selective=selective)
    assign_elements(elements, sections)
    return ParsedBook(source=str(source), toc=toc, sections=sections)


def totals(book: ParsedBook) -> dict[str, int]:
    return {
        "sections": len(book.sections),
        "blocks": sum(len(section.texts) for section in book.sections),
        "tables": sum(len(section.tables) for section in book.sections),
        "images": sum(len(section.images) for section in book.sections),
        "characters": sum(len(section.full_text) for section in book.sections),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    arguments = parser.parse_args()

    source = arguments.source
    if not source.exists():
        print(f"no such file: {source}")
        return 2

    rich = pages_needing_layout(source)
    _, page_count = extract_toc(source)
    print(
        f"{source.name}: {page_count} pages, {len(rich)} need layout "
        f"({len(rich) / page_count:.0%})\n"
    )

    with tempfile.TemporaryDirectory(prefix="extract-compare-") as directory:
        workspace = Path(directory)

        start = time.time()
        full = parse_with(source, selective=False, workspace=workspace)
        full_seconds = time.time() - start
        print(f"whole-document hi_res: {full_seconds / 60:.1f} min")

        start = time.time()
        selective = parse_with(source, selective=True, workspace=workspace)
        selective_seconds = time.time() - start
        print(f"selective layout:      {selective_seconds / 60:.1f} min")

    a, b = totals(full), totals(selective)
    print(f"\n{'metric':12} {'whole':>10} {'selective':>10} {'delta':>10}")
    worse = []
    for key in a:
        delta = b[key] - a[key]
        print(f"{key:12} {a[key]:10} {b[key]:10} {delta:+10}")
        # Losing a table or an image loses canonical content outright.
        if key in {"tables", "images"} and delta < 0:
            worse.append(key)
        if key == "characters" and b[key] < a[key] * 0.98:
            worse.append(key)

    speedup = full_seconds / selective_seconds if selective_seconds else 0
    print(f"\nspeedup: {speedup:.2f}x")

    # Per-page text coverage: a page that lost its text is invisible in totals
    # if another page gained some.
    lost_pages = []
    for index, (one, two) in enumerate(zip(full.sections, selective.sections)):
        if len(one.full_text) and not len(two.full_text):
            lost_pages.append(index)
    if lost_pages:
        worse.append("empty sections")
        print(f"sections that lost all text: {lost_pages[:10]}")

    if worse:
        print(f"\nREGRESSION in {', '.join(sorted(set(worse)))} - do not adopt")
        return 1
    print("\nselective extraction preserved tables, images, and text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
