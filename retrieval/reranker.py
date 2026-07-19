"""Local cross-encoder reranking over retrieved citation-aware chunks."""

from collections.abc import Sequence
from dataclasses import replace
import os
from typing import Protocol

from .sqlite import SearchResult


DEFAULT_RERANKER_MODEL = "Alibaba-NLP/gte-reranker-modernbert-base"
DEFAULT_RERANKER_REVISION = "f7481e6055501a30fb19d090657df9ec1f79ab2c"
DEFAULT_RERANKER_BATCH_SIZE = 8


class Reranker(Protocol):
    """Minimal scoring contract used by retrieval and tests."""

    model_name: str
    model_revision: str
    device: str
    max_sequence_length: int

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class LocalCrossEncoder:
    """Pinned long-context cross-encoder with explicit length validation."""

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        revision: str | None = None,
    ) -> None:
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self.model_revision = revision or (
            DEFAULT_RERANKER_REVISION
            if model_name == DEFAULT_RERANKER_MODEL
            else "main"
        )
        self.device = os.getenv(
            "RERANKER_DEVICE",
            os.getenv("EMBEDDING_DEVICE", "cpu"),
        )
        self.batch_size = int(
            os.getenv("RERANKER_BATCH_SIZE", DEFAULT_RERANKER_BATCH_SIZE)
        )
        if self.batch_size <= 0:
            raise ValueError("RERANKER_BATCH_SIZE must be positive")
        self._model = CrossEncoder(
            model_name,
            revision=(
                None if self.model_revision == "main" else self.model_revision
            ),
            device=self.device,
            model_kwargs={"torch_dtype": "auto"},
        )
        self.max_sequence_length = int(self._model.max_seq_length)

    def _validate_lengths(self, query: str, documents: Sequence[str]) -> None:
        encoded = self._model.tokenizer(
            [query] * len(documents),
            text_pair=list(documents),
            add_special_tokens=True,
            padding=False,
            truncation=False,
        )
        longest = max(len(ids) for ids in encoded["input_ids"])
        if longest > self.max_sequence_length:
            raise ValueError(
                f"reranker input has {longest} tokens but "
                f"{self.model_name} supports {self.max_sequence_length}; "
                "rechunk or choose a long-context reranker"
            )

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        self._validate_lengths(query, documents)

        order = sorted(
            range(len(documents)),
            key=lambda index: len(documents[index]),
        )
        pairs = [(query, documents[index]) for index in order]
        predictions = self._model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        scores = [0.0] * len(documents)
        for sorted_index, original_index in enumerate(order):
            scores[original_index] = float(predictions[sorted_index])
        return scores


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
