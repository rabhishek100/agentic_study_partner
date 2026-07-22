"""Synchronize Chroma vectors from rebuildable SQLite chunks."""

import argparse
from pathlib import Path

from retrieval.sqlite import connect, connect_source
from retrieval.vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_COLLECTION,
    DEFAULT_EMBEDDING_MODEL,
    build_chroma_client,
    build_embedder,
    rebuild_vector_index,
    write_manifest,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the Chroma vector index from derived chunks."
    )
    parser.add_argument(
        "--source-database",
        type=Path,
        default=Path("data/books.sqlite3"),
    )
    parser.add_argument(
        "--retrieval-database",
        type=Path,
        default=Path("data/retrieval.sqlite3"),
    )
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--embedder",
        default=DEFAULT_EMBEDDING_MODEL,
        help=(
            "OpenRouter embedding model id to embed with "
            f"(default: {DEFAULT_EMBEDDING_MODEL})."
        ),
    )
    parser.add_argument(
        "--chroma-provider",
        default="local",
        help=(
            "'local' (default) opens the on-disk PersistentClient at "
            "--chroma-path, or 'cloud' connects to Chroma Cloud using "
            "CHROMA_API_KEY/CHROMA_TENANT/CHROMA_DATABASE."
        ),
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate an incompatible derived collection.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    embedder = build_embedder(args.embedder)
    retrieval = connect(args.retrieval_database)
    source = connect_source(args.source_database)
    try:
        summary = rebuild_vector_index(
            retrieval,
            source,
            client=build_chroma_client(args.chroma_path, provider=args.chroma_provider),
            embedder=embedder,
            collection_name=args.collection,
            book_id=args.book_id,
            reset=args.reset,
            batch_size=args.batch_size,
        )
    finally:
        retrieval.close()
        source.close()

    args.chroma_path.mkdir(parents=True, exist_ok=True)
    manifest = write_manifest(
        args.chroma_path,
        summary,
        max_sequence_length=embedder.max_sequence_length,
    )
    print(
        f"Chroma collection {summary.collection!r}: {summary.total_count} vectors; "
        f"embedded {summary.embedded_count}, unchanged {summary.unchanged_count}, "
        f"deleted {summary.deleted_count}. Manifest: {manifest}"
    )


if __name__ == "__main__":
    main()
