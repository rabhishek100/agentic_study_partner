"""Give a book a readable copy when its own bytes are not in Storage.

The reading pane opens the page an answer cites, which needs a PDF in Storage.
A book ingested through the operator path never uploaded one, and the scans
that take that path are precisely the ones too large to upload as they stand.

This renders the same pages small enough to store and records it as the book's
*viewer* copy. `source_storage_path` and `file_hash` are left alone: they name
the exact bytes the canonical content came from, and one tool matches a file to
its book by that hash, so overwriting them with a re-encode would quietly make
the book unrestorable.

    uv run python -m scripts.store_viewer_copy --book-id 536 --source book.pdf
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from ingestion.config import load_limits
from ingestion.errors import IngestionError
from ingestion.local_source import inspect_local_source
from ingestion.storage_objects import upload_object
from ingestion.viewer_copy import build_viewer_copy, needs_viewer_copy
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)


logger = logging.getLogger("study_partner.scripts.store_viewer_copy")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store a readable copy of a book for the reading pane."
    )
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument(
        "--source", type=Path, required=True, help="The original PDF on this machine"
    )
    parser.add_argument("--owner-id", help="Owner UUID; defaults to DEFAULT_OWNER_ID")
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Render a viewer copy even for a source that would fit as-is",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    arguments = build_argument_parser().parse_args(argv)
    owner_id = (
        parse_owner_id(arguments.owner_id)
        if arguments.owner_id
        else environment_owner_id()
    )
    limits = load_limits()

    try:
        source = inspect_local_source(arguments.source)
    except IngestionError as error:
        logger.error("%s", error.safe_message)
        return 2

    with database_connection(arguments.database_url) as connection:
        book = connection.execute(
            """
            select id, title, file_hash, page_count, status,
                   source_storage_bucket, source_storage_path
            from books where id = %s and owner_id = %s
            """,
            (arguments.book_id, owner_id),
        ).fetchone()
    if book is None:
        logger.error("no book %s for this owner", arguments.book_id)
        return 3
    if book["file_hash"] != source.sha256:
        logger.error(
            "this file is not book %s: hash %s does not match %s",
            arguments.book_id,
            source.sha256[:12],
            (book["file_hash"] or "")[:12],
        )
        return 4

    fits = not needs_viewer_copy(
        source.size_bytes, ceiling_bytes=limits.max_source_bytes
    )
    if fits and not arguments.force:
        logger.info(
            "%.1f MB fits under the ceiling; upload it as the source instead",
            source.size_bytes / 1e6,
        )
        return 0

    with tempfile.TemporaryDirectory() as scratch:
        destination = Path(scratch) / "viewer.pdf"
        logger.info("rendering a viewer copy of %.1f MB source", source.size_bytes / 1e6)
        copy = build_viewer_copy(
            source.path, destination, ceiling_bytes=limits.max_source_bytes
        )
        if copy is None:
            logger.error("no rendering of this book fits and stays readable")
            return 5

        path = f"{owner_id}/book-{arguments.book_id}/viewer.pdf"
        logger.info(
            "uploading %.1f MB at %s dpi q%s",
            copy.size_bytes / 1e6,
            copy.render_dpi,
            copy.jpeg_quality,
        )
        try:
            upload_object(
                limits.source_bucket,
                path,
                copy.path.read_bytes(),
                overwrite=True,
            )
        except IngestionError as error:
            logger.error("upload failed: %s", error.safe_message)
            return 6

    with database_connection(arguments.database_url) as connection:
        connection.execute(
            """
            update books
            set viewer_storage_bucket = %s, viewer_storage_path = %s
            where id = %s and owner_id = %s
            """,
            (limits.source_bucket, path, arguments.book_id, owner_id),
        )
    logger.info("book %s now has a viewer copy at %s", arguments.book_id, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
