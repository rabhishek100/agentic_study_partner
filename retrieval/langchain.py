"""Thin LangChain adapter over the project's explicit retrieval layer."""

from functools import lru_cache
import os

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from storage.database import connection, resolve_owner_id
from .reranker import DEFAULT_RERANKER_MODEL, Reranker, build_reranker
from .search import RetrievalMode, retrieve
from .vector import (
    DEFAULT_EMBEDDING_MODEL,
    Embedder,
    build_embedder,
)


@lru_cache(maxsize=4)
def _cached_embedder(provider: str) -> Embedder:
    """Cache by OpenRouter model id.

    Must match the model provenance stored with the active pgvector rows;
    vector search filters out incompatible embedding spaces.
    """
    return build_embedder(provider)


@lru_cache(maxsize=4)
def _cached_reranker(provider: str) -> Reranker:
    """Cache by OpenRouter model id."""
    return build_reranker(provider)


def warm_models() -> None:
    """Construct hosted model clients before the first request."""
    _cached_embedder(os.getenv("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_MODEL))
    _cached_reranker(os.getenv("RERANKER_PROVIDER", DEFAULT_RERANKER_MODEL))


class BookRetriever(BaseRetriever):
    """Expose explicit project retrieval modes as LangChain documents."""

    database_url: str = os.getenv("DATABASE_URL", "")
    owner_id: str = str(resolve_owner_id())
    mode: RetrievalMode = "hybrid"
    book_id: int | None = None
    k: int = 5
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_MODEL)
    reranker_provider: str = os.getenv("RERANKER_PROVIDER", DEFAULT_RERANKER_MODEL)

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        embedder = (
            None if self.mode == "bm25" else _cached_embedder(self.embedding_provider)
        )
        reranker = (
            _cached_reranker(self.reranker_provider)
            if self.mode == "hybrid_rerank"
            else None
        )
        with connection(self.database_url or None, readonly=True) as database:
            results = retrieve(
                database,
                query,
                mode=self.mode,
                owner_id=self.owner_id,
                book_id=self.book_id,
                limit=self.k,
                unique_nodes=True,
                embedder=embedder,
                reranker=reranker,
            )
        return [
            Document(
                page_content=result.text,
                metadata={
                    "chunk_id": result.chunk_id,
                    "book_id": result.source_book_id,
                    "node_id": result.source_node_id,
                    "chunk_index": result.chunk_index,
                    "section": result.section_title,
                    "path": result.path_text,
                    "start_page": result.start_page,
                    "end_page": result.end_page,
                    "content_types": list(result.content_types),
                    "score": result.score,
                    "retrieval_method": result.retrieval_method,
                },
            )
            for result in results
        ]
