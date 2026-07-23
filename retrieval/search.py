"""Unified lexical, semantic, fused, and reranked retrieval."""

from dataclasses import replace
from typing import Literal
from uuid import UUID

from psycopg import Connection

from .reranker import Reranker, build_reranker, rerank
from .postgres import SearchResult, search as bm25_search
from .vector import (
    Embedder,
    build_embedder,
    vector_search,
)


RetrievalMode = Literal["bm25", "vector", "hybrid", "hybrid_rerank"]
RRF_RANK_CONSTANT = 60
RERANK_CANDIDATE_LIMIT = 20


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


def hybrid_candidates(
    connection: Connection,
    query: str,
    *,
    book_id: int | None,
    owner_id: str | UUID | None,
    candidate_limit: int,
    embedder: Embedder,
) -> list[SearchResult]:
    """Build an RRF-ordered shortlist from lexical and semantic candidates."""

    lexical = bm25_search(
        connection,
        query,
        owner_id=owner_id,
        book_id=book_id,
        limit=candidate_limit,
        unique_nodes=False,
    )
    semantic = vector_search(
        connection,
        query,
        embedder=embedder,
        owner_id=owner_id,
        book_id=book_id,
        limit=candidate_limit,
        unique_nodes=False,
    )
    return reciprocal_rank_fusion(
        [lexical, semantic],
        limit=candidate_limit,
        unique_nodes=False,
    )


def _take_ranked(
    candidates: list[SearchResult],
    *,
    limit: int,
    unique_nodes: bool,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for result in candidates:
        if unique_nodes and result.source_node_id in seen_nodes:
            continue
        seen_nodes.add(result.source_node_id)
        results.append(result)
        if len(results) == limit:
            break
    return results


def retrieve(
    connection: Connection,
    query: str,
    *,
    mode: RetrievalMode = "hybrid",
    owner_id: str | UUID | None = None,
    book_id: int | None = None,
    limit: int = 5,
    unique_nodes: bool = False,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
) -> list[SearchResult]:
    """Run one explicit retrieval strategy over the same Postgres chunks."""

    if mode == "bm25":
        return bm25_search(
            connection,
            query,
            owner_id=owner_id,
            book_id=book_id,
            limit=limit,
            unique_nodes=unique_nodes,
        )
    if mode not in ("vector", "hybrid", "hybrid_rerank"):
        raise ValueError(f"unsupported retrieval mode: {mode}")

    embedder = embedder or build_embedder()
    if mode == "vector":
        return vector_search(
            connection,
            query,
            embedder=embedder,
            owner_id=owner_id,
            book_id=book_id,
            limit=limit,
            unique_nodes=unique_nodes,
        )

    candidate_limit = (
        RERANK_CANDIDATE_LIMIT if mode == "hybrid_rerank" else max(20, limit * 4)
    )
    fused = hybrid_candidates(
        connection,
        query,
        book_id=book_id,
        owner_id=owner_id,
        candidate_limit=candidate_limit,
        embedder=embedder,
    )
    if mode == "hybrid":
        return _take_ranked(
            fused,
            limit=limit,
            unique_nodes=unique_nodes,
        )
    return rerank(
        query,
        fused,
        reranker=reranker or build_reranker(),
        limit=limit,
        unique_nodes=unique_nodes,
    )
