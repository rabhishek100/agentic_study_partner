"""Rebuildable semantic vectors over one video ingestion version.

Lexical evidence answers questions that reuse the lecturer's vocabulary.
Diagram-region vectors and evidence-text vectors answer the rest, so both are
built here from canonical rows and keyed by model, dimension, and document
format version. Nothing in this module is canonical: every row can be dropped
and rebuilt from evidence units and stored crops.
"""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import os
from typing import Any, Protocol
from uuid import UUID

import httpx
from psycopg import Connection

from video.errors import VideoBudgetExceeded
from video.states import Stage


OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
DEFAULT_TEXT_EMBEDDING_MODEL = "openai/text-embedding-3-large"
DEFAULT_IMAGE_EMBEDDING_MODEL = "google/gemini-embedding-2"
# Measured against the frozen video gold set: 1024 costs 1.6% anchor recall
# (0.912 -> 0.897 over 34 evidence turns) and roughly two and a half times the
# storage, which is the trade worth making on the one corpus that grows. The
# book side stays at 3072, where the same truncation cost 4.5%.
#
# text-embedding-3-large is a Matryoshka model, so asking for 1024 returns the
# same leading components a 3072 vector would have had. Stored vectors were
# truncated in place rather than re-embedded.
TEXT_EMBEDDING_DIMENSION = 1024
IMAGE_EMBEDDING_DIMENSION = 768
TEXT_DOCUMENT_FORMAT_VERSION = "video-evidence-text-v1"
IMAGE_DOCUMENT_FORMAT_VERSION = "video-evidence-region-v1"
TEXT_BATCH_SIZE = 32
MAXIMUM_DOCUMENT_CHARACTERS = 8_000
MAXIMUM_REGION_SUMMARY_CHARACTERS = 2_000
ESTIMATED_TEXT_BATCH_COST = Decimal("0.002")
ESTIMATED_REGION_COST = Decimal("0.002")

MODALITY_PREFIX = {
    "transcript": "Spoken transcript",
    "visual_frame": "Video frame",
    "visual_event": "Visual change",
    "resource_page": "Linked document page",
}


class VideoEmbeddingConflictError(RuntimeError):
    """The embedding stage is no longer owned by this worker attempt."""


class VideoEmbeddingProviderError(RuntimeError):
    """The embedding provider returned an unusable response."""


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    cost_usd: Decimal


@dataclass(frozen=True)
class RegionImage:
    image: bytes
    mime_type: str
    summary: str


class TextEmbedder(Protocol):
    model_name: str
    model_revision: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult: ...

    def embed_query(self, text: str) -> EmbeddingResult: ...


class ImageEmbedder(Protocol):
    model_name: str
    model_revision: str
    dimension: int

    def embed_regions(self, regions: Sequence[RegionImage]) -> EmbeddingResult: ...

    def embed_query(self, text: str) -> EmbeddingResult: ...


RegionImageLoader = Callable[[UUID, str], bytes]


@dataclass(frozen=True)
class EmbeddingBuild:
    text_count: int
    image_count: int
    reused_count: int
    deleted_count: int
    cost_usd: Decimal
    text_model: str
    text_dimension: int
    image_model: str | None
    image_dimension: int | None


class _OpenRouterEmbeddings:
    """Shared request handling for the hosted embedding endpoint."""

    def __init__(self, *, model_name: str, dimension: int, timeout: float) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for video embeddings")
        self.model_name = model_name
        self.model_revision = "hosted"
        self.dimension = dimension
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "_OpenRouterEmbeddings":
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    def _post(self, request: dict[str, Any], *, expected: int) -> EmbeddingResult:
        response = self._client.post(OPENROUTER_EMBEDDINGS_URL, json=request)
        response.raise_for_status()
        body = response.json()
        data = body.get("data") or []
        if len(data) != expected:
            raise VideoEmbeddingProviderError(
                f"embedding provider returned {len(data)} vectors for {expected} inputs"
            )
        indexes = [item.get("index") for item in data]
        if any(not isinstance(index, int) for index in indexes) or sorted(
            indexes
        ) != list(range(expected)):
            raise VideoEmbeddingProviderError(
                f"embedding provider returned invalid input indexes: {indexes!r}"
            )
        vectors = []
        for item in sorted(data, key=lambda value: value["index"]):
            # An exact-zero component decodes as int, and psycopg refuses to
            # adapt a mixed int/float vector, so normalize every component.
            vector = tuple(float(value) for value in item.get("embedding") or ())
            if len(vector) != self.dimension:
                raise VideoEmbeddingProviderError(
                    f"embedding provider returned {len(vector)} dimensions; "
                    f"expected {self.dimension}"
                )
            vectors.append(vector)
        usage = body.get("usage") or {}
        return EmbeddingResult(
            vectors=tuple(vectors),
            model=str(body.get("model") or self.model_name),
            cost_usd=Decimal(str(usage.get("cost") or 0)).quantize(Decimal("0.000001")),
        )


