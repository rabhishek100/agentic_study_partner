"""Check each book's chapters against the chapter list its publisher prints.

    uv run python -m scripts.evaluate_outlines --owner-id <owner>

Every check an outline can be given from inside this system passed on an
outline that had four sections promoted to chapters. Titles appeared on their
pages, levels nested, pages ascended. What those four did was steal 56 pages
from one real chapter and 6 from another, so summarizing chapter 2 retrieved
five pages of sixty — and reported nothing wrong, because nothing was wrong by
any measure the system could take of itself.

This is the measure it cannot take of itself. It is worth more than its size
suggests: an outline error is silent, survives every internal check, and
corrupts retrieval for the life of the book.
"""


import argparse
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

from observability import traced
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)


logger = logging.getLogger("study_partner.scripts.evaluate_outlines")

DEFAULT_CONTENTS = Path("evaluation/published_contents.json")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# A publisher writes "Metrics Monitoring"; the page prints "5 Metrics
# Monitoring and Alerting System". Neither is wrong, and an exact string
# comparison would call every such pair a miss.
_CHAPTER_PREFIX = re.compile(r"^\s*(?:chapter\s+)?\d+\s*[.:]?\s*", re.IGNORECASE)


def _comparable(title: str) -> str:
    without_number = _CHAPTER_PREFIX.sub("", title)
    folded = unicodedata.normalize("NFKD", without_number).casefold()
    return " ".join(_WORD.findall(folded))


def _matches(extracted: str, published: str) -> bool:
    """Whether two titles name the same chapter.

    Containment either way, because a publisher's listing and the book's own
    heading routinely differ by a trailing clause: "Metrics Monitoring" against
    "Metrics Monitoring and Alerting System".
    """

    left, right = _comparable(extracted), _comparable(published)
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id")
    parser.add_argument("--database-url")
    parser.add_argument("--contents", type=Path, default=DEFAULT_CONTENTS)
    return parser


@traced("scripts.evaluate_outlines.main", flow="evaluation")
def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = build_argument_parser().parse_args(argv)
    owner_id = (
        parse_owner_id(arguments.owner_id)
        if arguments.owner_id
        else environment_owner_id()
    )
    published = json.loads(arguments.contents.read_text())["books"]

    with database_connection(arguments.database_url, readonly=True) as connection:
        books = connection.execute(
            "select id, title from books where owner_id = %s and status = 'ready' order by id",
            (owner_id,),
        ).fetchall()
        chapters_by_book = {}
        for book in books:
            rows = connection.execute(
                """
                select title from nodes
                where book_id = %s and node_type = 'chapter' order by toc_index
                """,
                (book["id"],),
            ).fetchall()
            chapters_by_book[book["id"]] = [row["title"] for row in rows]

    exit_code = 0
    print(f"{'book':<44}{'found':>7}{'want':>6}{'recall':>9}{'precision':>11}")
    for entry in published:
        book = next(
            (b for b in books if entry["match_on"].casefold() in b["title"].casefold()),
            None,
        )
        if book is None:
            print(f"{entry['title'][:42]:<44}{'not ingested':>33}")
            continue

        extracted = chapters_by_book[book["id"]]
        expected = entry["chapters"]
        matched = [
            want for want in expected if any(_matches(got, want) for got in extracted)
        ]
        spurious = [
            got for got in extracted if not any(_matches(got, want) for want in expected)
        ]
        recall = len(matched) / len(expected) if expected else 0.0
        precision = (
            (len(extracted) - len(spurious)) / len(extracted) if extracted else 0.0
        )
        print(
            f"{entry['title'][:42]:<44}{len(extracted):>7}{len(expected):>6}"
            f"{recall:>9.2%}{precision:>11.2%}"
        )
        for want in expected:
            if want not in matched:
                print(f"    missing:  {want}")
                exit_code = 1
        for got in spurious:
            print(f"    spurious: {got}")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
