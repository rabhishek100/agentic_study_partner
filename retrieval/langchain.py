"""Thin LangChain adapter over the project's explicit retrieval layer."""

from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from .reranker import (
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
    LocalCrossEncoder,
)
from .search import RetrievalMode, retrieve
from .sqlite import connect
from .vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    LocalEmbedder,
    persistent_client,
)


@lru_cache(maxsize=2)
def _cached_embedder(model_name: str, revision: str) -> LocalEmbedder:
    return LocalEmbedder(model_name, revision=revision)


@lru_cache(maxsize=2)
def _cached_reranker(model_name: str, revision: str) -> LocalCrossEncoder:
    return LocalCrossEncoder(model_name, revision=revision)


@lru_cache(maxsize=2)
def _cached_chroma_client(chroma_path: str):
    return persistent_client(chroma_path)


def warm_models() -> None:
    """Load and cache the embedder and reranker ahead of the first request."""
    _cached_embedder(DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_REVISION)
    _cached_reranker(DEFAULT_RERANKER_MODEL, DEFAULT_RERANKER_REVISION)
    _cached_chroma_client(str(DEFAULT_CHROMA_PATH))


class BookRetriever(BaseRetriever):
    """Expose explicit project retrieval modes as LangChain documents."""

    database_path: str = "data/retrieval.sqlite3"
    chroma_path: str = str(DEFAULT_CHROMA_PATH)
    mode: RetrievalMode = "hybrid"
    book_id: int | None = None
    k: int = 5
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    reranker_model: str = DEFAULT_RERANKER_MODEL
    reranker_revision: str = DEFAULT_RERANKER_REVISION

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        embedder = (
            None
            if self.mode == "bm25"
            else _cached_embedder(self.embedding_model, self.embedding_revision)
        )
        reranker = (
            _cached_reranker(self.reranker_model, self.reranker_revision)
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