class OpenRouterTextEmbedder(_OpenRouterEmbeddings):
    """Batched evidence-text embeddings with provider-reported cost."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        dimension: int = TEXT_EMBEDDING_DIMENSION,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            model_name=model_name or DEFAULT_TEXT_EMBEDDING_MODEL,
            dimension=dimension,
            timeout=timeout,
        )

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), model=self.model_name, cost_usd=Decimal(0))
        return self._post(
            {
                "model": self.model_name,
                "input": list(texts),
                "encoding_format": "float",
                "dimensions": self.dimension,
            },
            expected=len(texts),
        )

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_documents([text])


class OpenRouterRegionEmbedder(_OpenRouterEmbeddings):
    """Diagram-region image embeddings in a text-searchable shared space."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        dimension: int = IMAGE_EMBEDDING_DIMENSION,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            model_name=model_name or DEFAULT_IMAGE_EMBEDDING_MODEL,
            dimension=dimension,
            timeout=timeout,
        )

    def embed_regions(self, regions: Sequence[RegionImage]) -> EmbeddingResult:
        if not regions:
            return EmbeddingResult(vectors=(), model=self.model_name, cost_usd=Decimal(0))
        inputs = []
        for region in regions:
            parts: list[dict[str, Any]] = []
            summary = region.summary.strip()[:MAXIMUM_REGION_SUMMARY_CHARACTERS]
            if summary:
                parts.append({"type": "text", "text": summary})
            encoded = b64encode(region.image).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{region.mime_type};base64,{encoded}"},
                }
            )
            inputs.append({"content": parts})
        return self._post(
            {
                "model": self.model_name,
                "input": inputs,
                "encoding_format": "float",
                "dimensions": self.dimension,
            },
            expected=len(regions),
        )

    def embed_query(self, text: str) -> EmbeddingResult:
        return self._post(
            {
                "model": self.model_name,
                "input": text,
                "input_type": "search_query",
                "encoding_format": "float",
                "dimensions": self.dimension,
            },
            expected=1,
        )


def build_text_embedder() -> OpenRouterTextEmbedder:
    return OpenRouterTextEmbedder(
        os.getenv("OPENROUTER_VIDEO_TEXT_EMBEDDING_MODEL", "").strip() or None
    )


def build_image_embedder() -> OpenRouterRegionEmbedder:
    return OpenRouterRegionEmbedder(
        os.getenv("OPENROUTER_VIDEO_IMAGE_EMBEDDING_MODEL", "").strip() or None
    )


def evidence_document(modality: str, text: str) -> str:
    """Name the modality so a vector keeps the evidence kind it came from."""

    prefix = MODALITY_PREFIX.get(modality, "Video evidence")
    return f"{prefix}: {text}"[:MAXIMUM_DOCUMENT_CHARACTERS]


def embedding_input_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def region_input_hash(*, crop_content_hash: str, summary: str) -> str:
    return embedding_input_hash(
        "|".join((IMAGE_DOCUMENT_FORMAT_VERSION, crop_content_hash, summary.strip()))
    )


