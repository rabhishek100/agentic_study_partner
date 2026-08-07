"""Check a linked document's stored pages against the PDF they came from.

Stored resource pages are derived data: the source PDF is canonical and the
pages are rebuildable from it. Nothing checked that they still match, so a
parse that silently degraded — a font whose glyphs stop mapping to characters,
a page that extracts empty, a stale row from an older parser — would look
exactly like a healthy index from the outside, and would show up only as
retrieval scoring worse than it should against questions nobody could prove
were unfair.

This is what proves or disproves that claim. It re-parses the source with the
same parser the ingest used, compares page for page, and reports the text
quality of what is stored:

    uv run python -m scripts.check_resource_text --resource-id <uuid> \
        --pdf evaluation/runs/video-course/cme295-lecture1/source/lecture-slides.pdf

Without `--pdf` it checks stored quality alone, which is all that is available
when the source lives on a volume this machine cannot reach.
"""

from __future__ import annotations

import argparse
from collections import Counter
import os
import sys
from pathlib import Path
from uuid import UUID

from psycopg.rows import dict_row
import psycopg

from video.resources import parse_pdf_pages


# English letter frequencies are stable enough that a letter falling to near
# zero across a whole document is a broken glyph map rather than a writing
# style. The check is deliberately document-wide: a single slide can be all
# diagram labels and legitimately contain no `s` at all.
COMMON_LETTERS = "etaoinshrdlu"
SUSPICIOUS_RATE = 0.005
MINIMUM_LETTERS = 2_000
REPLACEMENT = "�"


def _stored_pages(connection, *, resource_id: UUID) -> list[dict]:
    return connection.execute(
        """
        select page_number, text_content, parser_version, parser_config_hash
        from video.resource_pages
        where resource_id = %s order by page_number
        """,
        (resource_id,),
    ).fetchall()


def _quality(pages: list[dict]) -> dict:
    text = "".join(page["text_content"] for page in pages)
    letters = [character for character in text.lower() if character.isalpha()]
    counts = Counter(letters)
    total = len(letters)
    starved = (
        [
            letter
            for letter in COMMON_LETTERS
            if counts[letter] / total < SUSPICIOUS_RATE
        ]
        if total >= MINIMUM_LETTERS
        else []
    )
    return {
        "pages": len(pages),
        "empty_pages": sum(1 for page in pages if not page["text_content"].strip()),
        "letters": total,
        "replacement_characters": text.count(REPLACEMENT),
        "starved_letters": starved,
        "rates": {
            letter: round(counts[letter] / total, 4) if total else 0.0
            for letter in COMMON_LETTERS
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-id", required=True, type=UUID)
    parser.add_argument(
        "--pdf", type=Path, help="the source PDF, to compare page for page"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL"),
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        print("set --database-url, MIGRATION_DATABASE_URL, or DATABASE_URL")
        return 2

    with psycopg.connect(
        arguments.database_url, connect_timeout=20, row_factory=dict_row
    ) as connection:
        pages = _stored_pages(connection, resource_id=arguments.resource_id)

    if not pages:
        print(f"no stored pages for resource {arguments.resource_id}")
        return 1

    quality = _quality(pages)
    print(f"resource {arguments.resource_id}")
    print(f"  parser        {pages[0]['parser_version']}")
    print(f"  pages stored  {quality['pages']} ({quality['empty_pages']} empty)")
    print(f"  letters       {quality['letters']}")
    print(f"  U+FFFD        {quality['replacement_characters']}")
    rates = ", ".join(
        f"{letter}={rate:.3f}" for letter, rate in quality["rates"].items()
    )
    print(f"  letter rates  {rates}")

    failed = False
    if quality["replacement_characters"]:
        print("  ! unmappable glyphs reached the stored text")
        failed = True
    if quality["starved_letters"]:
        print(
            "  ! effectively absent from the whole document: "
            f"{', '.join(quality['starved_letters'])} — a broken glyph map, "
            "not a writing style"
        )
        failed = True

    if arguments.pdf:
        parsed = parse_pdf_pages(arguments.pdf)
        stored = {page["page_number"]: page["text_content"] for page in pages}
        differing = [
            page.page_number
            for page in parsed.pages
            if stored.get(page.page_number) != page.text
        ]
        print(f"  source        {arguments.pdf} ({parsed.page_count} pages)")
        if parsed.page_count != quality["pages"]:
            print("  ! the store and the source disagree on how many pages exist")
            failed = True
        if differing:
            print(
                f"  ! {len(differing)} pages differ from a fresh parse: "
                f"{differing[:12]}{' …' if len(differing) > 12 else ''}"
            )
            failed = True
        else:
            print("  stored text matches a fresh parse of the source exactly")

    print("  verdict:", "PROBLEMS FOUND" if failed else "healthy")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
