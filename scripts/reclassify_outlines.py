"""Rename the structural roles of already-ingested books in place.

`node_type` is assigned at ingestion, not during parsing, and it is the only
thing the per-book chapter-level rule changed. The outline itself - titles,
levels, parents, page ranges, text, chunks, embeddings - is untouched by that
rule, so a book already in the database can be corrected by recomputing its
roles rather than re-parsing it. No PDF is read, no OCR runs, no model is
called, and nothing derived has to be rebuilt.

    uv run python -m scripts.reclassify_outlines --check
    uv run python -m scripts.reclassify_outlines
    uv run python -m scripts.reclassify_outlines --book-id 532

`--check` reports the change for every book and writes nothing. Without it the
updates are applied, one transaction per book.
"""

import argparse
import os

from dotenv import load_dotenv

load_dotenv()

from parsing.outline_roles import chapter_level, outline_roles
from storage.database import connection as database_connection, parse_owner_id


def _books(connection, owner, book_id):
    predicate = "and id = %s" if book_id else ""
    parameters = (owner, book_id) if book_id else (owner,)
    return connection.execute(
        f"select id, title from books where owner_id = %s {predicate} order by id",
        parameters,
    ).fetchall()


def _nodes(connection, owner, book_id):
    return connection.execute(
        """
        select id, toc_index, toc_level, title, node_type, start_page
        from nodes where owner_id = %s and book_id = %s
        order by toc_index
        """,
        (owner, book_id),
    ).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", default=os.getenv("DEFAULT_OWNER_ID"))
    parser.add_argument("--book-id", type=int)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would change without writing.",
    )
    arguments = parser.parse_args()
    if not arguments.owner_id:
        parser.error("set DEFAULT_OWNER_ID or pass --owner-id")
    owner = parse_owner_id(arguments.owner_id)

    with database_connection(readonly=arguments.check) as connection:
        books = _books(connection, owner, arguments.book_id)
        if not books:
            print("no books matched")
            return

        for book in books:
            rows = _nodes(connection, owner, book["id"])
            if not rows:
                print(f"book {book['id']}: no nodes, skipped")
                continue

            levels = [row["toc_level"] for row in rows]
            titles = [row["title"] for row in rows]
            # Pages distinguish a book's chapter sequence from a numbered list
            # inside one of its pages; omitting them is what let four
            # diffusion-model steps become a 351-page book's chapters.
            pages = [row["start_page"] for row in rows]
            detected = chapter_level(levels, titles, pages)
            roles = outline_roles(levels, titles, pages)

            changes = [
                (row["id"], row["node_type"], role)
                for row, role in zip(rows, roles, strict=True)
                if row["node_type"] != role
            ]
            chapters = [
                title for title, role in zip(titles, roles) if role == "chapter"
            ]
            before = sum(1 for row in rows if row["node_type"] == "chapter")

            level_note = (
                f"chapter level {detected}"
                if detected is not None
                else "no numbered run; depth fallback"
            )
            print(
                f"\nbook {book['id']}  {book['title'][:60]}\n"
                f"  {level_note}\n"
                f"  chapters: {before} -> {len(chapters)}"
                f"   ({len(changes)} of {len(rows)} nodes retyped)"
            )
            for title in chapters[:3]:
                print(f"    - {title[:66]}")
            if len(chapters) > 3:
                print(f"    ... and {len(chapters) - 3} more")

            if arguments.check or not changes:
                continue
            with connection.transaction():
                for node_id, _, role in changes:
                    connection.execute(
                        "update nodes set node_type = %s where id = %s and owner_id = %s",
                        (role, node_id, owner),
                    )
            print(f"  applied {len(changes)} updates")


if __name__ == "__main__":
    main()
