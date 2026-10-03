"""Synchronize rebuildable pgvector rows from Postgres chunks."""


import argparse

from dotenv import load_dotenv

from observability import traced
from retrieval.vector import (
    DEFAULT_EMBEDDING_MODEL,
    build_embedder,
    rebuild_vector_index,
)
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build exact-search pgvector rows from derived chunks."
    )
    parser.add_argument("--database-url", help="Defaults to DATABASE_URL")
    parser.add_argument(
        "--embedding-model",
        help=(
            "OpenRouter embedding model id; defaults to "
            "OPENROUTER_EMBEDDING_MODEL, then " + DEFAULT_EMBEDDING_MODEL
        ),
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--owner-id",
        help="Owner UUID; defaults to DEFAULT_OWNER_ID",
    )
    return parser


@traced("scripts.build_vector_index.main", flow="cli")
def main() -> None:
    load_dotenv()
    args = build_argument_parser().parse_args()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    embedder = build_embedder(args.embedding_model)
    with database_connection(args.database_url) as connection:
        summary = rebuild_vector_index(
            connection,
            embedder=embedder,
            owner_id=owner_id,
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
    load_dotenv()
    main()
