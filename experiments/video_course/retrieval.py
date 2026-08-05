"""Small inspectable multimodal retrieval index for a single lecture."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
import math
from pathlib import Path
import re
from typing import Iterable

import numpy as np

from .models import EvidenceUnit, RetrievedEvidence
from .openrouter import OpenRouterClient


TOKEN = re.compile(r"[a-z0-9_+.-]+", re.IGNORECASE)
TEXT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
IMAGE_EMBEDDING_MODEL = "google/gemini-embedding-2"


class MultimodalIndex:
    def __init__(self, evidence: list[EvidenceUnit]) -> None:
        self.evidence = evidence
        self.by_id = {item.id: item for item in evidence}
        self._tokens = [tokens(item.text) for item in evidence]
        self._document_frequency: Counter[str] = Counter()
        for words in self._tokens:
            self._document_frequency.update(set(words))
        self._average_length = (
            sum(map(len, self._tokens)) / len(self._tokens) if self._tokens else 1.0
        )

    def search(
        self,
        question: str,
        *,
        query_text_embedding: list[float] | None = None,
        query_image_embedding: list[float] | None = None,
        limit: int = 12,
    ) -> list[RetrievedEvidence]:
        rankings: dict[str, list[int]] = {}
        bm25 = sorted(
            range(len(self.evidence)),
            key=lambda index: self._bm25(question, index),
            reverse=True,
        )
        rankings["bm25"] = bm25[: max(limit * 3, 30)]
        if query_text_embedding:
            candidates = [
                (index, cosine(query_text_embedding, list(item.embedding)))
                for index, item in enumerate(self.evidence)
                if item.embedding
            ]
            candidates.sort(key=lambda pair: pair[1], reverse=True)
            rankings["text_vector"] = [index for index, _ in candidates[: max(limit * 3, 30)]]
        if query_image_embedding:
            candidates = [
                (index, cosine(query_image_embedding, list(item.embedding)))
                for index, item in enumerate(self.evidence)
                if item.embedding and item.modality == "visual_image"
            ]
            candidates.sort(key=lambda pair: pair[1], reverse=True)
            rankings["image_vector"] = [index for index, _ in candidates[: max(limit * 2, 20)]]

        fused: defaultdict[int, float] = defaultdict(float)
        methods: defaultdict[int, list[str]] = defaultdict(list)
        for method, ranking in rankings.items():
            for rank, index in enumerate(ranking, start=1):
                fused[index] += 1.0 / (60 + rank)
                methods[index].append(method)
        ordered = sorted(fused, key=fused.get, reverse=True)[:limit]
        return [
            RetrievedEvidence(
                evidence_id=self.evidence[index].id,
                rank=rank,
                score=round(fused[index], 8),
                retrieval_methods=tuple(methods[index]),
            )
            for rank, index in enumerate(ordered, start=1)
        ]

    def _bm25(self, query: str, index: int, *, k1: float = 1.5, b: float = 0.75) -> float:
        query_words = tokens(query)
        document = self._tokens[index]
        frequencies = Counter(document)
        score = 0.0
        count = max(1, len(self.evidence))
        for word in query_words:
            frequency = frequencies[word]
            if not frequency:
                continue
            document_frequency = self._document_frequency[word]
            inverse = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
            denominator = frequency + k1 * (
                1 - b + b * len(document) / max(1.0, self._average_length)
            )
            score += inverse * frequency * (k1 + 1) / denominator
        return score


def add_embeddings(
    client: OpenRouterClient,
    evidence: list[EvidenceUnit],
) -> tuple[list[EvidenceUnit], dict[str, float]]:
    result: list[EvidenceUnit | None] = [None] * len(evidence)
    costs = {"text": 0.0, "image": 0.0}
    text_indexes = [
        index
        for index, item in enumerate(evidence)
        if not (item.modality == "visual_image" and item.image_path)
    ]
    text_embeddings, provenance = client.embed_texts(
        model=TEXT_EMBEDDING_MODEL,
        operation="evidence-text-embeddings",
        texts=[evidence[index].text[:8_000] for index in text_indexes],
        estimated_cost_usd=0.02,
    )
    costs["text"] = float(provenance["cost_usd"])
    for index, embedding in zip(text_indexes, text_embeddings, strict=True):
        result[index] = replace(evidence[index], embedding=tuple(embedding))

    for index, item in enumerate(evidence):
        if item.modality == "visual_image" and item.image_path:
            embedding, provenance = client.embed(
                model=IMAGE_EMBEDDING_MODEL,
                operation="visual-image-embedding",
                text=item.text[:2_000],
                image=Path(item.image_path),
                dimensions=768,
                estimated_cost_usd=0.002,
            )
            costs["image"] += float(provenance["cost_usd"])
            result[index] = replace(item, embedding=tuple(embedding))
    return [item for item in result if item is not None], costs


def embed_query(
    client: OpenRouterClient,
    question: str,
) -> tuple[list[float], list[float], float]:
    text, text_provenance = client.embed(
        model=TEXT_EMBEDDING_MODEL,
        operation="question-text-embedding",
        text=question,
        estimated_cost_usd=0.0002,
    )
    image, image_provenance = client.embed(
        model=IMAGE_EMBEDDING_MODEL,
        operation="question-image-embedding",
        text=question,
        input_type="search_query",
        dimensions=768,
        estimated_cost_usd=0.0002,
    )
    return (
        text,
        image,
        float(text_provenance["cost_usd"]) + float(image_provenance["cost_usd"]),
    )


def tokens(value: str) -> list[str]:
    return TOKEN.findall(value.lower())


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else 0.0
