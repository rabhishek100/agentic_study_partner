"""Thin LangChain adapter over the project's explicit retrieval layer."""

from functools import lru_cache
import os
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from .reranker import DEFAULT_RERANKER_MODEL, Reranker, build_reranker
from .search import RetrievalMode, retrieve
from .sqlite import connect
from .vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_EMBEDDING_MODEL,
    Embedder,
    build_chroma_client,
    build_embedder,
)


@lru_cache(maxsize=4)
def _cached_embedder(provider: str) -> Embedder:
    """Cache by OpenRouter model id.

    Must match whichever embedder built the active Chroma collection —
    vector_search validates this against the collection's stored metadata
    and raises rather than silently comparing embeddings from mismatched
    spaces.
    """
    return build_embedder(provider)


@lru_cache(maxsize=4)
def _cached_reranker(provider: str) -> Reranker:
    """Cache by OpenRouter model id."""
    return build_reranker(provider)


@lru_cache(maxsize=2)
def _cached_chroma_client(chroma_path: str):
    return build_chroma_client(chroma_path)


def warm_models() -> None:
    """Construct the embedder, reranker, and Chroma client ahead of the
    first request, so a bad OPENROUTER_API_KEY or Chroma Cloud credential
    fails at startup instead of on a user's first turn.
    """
    _cached_chroma_client(str(DEFAULT_CHROMA_PATH))
    _cached_embedder(os.getenv("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_MODEL))
    _cached_reranker(os.getenv("RERANKER_PROVIDER", DEFAULT_RERANKER_MODEL))


class BookRetriever(BaseRetriever):
    """Expose explicit project retrieval modes as LangChain documents."""

    database_path: str = "data/retrieval.sqlite3"
    chroma_path: str = str(DEFAULT_CHROMA_PATH)
    mode: RetrievalMode = "hybrid"
    book_id: int | None = None
    k: int = 5
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_MODEL)
    reranker_provider: str = os.getenv("RERANKER_PROVIDER", DEFAULT_RERANKER_MODEL)

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        embedder = (
            None
            if self.mode == "bm25"
            else _cached_embedder(self.embedding_provider)
        )
        reranker = (
            _cached_reranker(self.reranker_provider)
            if self.mode == "hybrid_rerank"
            else None
        )
        client = (
            None if self.mode == "bm25" else _cached_chroma_client(self.chroma_path)
        )
        with connect(Path(self.database_path)) as connection:
            results = retrieve(
                connection,
                query,
                mode=self.mode,
                book_id=self.book_id,
                limit=self.k,
                unique_nodes=True,
                chroma_path=self.chroma_path,
                client=client,
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
