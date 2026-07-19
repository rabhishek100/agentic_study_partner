"""Thin LangChain adapter over the project's explicit retrieval layer."""

from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from .search import RetrievalMode, retrieve
from .sqlite import connect
from .vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    LocalEmbedder,
)


@lru_cache(maxsize=2)
def _cached_embedder(model_name: str, revision: str) -> LocalEmbedder:
    return LocalEmbedder(model_name, revision=revision)


class BookRetriever(BaseRetriever):
    """Expose normalized BM25/vector/hybrid results as LangChain documents."""

    database_path: str = "data/retrieval.sqlite3"
    chroma_path: str = str(DEFAULT_CHROMA_PATH)
    mode: RetrievalMode = "hybrid"
    book_id: int | None = None
    k: int = 5
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        embedder = (
            None
            if self.mode == "bm25"
            else _cached_embedder(self.embedding_model, self.embedding_revision)
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
                embedder=embedder,
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
