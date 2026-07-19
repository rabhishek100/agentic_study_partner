"""Derived chunking and lexical retrieval."""

from .chunking import CHUNKER_VERSION, build_book_chunks
from .models import Chunk, ChunkSource, ChunkingConfig
from .reranker import (
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
    LocalCrossEncoder,
    rerank,
    reranker_document,
)
from .search import (
    RERANK_CANDIDATE_LIMIT,
    RetrievalMode,
    hybrid_candidates,
    reciprocal_rank_fusion,
    retrieve,
)
from .sqlite import (
    BuildSummary,
    SearchResult,
    chunks_by_id,
    connect,
    connect_source,
    initialize,
    rebuild,
    search,
    search_result_from_row,
)
from .vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_COLLECTION,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    LocalEmbedder,
    VectorBuildSummary,
    embedding_document,
    persistent_client,
    rebuild_vector_index,
    vector_search,
)

__all__ = [
    "CHUNKER_VERSION",
    "BuildSummary",
    "Chunk",
    "ChunkSource",
    "ChunkingConfig",
    "DEFAULT_CHROMA_PATH",
    "DEFAULT_COLLECTION",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_REVISION",
    "DEFAULT_RERANKER_MODEL",
    "DEFAULT_RERANKER_REVISION",
    "LocalEmbedder",
    "LocalCrossEncoder",
    "RERANK_CANDIDATE_LIMIT",
    "RetrievalMode",
    "SearchResult",
    "VectorBuildSummary",
    "build_book_chunks",
    "chunks_by_id",
    "connect",
    "connect_source",
    "embedding_document",
    "initialize",
    "hybrid_candidates",
    "persistent_client",
    "rebuild",
    "rebuild_vector_index",
    "reciprocal_rank_fusion",
    "rerank",
    "reranker_document",
    "retrieve",
    "search",
    "search_result_from_row",
    "vector_search",
]
