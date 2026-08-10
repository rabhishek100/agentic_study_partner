"""Import cached parser output into canonical Postgres storage."""

import argparse
from hashlib import sha256
from pathlib import Path

import fitz

from parsing.parser import PARSER_VERSION, load_parsed_book
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)
from storage.postgres import ingest_book


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pdf(path: Path) -> tuple[int, list[tuple[int, str, int]], dict[str, str]]:
    with fitz.open(path) as document:
        metadata = {
            key: value
            for key, value in document.metadata.items()
            if value not in (None, "")
        }
        toc = [tuple(entry) for entry in document.get_toc()]
        return document.page_count, toc, metadata


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import cached ParsedBook JSON into Postgres."
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("cache/parsed_book.json"),
        help="ParsedBook JSON cache (default: cache/parsed_book.json)",
    )
    parser.add_argument(
        "--database-url",
        help="Postgres URL; defaults to DATABASE_URL",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="PDF to hash; defaults to the source stored in ParsedBook",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace a book with the same PDF hash",
    )
    parser.add_argument(
        "--document-type",
        choices=["book", "paper"],
        default="book",
        help="Type of document (default: book)",
    )
    parser.add_argument(
        "--owner-id",
        help="Owner UUID; defaults to DEFAULT_OWNER_ID",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    book = load_parsed_book(args.cache)
    source_pdf = args.source or Path(book.source)
    if not source_pdf.is_file():
        raise FileNotFoundError(
            f"source PDF not found: {source_pdf}; pass --source explicitly"
        )

    page_count, source_toc, source_metadata = read_pdf(source_pdf)
    if source_toc != book.toc:
        raise ValueError(
            "cached ParsedBook TOC does not match the source PDF; "
            "reparse the PDF or choose the matching cache"
        )
    if args.source is not None:
        book = book.model_copy(update={"source": str(source_pdf)})

    title = source_metadata.get("title") or source_pdf.stem
    author = source_metadata.get("author")

    with database_connection(args.database_url) as connection:
        book_id = ingest_book(
            connection,
            book,
            owner_id=owner_id,
            title=title,
            author=author,
            file_hash=hash_file(source_pdf),
            page_count=page_count,
            parser_version=PARSER_VERSION,
            metadata={"pdf": source_metadata},
            document_type=args.document_type,
            replace=args.replace,
        )
    print(
        f"Imported book {book_id}: {title!r} ({len(book.sections)} nodes) into Postgres"
    )


if __name__ == "__main__":
    main()
