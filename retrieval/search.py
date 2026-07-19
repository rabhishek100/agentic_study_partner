"""Unified BM25, vector, and reciprocal-rank-fusion retrieval."""

from dataclasses import replace
from pathlib import Path
import sqlite3
from typing import Literal

from .sqlite import SearchResult, search as bm25_search
from .vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_COLLECTION,
    Embedder,
    LocalEmbedder,
    persistent_client,
    vector_search,
)


RetrievalMode = Literal["bm25", "vector", "hybrid"]
RRF_RANK_CONSTANT = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    *,
    limit: int,
    unique_nodes: bool,
) -> list[SearchResult]:
    """Fuse ranked chunk lists without calibrating incomparable raw scores."""

    scores: dict[str, float] = {}
    best: dict[str, SearchResult] = {}
    first_rank: dict[str, int] = {}
    for ranked in ranked_lists:
        for rank, result in enumerate(ranked, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + (
                1.0 / (RRF_RANK_CONSTANT + rank)
            )
            best.setdefault(result.chunk_id, result)
            first_rank[result.chunk_id] = min(
                first_rank.get(result.chunk_id, rank),
                rank,
            )

    ordered = sorted(
        best.values(),
        key=lambda result: (
            -scores[result.chunk_id],
            first_rank[result.chunk_id],
            result.toc_index,
            result.chunk_index,
        ),
    )
    fused: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for result in ordered:
        if unique_nodes and result.source_node_id in seen_nodes:
            continue
        seen_nodes.add(result.source_node_id)
        fused.append(
            replace(
                result,
                score=scores[result.chunk_id],
                retrieval_method="hybrid",
            )
        )
        if len(fused) == limit:
            break
    return fused


def retrieve(
    connection: sqlite3.Connection,
    query: str,
    *,
    mode: RetrievalMode = "hybrid",
    book_id: int | None = None,
    limit: int = 5,
    unique_nodes: bool = False,
    chroma_path: str | Path = DEFAULT_CHROMA_PATH,
    collection_name: str = DEFAULT_COLLECTION,
    client=None,
    embedder: Embedder | None = None,
) -> list[SearchResult]:
    """Run one explicit retrieval strategy over the same SQLite chunks."""

    if mode == "bm25":
        return bm25_search(
            connection,
            query,
            book_id=book_id,
            limit=limit,
            unique_nodes=unique_nodes,
        )
    if mode not in ("vector", "hybrid"):
        raise ValueError(f"unsupported retrieval mode: {mode}")

    client = client or persistent_client(chroma_path)
    embedder = embedder or LocalEmbedder()
    if mode == "vector":
        return vector_search(
            connection,
            query,
            client=client,
            embedder=embedder,
            collection_name=collection_name,
            book_id=book_id,
            limit=limit,
            unique_nodes=unique_nodes,
        )

    candidate_limit = max(20, limit * 4)
    lexical = bm25_search(
        connection,
        query,
        book_id=book_id,
        limit=candidate_limit,
        unique_nodes=False,
    )
    semantic = vector_search(
        connection,
        query,
        client=client,
        embedder=embedder,
        collection_name=collection_name,
        book_id=book_id,
        limit=candidate_limit,
        unique_nodes=False,
    )
    return reciprocal_rank_fusion(
        [lexical, semantic],
        limit=limit,
        unique_nodes=unique_nodes,
    )
