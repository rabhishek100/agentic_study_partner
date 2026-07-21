"""Rebuild the SQLite book database from cached parser output."""

import argparse
from hashlib import sha256
from pathlib import Path

import fitz

from parsing.parser import PARSER_VERSION, load_parsed_book
from storage.sqlite import connect, ingest_book, initialize


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
        description="Rebuild SQLite from cached ParsedBook JSON."
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("cache/parsed_book.json"),
        help="ParsedBook JSON cache (default: cache/parsed_book.json)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/books.sqlite3"),
        help="SQLite database (default: data/books.sqlite3)",
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
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
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

    connection = connect(args.database)
    try:
        initialize(connection)
        book_id = ingest_book(
            connection,
            book,
            title=title,
            author=author,
            file_hash=hash_file(source_pdf),
            page_count=page_count,
            parser_version=PARSER_VERSION,
            metadata={"pdf": source_metadata},
            replace=args.replace,
        )
    finally:
        connection.close()

    print(
        f"Imported book {book_id}: {title!r} "
        f"({len(book.sections)} nodes) into {args.database}"
    )


if __name__ == "__main__":
    main()
