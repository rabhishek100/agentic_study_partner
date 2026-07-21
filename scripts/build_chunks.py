"""Build citation-aware chunks and their SQLite FTS5 index."""

import argparse
from pathlib import Path

from retrieval.models import ChunkingConfig
from retrieval.sqlite import connect, connect_source, initialize, rebuild


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild derived chunks and BM25 index from canonical SQLite."
    )
    parser.add_argument(
        "--source-database",
        type=Path,
        default=Path("data/books.sqlite3"),
        help="Canonical SQLite database (default: data/books.sqlite3)",
    )
    parser.add_argument(
        "--retrieval-database",
        type=Path,
        default=Path("data/retrieval.sqlite3"),
        help="Derived SQLite database (default: data/retrieval.sqlite3)",
    )
    parser.add_argument(
        "--book-id",
        type=int,
        default=1,
        help="Canonical book ID to index (default: 1)",
    )
    parser.add_argument("--target-tokens", type=int, default=600)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--overlap-tokens", type=int, default=80)
    parser.add_argument("--encoding", default="cl100k_base")
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.source_database.resolve() == args.retrieval_database.resolve():
        raise ValueError("source and retrieval databases must be different files")
    config = ChunkingConfig(
        target_tokens=args.target_tokens,
        max_tokens=args.max_tokens,
        overlap_tokens=args.overlap_tokens,
        encoding_name=args.encoding,
    )
    source = connect_source(args.source_database)
    destination = connect(args.retrieval_database)
    try:
        initialize(destination)
        summary = rebuild(source, destination, args.book_id, config=config)
    finally:
        source.close()
        destination.close()

    print(
        f"Built {summary.chunk_count} chunks from "
        f"{summary.source_node_count} nodes for book {summary.book_id} "
        f"({summary.title!r}) in {args.retrieval_database}"
    )


if __name__ == "__main__":
    main()