def rebuild_evidence_embeddings(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    text_embedder: TextEmbedder,
    image_embedder: ImageEmbedder | None = None,
    load_region_image: RegionImageLoader | None = None,
    remaining_budget_usd: Decimal | None = None,
) -> EmbeddingBuild:
    """Synchronize vectors without holding a transaction over provider calls."""

    if image_embedder is not None and load_region_image is None:
        raise ValueError("region embeddings require an image loader")
    scope = _locked_scope(
        connection,
        job_id=job_id,
        worker_id=worker_id,
        attempt_count=attempt_count,
    )
    evidence = connection.execute(
        """
        select id, modality, frame_id, retrieval_text
        from video.evidence_units
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        order by id
        """,
        (scope["owner_id"], scope["video_id"], scope["version_id"]),
    ).fetchall()
    frame_evidence = {
        row["frame_id"]: row["id"] for row in evidence if row["frame_id"] is not None
    }
    regions = (
        connection.execute(
            """
            select id, frame_id, crop_storage_key, crop_content_hash, summary
            from video.visual_regions
            where owner_id = %s and video_id = %s and ingestion_version_id = %s
            order by frame_id, region_index
            """,
            (scope["owner_id"], scope["video_id"], scope["version_id"]),
        ).fetchall()
        if image_embedder is not None
        else []
    )
    stored = connection.execute(
        """
        select id, evidence_id, embedding_kind, visual_region_id, model_name,
               model_revision, dimension, document_format_version,
               embedding_input_hash
        from video.evidence_embeddings
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        """,
        (scope["owner_id"], scope["video_id"], scope["version_id"]),
    ).fetchall()
    # Provider calls follow, so release the stage lock and the read snapshot.
    connection.commit()

    existing = {
        (row["embedding_kind"], row["evidence_id"], row["visual_region_id"]): row
        for row in stored
    }
    pending_text: list[tuple[str, str, str]] = []
    reused = 0
    wanted: set[tuple[str, str, int | None]] = set()
    for row in evidence:
        document = evidence_document(row["modality"], row["retrieval_text"])
        digest = embedding_input_hash(document)
        key = ("text", row["id"], None)
        wanted.add(key)
        if _matches(
            existing.get(key),
            model_name=text_embedder.model_name,
            model_revision=text_embedder.model_revision,
            dimension=text_embedder.dimension,
            document_format_version=TEXT_DOCUMENT_FORMAT_VERSION,
            digest=digest,
        ):
            reused += 1
            continue
        pending_text.append((row["id"], document, digest))

    pending_regions: list[dict[str, Any]] = []
    for row in regions:
        evidence_id = frame_evidence.get(row["frame_id"])
        if evidence_id is None:
            # A frame without retrieval text has nothing to attach a vector to.
            continue
        digest = region_input_hash(
            crop_content_hash=row["crop_content_hash"], summary=row["summary"]
        )
        key = ("image", evidence_id, row["id"])
        wanted.add(key)
        if _matches(
            existing.get(key),
            model_name=image_embedder.model_name,
            model_revision=image_embedder.model_revision,
            dimension=image_embedder.dimension,
            document_format_version=IMAGE_DOCUMENT_FORMAT_VERSION,
            digest=digest,
        ):
            reused += 1
            continue
        pending_regions.append(
            {
                "evidence_id": evidence_id,
                "region_id": row["id"],
                "frame_id": row["frame_id"],
                "storage_key": row["crop_storage_key"],
                "summary": row["summary"],
                "digest": digest,
            }
        )

    replaced = {("text", evidence_id, None) for evidence_id, _, _ in pending_text}
    replaced.update(
        ("image", item["evidence_id"], item["region_id"]) for item in pending_regions
    )
    stale = [
        row["id"]
        for key, row in existing.items()
        if key not in wanted or key in replaced
    ]
    if stale:
        with connection.transaction():
            connection.execute(
                "delete from video.evidence_embeddings where owner_id = %s and id = any(%s)",
                (scope["owner_id"], stale),
            )

    budget = _Budget(remaining_budget_usd)
    text_model = text_embedder.model_name
    for start in range(0, len(pending_text), TEXT_BATCH_SIZE):
        batch = pending_text[start : start + TEXT_BATCH_SIZE]
        budget.reserve(ESTIMATED_TEXT_BATCH_COST)
        result = text_embedder.embed_documents([document for _, document, _ in batch])
        if len(result.vectors) != len(batch):
            raise VideoEmbeddingProviderError(
                "embedding provider returned an unusable batch size"
            )
        budget.spend(result.cost_usd)
        text_model = result.model
        with connection.transaction():
            _locked_scope(
                connection,
                job_id=job_id,
                worker_id=worker_id,
                attempt_count=attempt_count,
            )
            for (evidence_id, _, digest), vector in zip(batch, result.vectors, strict=True):
                _insert_embedding(
                    connection,
                    scope=scope,
                    evidence_id=evidence_id,
                    embedding_kind="text",
                    visual_region_id=None,
                    evidence_frame_id=None,
                    model_name=text_embedder.model_name,
                    model_revision=text_embedder.model_revision,
                    dimension=text_embedder.dimension,
                    document_format_version=TEXT_DOCUMENT_FORMAT_VERSION,
                    digest=digest,
                    vector=list(vector),
                )

    image_model = image_embedder.model_name if image_embedder is not None else None
    for item in pending_regions:
        if image_embedder is None or load_region_image is None:
            break
        budget.reserve(ESTIMATED_REGION_COST)
        image = load_region_image(scope["owner_id"], item["storage_key"])
        result = image_embedder.embed_regions(
            [RegionImage(image=image, mime_type="image/jpeg", summary=item["summary"])]
        )
        if len(result.vectors) != 1:
            raise VideoEmbeddingProviderError(
                "embedding provider returned an unusable region vector"
            )
        budget.spend(result.cost_usd)
        image_model = result.model
        with connection.transaction():
            _locked_scope(
                connection,
                job_id=job_id,
                worker_id=worker_id,
                attempt_count=attempt_count,
            )
            _insert_embedding(
                connection,
                scope=scope,
                evidence_id=item["evidence_id"],
                embedding_kind="image",
                visual_region_id=item["region_id"],
                evidence_frame_id=item["frame_id"],
                model_name=image_embedder.model_name,
                model_revision=image_embedder.model_revision,
                dimension=image_embedder.dimension,
                document_format_version=IMAGE_DOCUMENT_FORMAT_VERSION,
                digest=item["digest"],
                vector=list(result.vectors[0]),
            )

    return EmbeddingBuild(
        text_count=len(pending_text),
        image_count=len(pending_regions),
        reused_count=reused,
        deleted_count=len(stale),
        cost_usd=budget.spent,
        text_model=text_model,
        text_dimension=text_embedder.dimension,
        image_model=image_model,
        image_dimension=(
            image_embedder.dimension if image_embedder is not None else None
        ),
    )


