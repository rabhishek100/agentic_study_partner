"""Put a book's original PDF back where its record already points.

The canonical content, chunks, embeddings, and captions of an ingested book
all survive independently of the uploaded file, so a source object that goes
missing costs nothing but the reading pane — restoring it is a byte copy, not
a re-ingest.

The file is matched to a book by SHA-256, never by filename. `books.file_hash`
records exactly which bytes produced that book's pages, so a hash match is
proof that page 78 in the restored file is the page 78 the answers cite; a
filename is not.

    uv run python -m scripts.restore_book_source sources/books/*.pdf
    uv run python -m scripts.restore_book_source --check sources/books/*.pdf
"""

import argparse
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from ingestion.storage_objects import object_info, upload_object
from storage.database import connection as database_connection, parse_owner_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--owner-id", default=os.getenv("DEFAULT_OWNER_ID"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would be restored without uploading.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an object that is already present.",
    )
    arguments = parser.parse_args()
    if not arguments.owner_id:
        parser.error("set DEFAULT_OWNER_ID or pass --owner-id")
    owner = parse_owner_id(arguments.owner_id)

    with database_connection(readonly=True) as connection:
        books = {
            row["file_hash"]: dict(row)
            for row in connection.execute(
                """
                select id, title, file_hash, page_count,
                       source_storage_bucket, source_storage_path
                from books where owner_id = %s
                """,
                (owner,),
            ).fetchall()
        }

    unmatched: list[Path] = []
    for pdf in arguments.pdfs:
        if not pdf.is_file():
            print(f"skip  {pdf}: not a file")
            continue
        payload = pdf.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        book = books.get(digest)
        if book is None:
            unmatched.append(pdf)
            print(f"skip  {pdf.name}: no book has hash {digest[:16]}…")
            continue

        bucket = book["source_storage_bucket"]
        path = book["source_storage_path"]
        if not bucket or not path:
            print(f"skip  {pdf.name}: book {book['id']} records no storage path")
            continue

        existing = object_info(bucket, path)
        state = "present" if existing else "missing"
        print(
            f"book {book['id']}  {book['page_count']:>4}pp  object {state}  "
            f"{book['title'][:44]}"
        )
        if arguments.check:
            continue
        if existing and not arguments.overwrite:
            print("      already stored; pass --overwrite to replace")
            continue

        upload_object(bucket, path, payload, overwrite=arguments.overwrite or bool(existing))
        restored = object_info(bucket, path)
        print(
            f"      restored {len(payload):,} bytes"
            + (f", verified {restored.size_bytes:,} stored" if restored else "")
        )

    if unmatched:
        print(
            "\nUnmatched files are not a failure: a book is identified by the "
            "exact bytes that produced it, so a different edition or a "
            "re-exported copy will not match and must be uploaded as a new book."
        )


if __name__ == "__main__":
    main()
