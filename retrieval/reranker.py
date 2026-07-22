"""Hosted cross-encoder reranking over retrieved citation-aware chunks."""

from collections.abc import Sequence
from dataclasses import replace
import os
from typing import Protocol

import httpx

from .sqlite import SearchResult


DEFAULT_RERANKER_MODEL = "cohere/rerank-4-pro"
OPENROUTER_RERANK_URL = "https://openrouter.ai/api/v1/rerank"


class Reranker(Protocol):
    """Minimal scoring contract used by retrieval and tests."""

    model_name: str
    model_revision: str
    device: str
    max_sequence_length: int

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class OpenRouterReranker:
    """Hosted Cohere reranker via OpenRouter's unified /rerank endpoint.

    Documents beyond the model's context window are truncated server-side
    rather than rejected.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        *,
        max_sequence_length: int = 4096,
        timeout: float = 30.0,
    ) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for the OpenRouter reranker"
            )
        self.model_name = model_name
        self.model_revision = "hosted"
        self.device = "api"
        self.max_sequence_length = max_sequence_length
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        response = self._client.post(
            OPENROUTER_RERANK_URL,
            json={
                "model": self.model_name,
                "query": query,
                "documents": list(documents),
            },
        )
        response.raise_for_status()
        results = response.json()["results"]
        scores = [0.0] * len(documents)
        for item in results:
            scores[item["index"]] = float(item["relevance_score"])
        return scores


def build_reranker(spec: str | None = None) -> Reranker:
    """Construct an OpenRouter reranker from a model id spec.

    Falls back to RERANKER_PROVIDER, then DEFAULT_RERANKER_MODEL, when spec
    is not given.
    """
    spec = spec or os.getenv("RERANKER_PROVIDER", DEFAULT_RERANKER_MODEL)
    return OpenRouterReranker(spec)


def reranker_document(result: SearchResult) -> str:
    """Build the hierarchy-aware document paired with a query."""

    return f"Hierarchy: {result.path_text}\n\n{result.text}"


def rerank(
    query: str,
    candidates: list[SearchResult],
    *,
    reranker: Reranker,
    limit: int,
    unique_nodes: bool,
) -> list[SearchResult]:
    """Score, order, and optionally collapse retrieved chunks."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    unique_candidates = list(
        {candidate.chunk_id: candidate for candidate in candidates}.values()
    )
    scores = reranker.score(
        query,
        [reranker_document(candidate) for candidate in unique_candidates],
    )
    if len(scores) != len(unique_candidates):
        raise ValueError(
            "reranker returned a score count that does not match candidates"
        )

    original_rank = {
        candidate.chunk_id: rank
        for rank, candidate in enumerate(unique_candidates, start=1)
    }
    scored = [
        replace(
            candidate,
            score=score,
            retrieval_method="hybrid_rerank",
        )
        for candidate, score in zip(unique_candidates, scores, strict=True)
    ]
    scored.sort(
        key=lambda result: (
            -result.score,
            original_rank[result.chunk_id],
            result.toc_index,
            result.chunk_index,
        )
    )

    results: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for result in scored:
        if unique_nodes and result.source_node_id in seen_nodes:
            continue
        seen_nodes.add(result.source_node_id)
        results.append(result)
        if len(results) == limit:
            break
    return results
