"""Rebuildable pgvector persistence and exact semantic retrieval."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from typing import Protocol
from uuid import UUID

import httpx
from psycopg import Connection

from observability import provider_post, traced, record_metadata
from storage.database import parse_owner_id
from .models import book_scope
from .postgres import SearchResult, search_result_from_row


DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-large"
OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
DOCUMENT_FORMAT_VERSION = "hierarchy-v1"
POSTGRES_EMBEDDING_DIMENSION = 3072


class Embedder(Protocol):
    model_name: str
    model_revision: str
    device: str
    dimension: int
    max_sequence_length: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenRouterEmbedder:
    """Hosted embedding model through OpenRouter's compatible endpoint."""

    def __init__(
        self,
        model_name: str,
        *,
        max_sequence_length: int = 8192,
        timeout: float = 60.0,
        dimension: int = POSTGRES_EMBEDDING_DIMENSION,
    ) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for embeddings")
        self.model_name = model_name
        self.model_revision = "hosted"
        self.device = "api"
        self.max_sequence_length = max_sequence_length
        self.dimension = dimension
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self._query_cache: dict[str, list[float]] = {}

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = provider_post(
            self._client,
            OPENROUTER_EMBEDDINGS_URL,
            json={
                "model": self.model_name,
                "input": list(texts),
                "encoding_format": "float",
                "dimensions": self.dimension,
            },
        )
        response.raise_for_status()
        data = response.json()["data"]
        if len(data) != len(texts):
            raise ValueError(
                "embedding provider returned a different number of vectors "
                f"({len(data)}) than inputs ({len(texts)})"
            )
        indexes = [item.get("index") for item in data]
        if any(not isinstance(index, int) for index in indexes) or sorted(
            indexes
        ) != list(range(len(texts))):
            raise ValueError(
                f"embedding provider returned invalid input indexes: {indexes!r}"
            )
        ordered = sorted(data, key=lambda item: item["index"])
        # JSON decodes an exact-zero component as int, and psycopg refuses to
        # adapt a mixed int/float list, so normalize every component to float.
        embeddings = [
            [float(component) for component in item["embedding"]] for item in ordered
        ]
        mismatched = [
            len(value) for value in embeddings if len(value) != self.dimension
        ]
        if mismatched:
            raise ValueError(
                f"embedding provider returned dimensions {mismatched}; "
                f"expected {self.dimension}"
            )
        return embeddings

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts) if texts else []

    @traced("retrieval.vector.OpenRouterEmbedder.embed_query", flow="retrieval")
    def embed_query(self, text: str) -> list[float]:
        record_metadata(cache_hit=text in self._query_cache)
        if text not in self._query_cache:
            self._query_cache[text] = self._embed([text])[0]
        return self._query_cache[text]


