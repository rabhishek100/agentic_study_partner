"""Build citation-aware chunks and Postgres full-text index rows."""


import argparse
from observability import traced
from retrieval.models import ChunkingConfig
from retrieval.postgres import rebuild
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild derived chunks and full-text rows in Postgres."
    )
    parser.add_argument(
        "--database-url",
        help="Postgres URL; defaults to DATABASE_URL",
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
    parser.add_argument(
        "--owner-id",
        help="Owner UUID; defaults to DEFAULT_OWNER_ID",
    )
    return parser


@traced("scripts.build_chunks.main", flow="cli")
def main() -> None:
    args = build_argument_parser().parse_args()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    config = ChunkingConfig(
        target_tokens=args.target_tokens,
        max_tokens=args.max_tokens,
        overlap_tokens=args.overlap_tokens,
        encoding_name=args.encoding,
    )
    with database_connection(args.database_url) as connection:
        summary = rebuild(
            connection,
            args.book_id,
            owner_id=owner_id,
            config=config,
        )

    print(
        f"Built {summary.chunk_count} chunks from "
        f"{summary.source_node_count} nodes for book {summary.book_id} "
        f"({summary.title!r}) in Postgres"
    )


if __name__ == "__main__":
    main()
