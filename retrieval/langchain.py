"""Thin LangChain adapter over the project's explicit retrieval layer."""

import os
from functools import lru_cache

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from storage.database import connection

from .postgres import SearchResult
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


def warm_models(*, include_reranker: bool = False) -> None:
    """Construct clients needed by the default retrieval path."""
    _cached_embedder(os.getenv("OPENROUTER_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL)
    if include_reranker:
        _cached_reranker(
            os.getenv("OPENROUTER_RERANKER_MODEL") or DEFAULT_RERANKER_MODEL
        )


def document_from_result(result: SearchResult) -> Document:
    """Render one search result as the document shape answering expects.

    Shared with the anchored-chunk path in `study.query`, which loads chunks by
    id rather than by search: an answer must not be able to tell a pinned chunk
    from a retrieved one by the metadata it carries.
    """

    return Document(
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


class BookRetriever(BaseRetriever):
    """Expose explicit project retrieval modes as LangChain documents."""

    database_url: str = os.getenv("DATABASE_URL", "")
    owner_id: str
    mode: RetrievalMode = "hybrid"
    book_id: int | None = None
    book_ids: list[int] | None = None
    k: int = 5
    unique_nodes: bool = True
    embedding_model: str = Field(
        default_factory=lambda: (
            os.getenv("OPENROUTER_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
        )
    )
    reranker_model: str = Field(
        default_factory=lambda: (
            os.getenv("OPENROUTER_RERANKER_MODEL") or DEFAULT_RERANKER_MODEL
        )
    )

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        embedder = (
            None if self.mode == "bm25" else _cached_embedder(self.embedding_model)
        )
        reranker = (
            _cached_reranker(self.reranker_model)
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
                book_ids=self.book_ids,
                limit=self.k,
                unique_nodes=self.unique_nodes,
                embedder=embedder,
                reranker=reranker,
            )
        return [document_from_result(result) for result in results]
