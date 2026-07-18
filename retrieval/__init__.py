"""Derived chunking and lexical retrieval."""

from .chunking import CHUNKER_VERSION, build_book_chunks
from .models import Chunk, ChunkSource, ChunkingConfig
from .sqlite import (
    BuildSummary,
    SearchResult,
    connect,
    connect_source,
    initialize,
    rebuild,
    search,
)

__all__ = [
    "CHUNKER_VERSION",
    "BuildSummary",
    "Chunk",
    "ChunkSource",
    "ChunkingConfig",
    "SearchResult",
    "build_book_chunks",
    "connect",
    "connect_source",
    "initialize",
    "rebuild",
    "search",
]
