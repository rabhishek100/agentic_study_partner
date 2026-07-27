"""Compare block-level OCR against Unstructured's full-page default.

`hi_res` runs Tesseract over every page by default, re-reading text that
pdfminer has already taken out of the file. Restricting OCR to regions the
text layer does not cover is roughly twice as quick, but "quicker" is only
worth having if the book comes out the same, so this parses a real book both
ways and reports the difference in sections, blocks, tables, images and text.

The one difference it expects to find is text drawn *inside* a figure - chart
axis labels, words in a screenshot - which the full-page pass reads and the
block pass does not. That text is sampled rather than merely counted, so the
trade is visible instead of hidden inside a character total.

    uv run python -m scripts.compare_ocr_mode sources/books/example.pdf
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

from parsing.parser import assign_elements, build_sections, extract_elements, extract_toc
from parsing.models import ParsedBook
from scripts.compare_extraction import totals


def parse_with(source: Path, *, full_page: bool, workspace: Path) -> ParsedBook:
    """Parse the book with one OCR mode.

    The mode is read from the environment inside the parser, so it reaches the
    pool's worker processes the same way it reaches the worker on Railway.
    """

    os.environ["PARSER_FULL_PAGE_OCR"] = "1" if full_page else "0"
    toc, page_count = extract_toc(source)
    sections = build_sections(toc, page_count)
    cache = workspace / f"{'full-page' if full_page else 'block'}.json"
    elements = extract_elements(source, cache_path=cache, selective=False)
    assign_elements(elements, sections)
    return ParsedBook(source=str(source), toc=toc, sections=sections)


def strings(book: ParsedBook) -> list[str]:
    found = []
    for section in book.sections:
        found.extend(text.text.strip() for text in section.texts)
        found.extend(table.text.strip() for table in section.tables)
    return [text for text in found if text]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    arguments = parser.parse_args()

    source = arguments.source
    if not source.exists():
        print(f"no such file: {source}")
        return 2

    _, page_count = extract_toc(source)
    print(f"{source.name}: {page_count} pages\n", flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix="ocr-compare-") as directory:
            workspace = Path(directory)

            start = time.time()
            full = parse_with(source, full_page=True, workspace=workspace)
            full_seconds = time.time() - start
            print(
                f"full-page OCR (old default): {full_seconds / 60:.1f} min "
                f"({full_seconds / page_count:.2f} s/page)",
                flush=True,
            )

            start = time.time()
            block = parse_with(source, full_page=False, workspace=workspace)
            block_seconds = time.time() - start
            print(
                f"block OCR (new default):     {block_seconds / 60:.1f} min "
                f"({block_seconds / page_count:.2f} s/page)",
                flush=True,
            )
    finally:
        os.environ.pop("PARSER_FULL_PAGE_OCR", None)

    a, b = totals(full), totals(block)
    print(f"\n{'metric':12} {'full-page':>12} {'block':>12} {'delta':>10}")
    worse = []
    for key in a:
        delta = b[key] - a[key]
        print(f"{key:12} {a[key]:12} {b[key]:12} {delta:+10}")
        # Losing a table or an image loses canonical content outright.
        if key in {"tables", "images"} and delta < 0:
            worse.append(key)
        if key == "characters" and b[key] < a[key] * 0.98:
            worse.append(key)

    speedup = full_seconds / block_seconds if block_seconds else 0
    print(f"\nspeedup: {speedup:.2f}x")

    # A section that lost all its text is invisible in the totals if another
    # section gained some.
    lost_sections = [
        index
        for index, (one, two) in enumerate(zip(full.sections, block.sections))
        if one.full_text and not two.full_text
    ]
    if lost_sections:
        worse.append("empty sections")
        print(f"sections that lost all text: {lost_sections[:10]}")

    # What full-page OCR read and block OCR did not: expected to be text drawn
    # inside figures. Printed so the trade is inspected, not assumed.
    only_full = [text for text in strings(full) if text not in set(strings(block))]
    print(f"\nstrings only the full-page pass found: {len(only_full)}")
    for text in only_full[:15]:
        print("   -", " ".join(text.split())[:100])

    if worse:
        print(f"\nREGRESSION in {', '.join(sorted(set(worse)))} - do not adopt")
        return 1
    print("\nblock OCR preserved tables, images, and text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