def build_embedder(spec: str | None = None) -> Embedder:
    return OpenRouterEmbedder(
        spec or os.getenv("OPENROUTER_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
    )


@dataclass(frozen=True)
class VectorBuildSummary:
    model_name: str
    model_revision: str
    device: str
    dimension: int
    total_count: int
    embedded_count: int
    unchanged_count: int
    deleted_count: int
    book_ids: tuple[int, ...]


def embedding_document(book_title: str, path_text: str, text: str) -> str:
    return f"Book: {book_title}\nHierarchy: {path_text}\n\n{text}"


def embedding_input_hash(document: str) -> str:
    return sha256(document.encode("utf-8")).hexdigest()


def rebuild_vector_index(
    connection: Connection,
    *,
    embedder: Embedder,
    owner_id: str | UUID,
    book_id: int | None = None,
    reset: bool = False,
    batch_size: int = 32,
) -> VectorBuildSummary:
    """Synchronize pgvector rows without holding transactions over API calls."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if embedder.dimension != POSTGRES_EMBEDDING_DIMENSION:
        raise ValueError(
            f"database expects {POSTGRES_EMBEDDING_DIMENSION}-dimension vectors; "
            f"embedder reports {embedder.dimension}"
        )
    owner = parse_owner_id(owner_id)
    params: list[object] = [owner]
    predicate = ""
    if book_id is not None:
        predicate = "and chunks.source_book_id = %s"
        params.append(book_id)
    rows = connection.execute(
        f"""
        select chunks.*, books.title as book_title
        from chunks
        join books
          on books.id = chunks.source_book_id
         and books.owner_id = chunks.owner_id
        where chunks.owner_id = %s {predicate}
        order by chunks.source_book_id, chunks.toc_index, chunks.chunk_index
        """,
        params,
    ).fetchall()
    if not rows:
        raise ValueError("no derived chunks matched the requested scope")

    book_ids = {int(row["source_book_id"]) for row in rows}
    existing_rows = connection.execute(
        """
        select * from chunk_embeddings
        where owner_id = %s and source_book_id = any(%s)
        """,
        (owner, list(book_ids)),
    ).fetchall()
    existing = {row["chunk_id"]: row for row in existing_rows}
    connection.commit()

    documents = {
        row["id"]: embedding_document(row["book_title"], row["path_text"], row["text"])
        for row in rows
    }
    current_ids = set(documents)
    stale_ids = sorted(set(existing) - current_ids)
    if reset:
        stale_ids = sorted(set(existing))
        existing = {}
    if stale_ids:
        with connection.transaction():
            connection.execute(
                "delete from chunk_embeddings where owner_id = %s and chunk_id = any(%s)",
                (owner, stale_ids),
            )

    pending = []
    for row in rows:
        digest = embedding_input_hash(documents[row["id"]])
        stored = existing.get(row["id"])
        if stored and (
            stored["embedding_input_hash"] == digest
            and stored["model_name"] == embedder.model_name
            and stored["model_revision"] == embedder.model_revision
            and stored["dimension"] == embedder.dimension
            and stored["document_format_version"] == DOCUMENT_FORMAT_VERSION
        ):
            continue
        pending.append((row, digest))

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = [
            # Guard the write for any embedder implementation: one integer
            # component in a 3,072-float list is a DataError at executemany.
            [float(component) for component in vector]
            for vector in embedder.embed_documents(
                [documents[row["id"]] for row, _ in batch]
            )
        ]
        now = datetime.now(timezone.utc)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    insert into chunk_embeddings (
                        chunk_id, owner_id, source_book_id, embedding, model_name,
                        model_revision, dimension, document_format_version,
                        embedding_input_hash, created_at
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (chunk_id) do update set
                        owner_id = excluded.owner_id,
                        source_book_id = excluded.source_book_id,
                        embedding = excluded.embedding,
                        model_name = excluded.model_name,
                        model_revision = excluded.model_revision,
                        dimension = excluded.dimension,
                        document_format_version = excluded.document_format_version,
                        embedding_input_hash = excluded.embedding_input_hash,
                        created_at = excluded.created_at
                    """,
                    [
                        (
                            row["id"],
                            owner,
                            row["source_book_id"],
                            vector,
                            embedder.model_name,
                            embedder.model_revision,
                            embedder.dimension,
                            DOCUMENT_FORMAT_VERSION,
                            digest,
                            now,
                        )
                        for ((row, digest), vector) in zip(batch, vectors, strict=True)
                    ],
                )

    total_count = int(
        connection.execute(
            "select count(*) as count from chunk_embeddings where owner_id = %s",
            (owner,),
        ).fetchone()["count"]
    )
    return VectorBuildSummary(
        model_name=embedder.model_name,
        model_revision=embedder.model_revision,
        device=embedder.device,
        dimension=embedder.dimension,
        total_count=total_count,
        embedded_count=len(pending),
        unchanged_count=len(rows) - len(pending),
        deleted_count=len(stale_ids),
        book_ids=tuple(sorted(book_ids)),
    )


def vector_search(
    connection: Connection,
    query: str,
    *,
    embedder: Embedder,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
    limit: int = 5,
    unique_nodes: bool = False,
) -> list[SearchResult]:
    """Return exact cosine-nearest chunks from the configured embedding space."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    owner = parse_owner_id(owner_id)
    scope = book_scope(book_id, book_ids)
    query_vector = embedder.embed_query(query)
    candidate_limit = max(limit * 4, 40) if unique_nodes else limit
    params: list[object] = [
        query_vector,
        owner,
        embedder.model_name,
        embedder.model_revision,
        embedder.dimension,
        DOCUMENT_FORMAT_VERSION,
    ]
    book_filter = ""
    if scope is not None:
        book_filter = "and chunks.source_book_id = any(%s)"
        params.append(scope)
    params.extend((query_vector, candidate_limit))
    rows = connection.execute(
        f"""
        select chunks.*,
               1 - (
                   chunk_embeddings.embedding <=> %s::extensions.vector
               ) as score
        from chunk_embeddings
        join chunks
          on chunks.id = chunk_embeddings.chunk_id
         and chunks.owner_id = chunk_embeddings.owner_id
        where chunks.owner_id = %s
          and chunk_embeddings.model_name = %s
          and chunk_embeddings.model_revision = %s
          and chunk_embeddings.dimension = %s
          and chunk_embeddings.document_format_version = %s
          {book_filter}
        order by chunk_embeddings.embedding <=> %s::extensions.vector
        limit %s
        """,
        params,
    ).fetchall()

    results: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for row in rows:
        if unique_nodes and row["source_node_id"] in seen_nodes:
            continue
        seen_nodes.add(row["source_node_id"])
        results.append(
            search_result_from_row(
                row,
                score=float(row["score"]),
                retrieval_method="vector",
            )
        )
        if len(results) == limit:
            break
    return results
