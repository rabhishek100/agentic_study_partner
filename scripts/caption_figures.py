"""Caption a book's figures, and rebuild the derived data that uses them.

New uploads are captioned by the ingestion pipeline. This is the backfill for
books that were ingested before captioning existed, and the way to re-caption
a corpus with a better model.

Captions are derived, so re-running is safe: an unchanged figure that already
has a caption is left alone unless `--recaption` is passed.

    uv run python -m scripts.caption_figures --book-id 530
    uv run python -m scripts.caption_figures --all --dry-run

Captions become chunk text, so a book whose figures were just captioned needs
its chunks and embeddings rebuilt before the new text is searchable. `--rebuild`
does that; without it the script says what is left to do.
"""

import argparse
import os

from dotenv import load_dotenv

load_dotenv()

from ingestion.captions import OpenRouterCaptioner, caption_book_figures
from retrieval.postgres import rebuild as rebuild_chunks
from retrieval.vector import build_embedder, rebuild_vector_index
from storage.database import connection as database_connection, parse_owner_id


def _books(connection, *, owner_id, book_id: int | None) -> list[dict]:
    if book_id is not None:
        rows = connection.execute(
            "select id, title from books where id = %s and owner_id = %s",
            (book_id, owner_id),
        ).fetchall()
    else:
        rows = connection.execute(
            "select id, title from books where owner_id = %s order by id",
            (owner_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _pending(connection, *, owner_id, book_id: int) -> int:
    return connection.execute(
        """
        select count(*) as pending
        from image_blocks
        left join image_captions
          on image_captions.block_id = image_blocks.block_id
         and image_captions.owner_id = image_blocks.owner_id
        where image_blocks.book_id = %s
          and image_blocks.owner_id = %s
          and image_captions.block_id is null
        """,
        (book_id, owner_id),
    ).fetchone()["pending"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book-id", type=int, help="Caption one book.")
    group.add_argument(
        "--all",
        action="store_true",
        help="Caption every book this owner has.",
    )
    parser.add_argument("--owner-id", default=os.getenv("DEFAULT_OWNER_ID"))
    parser.add_argument(
        "--recaption",
        action="store_true",
        help="Replace existing captions instead of only filling gaps.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild chunks and embeddings so captions become searchable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many figures are missing captions and stop.",
    )
    arguments = parser.parse_args()
    if not arguments.owner_id:
        parser.error("set DEFAULT_OWNER_ID or pass --owner-id")
    owner = parse_owner_id(arguments.owner_id)

    with database_connection(readonly=True) as connection:
        books = _books(
            connection,
            owner_id=owner,
            book_id=None if arguments.all else arguments.book_id,
        )
        pending = {
            book["id"]: _pending(connection, owner_id=owner, book_id=book["id"])
            for book in books
        }

    if not books:
        raise SystemExit("no matching books for this owner")

    for book in books:
        print(f"book {book['id']}: {book['title'][:60]} — {pending[book['id']]} uncaptioned")
    if arguments.dry_run:
        print("\nDry run: nothing was captioned.")
        return

    captioner = OpenRouterCaptioner()
    print(f"\nCaptioning with {captioner.model_name}\n")

    for book in books:
        with database_connection() as connection:
            summary = caption_book_figures(
                connection,
                book["id"],
                owner_id=owner,
                captioner=captioner,
                only_missing=not arguments.recaption,
                # Flushed: stdout is block-buffered when redirected to a
                # file, and an unflushed progress line makes a long backfill
                # look stalled for minutes at a time.
                on_progress=lambda done, total, title=book["title"]: (
                    print(
                        f"  {title[:40]:42} {done}/{total}",
                        end="\r",
                        flush=True,
                    )
                ),
            )
        print(
            f"\nbook {book['id']}: captioned={summary.captioned} "
            f"reused={summary.reused} "
            f"boilerplate={summary.skipped_boilerplate} "
            f"small={summary.skipped_small} failed={summary.failed}"
        )

        if not arguments.rebuild:
            continue

        with database_connection() as connection:
            chunks = rebuild_chunks(connection, book["id"], owner_id=owner)
        print(f"  rebuilt {chunks.chunk_count} chunks")
        with database_connection() as connection:
            vectors = rebuild_vector_index(
                connection,
                embedder=build_embedder(),
                owner_id=owner,
                book_id=book["id"],
            )
        print(
            f"  embedded {vectors.embedded_count} "
            f"(reused {vectors.unchanged_count})"
        )

    if not arguments.rebuild:
        print(
            "\nCaptions are stored but not yet searchable: chunks embed caption "
            "text, so re-run with --rebuild (or rebuild chunks and embeddings "
            "separately) before figures can be retrieved on what they show."
        )


if __name__ == "__main__":
    main()
