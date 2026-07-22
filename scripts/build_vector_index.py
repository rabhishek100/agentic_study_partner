"""Synchronize rebuildable pgvector rows from Postgres chunks."""

import argparse

from dotenv import load_dotenv

from retrieval.vector import (
    DEFAULT_EMBEDDING_MODEL,
    build_embedder,
    rebuild_vector_index,
)
from storage.database import connection as database_connection


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build exact-search pgvector rows from derived chunks."
    )
    parser.add_argument("--database-url", help="Defaults to DATABASE_URL")
    parser.add_argument("--embedder", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--reset", action="store_true")
    return parser


def main() -> None:
    load_dotenv()
    args = build_argument_parser().parse_args()
    embedder = build_embedder(args.embedder)
    with database_connection(args.database_url) as connection:
        summary = rebuild_vector_index(
            connection,
            embedder=embedder,
            book_id=args.book_id,
            reset=args.reset,
            batch_size=args.batch_size,
        )
    print(
        f"Postgres: {summary.total_count} vectors; embedded "
        f"{summary.embedded_count}, unchanged {summary.unchanged_count}, "
        f"deleted {summary.deleted_count}; model={summary.model_name}, "
        f"dimension={summary.dimension}."
    )


if __name__ == "__main__":
    main()