class _Budget:
    """Stop before a paid request that the job cannot afford."""

    def __init__(self, remaining: Decimal | None) -> None:
        self._remaining = remaining
        self.spent = Decimal(0)

    def reserve(self, estimate: Decimal) -> None:
        if self._remaining is not None and self.spent + estimate > self._remaining:
            raise VideoBudgetExceeded()

    def spend(self, cost: Decimal) -> None:
        self.spent += cost


def _matches(
    row: dict[str, Any] | None,
    *,
    model_name: str,
    model_revision: str,
    dimension: int,
    document_format_version: str,
    digest: str,
) -> bool:
    return bool(
        row
        and row["model_name"] == model_name
        and row["model_revision"] == model_revision
        and int(row["dimension"]) == dimension
        and row["document_format_version"] == document_format_version
        and row["embedding_input_hash"] == digest
    )


def _insert_embedding(
    connection: Connection,
    *,
    scope: dict[str, Any],
    evidence_id: str,
    embedding_kind: str,
    visual_region_id: int | None,
    evidence_frame_id: int | None,
    model_name: str,
    model_revision: str,
    dimension: int,
    document_format_version: str,
    digest: str,
    vector: list[float],
) -> None:
    connection.execute(
        """
        insert into video.evidence_embeddings (
            owner_id, video_id, ingestion_version_id, evidence_id,
            embedding_kind, visual_region_id, evidence_frame_id, model_name,
            model_revision, dimension, document_format_version,
            embedding_input_hash, embedding
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            scope["owner_id"],
            scope["video_id"],
            scope["version_id"],
            evidence_id,
            embedding_kind,
            visual_region_id,
            evidence_frame_id,
            model_name,
            model_revision,
            dimension,
            document_format_version,
            digest,
            vector,
        ),
    )


def _locked_scope(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select j.owner_id, j.video_id, j.target_version_id as version_id
        from video.ingestion_jobs as j
        where j.id = %s and j.status = 'running' and j.stage = %s
          and j.lease_owner = %s and j.attempt_count = %s
          and j.lease_expires_at >= now()
        for update
        """,
        (UUID(str(job_id)), str(Stage.EMBEDDINGS), worker_id, attempt_count),
    ).fetchone()
    if row is None:
        raise VideoEmbeddingConflictError(
            "video embedding stage is unavailable to this worker attempt"
        )
    return row
