"""Rebuildable local Chroma indexing and semantic retrieval."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Protocol

import chromadb
import httpx

from .sqlite import SearchResult, chunks_by_id, search_result_from_row


DEFAULT_CHROMA_PATH = Path("data/chroma")
DEFAULT_COLLECTION = "book_text_chunks"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-large"

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
DOCUMENT_FORMAT_VERSION = "hierarchy-v1"


class Embedder(Protocol):
    """Minimal embedding contract used by ingestion and retrieval."""

    model_name: str
    model_revision: str
    device: str
    dimension: int
    max_sequence_length: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenRouterEmbedder:
    """Hosted embedding model via OpenRouter's OpenAI-compatible endpoint.

    Trades local GPU/CPU inference for a network call. Dimension is probed
    once at construction since it varies by model and isn't otherwise known.
    """

    def __init__(
        self,
        model_name: str,
        *,
        max_sequence_length: int = 8192,
        timeout: float = 60.0,
    ) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for the OpenRouter embedder"
            )
        self.model_name = model_name
        self.model_revision = "hosted"
        self.device = "api"
        self.max_sequence_length = max_sequence_length
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self._query_cache: dict[str, list[float]] = {}
        self.dimension = len(self._embed([" "])[0])

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._client.post(
            OPENROUTER_EMBEDDINGS_URL,
            json={
                "model": self.model_name,
                "input": list(texts),
                "encoding_format": "float",
            },
        )
        response.raise_for_status()
        ordered = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        if text in self._query_cache:
            return self._query_cache[text]
        value = self._embed([text])[0]
        self._query_cache[text] = value
        return value


def build_embedder(spec: str | None = None) -> Embedder:
    """Construct an OpenRouter embedder from a model id spec.

    Falls back to EMBEDDING_PROVIDER, then DEFAULT_EMBEDDING_MODEL, when spec
    is not given.
    """
    spec = spec or os.getenv("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_MODEL)
    return OpenRouterEmbedder(spec)


@dataclass(frozen=True)
class VectorBuildSummary:
    collection: str
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
    """Construct the hierarchy-aware text represented by one vector."""

    return f"Book: {book_title}\nHierarchy: {path_text}\n\n{text}"


def persistent_client(path: str | Path = DEFAULT_CHROMA_PATH):
    """Open Chroma's embedded, on-disk client."""

    Path(path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=path)


