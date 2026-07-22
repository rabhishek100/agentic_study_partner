"""Derived chunking and lexical retrieval."""

from .chunking import CHUNKER_VERSION, build_book_chunks
from .models import Chunk, ChunkSource, ChunkingConfig
from .reranker import (
    DEFAULT_RERANKER_MODEL,
    OpenRouterReranker,
    build_reranker,
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
from .postgres import (
    BuildSummary,
    SearchResult,
    chunks_by_id,
    rebuild,
    search,
    search_result_from_row,
)
from .vector import (
    DEFAULT_EMBEDDING_MODEL,
    OpenRouterEmbedder,
    VectorBuildSummary,
    build_embedder,
    embedding_document,
    rebuild_vector_index,
    vector_search,
)

__all__ = [
    "CHUNKER_VERSION",
    "BuildSummary",
    "Chunk",
    "ChunkSource",
    "ChunkingConfig",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_RERANKER_MODEL",
    "OpenRouterEmbedder",
    "OpenRouterReranker",
    "RERANK_CANDIDATE_LIMIT",
    "RetrievalMode",
    "SearchResult",
    "VectorBuildSummary",
    "build_book_chunks",
    "build_embedder",
    "build_reranker",
    "chunks_by_id",
    "embedding_document",
    "hybrid_candidates",
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