def cloud_client():
    """Open a Chroma Cloud client from CHROMA_API_KEY/TENANT/DATABASE.

    Credentials are passed explicitly rather than left for chromadb's
    CloudClient() to auto-resolve from the environment: chromadb==1.5.9 has
    a bug where that auto-resolution path never assigns the looked-up key
    back to the variable used to build the auth header, silently sending
    the literal string "None" instead and failing with a 401.
    """
    api_key = os.getenv("CHROMA_API_KEY")
    tenant = os.getenv("CHROMA_TENANT")
    database = os.getenv("CHROMA_DATABASE")
    missing = [
        name
        for name, value in (
            ("CHROMA_API_KEY", api_key),
            ("CHROMA_TENANT", tenant),
            ("CHROMA_DATABASE", database),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"missing required environment variables for Chroma Cloud: {missing}"
        )
    return chromadb.CloudClient(tenant=tenant, database=database, api_key=api_key)


def build_chroma_client(path: str | Path = DEFAULT_CHROMA_PATH, *, provider: str | None = None):
    """Construct a Chroma client from a provider spec.

    "local" (the default, also used when CHROMA_PROVIDER is unset) opens the
    on-disk PersistentClient. "cloud" connects to Chroma Cloud.
    """
    provider = provider or os.getenv("CHROMA_PROVIDER", "local")
    if provider == "local":
        return persistent_client(path)
    if provider == "cloud":
        return cloud_client()
    raise ValueError(f"unsupported CHROMA_PROVIDER: {provider!r}")


def _collection_names(client) -> set[str]:
    return {collection.name for collection in client.list_collections()}


def _open_collection(
    client,
    *,
    embedder: Embedder,
    collection_name: str,
    create: bool,
):
    if collection_name not in _collection_names(client):
        if not create:
            raise FileNotFoundError(
                f"Chroma collection {collection_name!r} does not exist; "
                "run `uv run python -m scripts.build_vector_index` first"
            )
        return client.create_collection(
            name=collection_name,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={
                "embedding_model": embedder.model_name,
                "embedding_revision": embedder.model_revision,
                "embedding_dimension": embedder.dimension,
                "document_format_version": DOCUMENT_FORMAT_VERSION,
            },
        )

    collection = client.get_collection(
        name=collection_name,
        embedding_function=None,
    )
    metadata = collection.metadata or {}
    expected = {
        "embedding_model": embedder.model_name,
        "embedding_revision": embedder.model_revision,
        "embedding_dimension": embedder.dimension,
        "document_format_version": DOCUMENT_FORMAT_VERSION,
    }
    mismatches = {
        key: (metadata.get(key), value)
        for key, value in expected.items()
        if metadata.get(key) != value
    }
    if mismatches:
        raise ValueError(
            f"Chroma collection configuration does not match: {mismatches}. "
            "Rebuild with --reset."
        )
    return collection


def _book_titles(
    source: sqlite3.Connection,
    book_ids: set[int],
) -> dict[int, str]:
    if not book_ids:
        return {}
    placeholders = ",".join("?" for _ in book_ids)
    rows = source.execute(
        f"SELECT id, title FROM books WHERE id IN ({placeholders})",
        sorted(book_ids),
    ).fetchall()
    titles = {row["id"]: row["title"] for row in rows}
    missing = sorted(book_ids - titles.keys())
    if missing:
        raise ValueError(f"canonical database is missing books {missing}")
    return titles


def rebuild_vector_index(
    retrieval: sqlite3.Connection,
    source: sqlite3.Connection,
    *,
    client,
    embedder: Embedder,
    collection_name: str = DEFAULT_COLLECTION,
    book_id: int | None = None,
    reset: bool = False,
    batch_size: int = 32,
) -> VectorBuildSummary:
    """Synchronize Chroma with current derived SQLite chunks."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if reset and collection_name in _collection_names(client):
        client.delete_collection(collection_name)

    parameters: tuple[object, ...] = ()
    predicate = ""
    if book_id is not None:
        predicate = "WHERE source_book_id = ?"
        parameters = (book_id,)
    rows = retrieval.execute(
        f"""
        SELECT *
        FROM chunks
        {predicate}
        ORDER BY source_book_id, toc_index, chunk_index
        """,
        parameters,
    ).fetchall()
    if not rows:
        raise ValueError("no derived chunks matched the requested scope")

    collection = _open_collection(
        client,
        embedder=embedder,
        collection_name=collection_name,
        create=True,
    )
    book_ids = {int(row["source_book_id"]) for row in rows}
    titles = _book_titles(source, book_ids)

    if book_id is None:
        existing_ids = set(collection.get(include=[])["ids"])
    else:
        existing_ids = set(
            collection.get(where={"book_id": book_id}, include=[])["ids"]
        )
    current_ids = {row["id"] for row in rows}
    stale_ids = sorted(existing_ids - current_ids)
    if stale_ids:
        collection.delete(ids=stale_ids)

    new_rows = [row for row in rows if row["id"] not in existing_ids]
    for start in range(0, len(new_rows), batch_size):
        batch = new_rows[start : start + batch_size]
        documents = [
            embedding_document(
                titles[row["source_book_id"]],
                row["path_text"],
                row["text"],
            )
            for row in batch
        ]
        embeddings = embedder.embed_documents(documents)
        collection.upsert(
            ids=[row["id"] for row in batch],
            documents=documents,
            embeddings=embeddings,
            metadatas=[
                {
                    "book_id": row["source_book_id"],
                    "node_id": row["source_node_id"],
                    "chunk_index": row["chunk_index"],
                    "start_page": row["start_page"],
                    "end_page": row["end_page"],
                    "content_hash": row["content_hash"],
                }
                for row in batch
            ],
        )

    return VectorBuildSummary(
        collection=collection_name,
        model_name=embedder.model_name,
        model_revision=embedder.model_revision,
        device=embedder.device,
        dimension=embedder.dimension,
        total_count=collection.count(),
        embedded_count=len(new_rows),
        unchanged_count=len(rows) - len(new_rows),
        deleted_count=len(stale_ids),
        book_ids=tuple(sorted(book_ids)),
    )


def write_manifest(
    path: str | Path,
    summary: VectorBuildSummary,
    *,
    max_sequence_length: int,
) -> Path:
    """Write inspectable vector-index provenance beside Chroma data."""

    destination = Path(path) / "index_manifest.json"
    destination.write_text(
        json.dumps(
            {
                "collection": summary.collection,
                "embedding_model": summary.model_name,
                "embedding_revision": summary.model_revision,
                "embedding_dimension": summary.dimension,
                "embedding_device": summary.device,
                "max_sequence_length": max_sequence_length,
                "document_format_version": DOCUMENT_FORMAT_VERSION,
                "distance_metric": "cosine",
                "index": "hnsw",
                "book_ids": list(summary.book_ids),
                "vector_count": summary.total_count,
                "built_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination


def vector_search(
    retrieval: sqlite3.Connection,
    query: str,
    *,
    client,
    embedder: Embedder,
    collection_name: str = DEFAULT_COLLECTION,
    book_id: int | None = None,
    limit: int = 5,
    unique_nodes: bool = False,
) -> list[SearchResult]:
    """Return semantic results hydrated from derived SQLite by chunk ID."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    collection = _open_collection(
        client,
        embedder=embedder,
        collection_name=collection_name,
        create=False,
    )
    # Oversample past `limit` so book_id filtering and unique_nodes dedup
    # still have enough candidates to work with after trimming below.
    candidate_limit = min(collection.count(), max(limit * 4, 40))
    if candidate_limit == 0:
        return []
    response = collection.query(
        query_embeddings=[embedder.embed_query(query)],
        n_results=candidate_limit,
        where={"book_id": book_id} if book_id is not None else None,
        include=["distances"],
    )
    ids = response["ids"][0]
    distances = response["distances"][0]
    rows = chunks_by_id(retrieval, ids)

    results: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for chunk_id, distance in zip(ids, distances, strict=True):
        row = rows.get(chunk_id)
        if row is None:
            raise ValueError(
                f"vector {chunk_id} has no matching derived SQLite chunk; "
                "rebuild the vector index"
            )
        if unique_nodes and row["source_node_id"] in seen_nodes:
            continue
        seen_nodes.add(row["source_node_id"])
        results.append(
            search_result_from_row(
                row,
                score=1.0 - float(distance),
                retrieval_method="vector",
            )
        )
        if len(results) == limit:
            break
    return results
