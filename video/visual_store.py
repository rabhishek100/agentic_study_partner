"""Lease-fenced persistence for rebuildable video visual evidence."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from video.states import Stage


CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
PERCEPTUAL_HASH = re.compile(r"^[0-9a-f]{16,64}$")
STORAGE_BACKENDS = frozenset({"filesystem", "supabase", "s3"})
VISUAL_TYPES = frozenset(
    {
        "slide",
        "code",
        "equation",
        "diagram",
        "drawing",
        "chart",
        "ui",
        "terminal",
        "person",
        "other",
    }
)
FRAME_READER_STAGES = frozenset(
    {
        Stage.OCR,
        Stage.VISUAL_ANALYSIS,
        Stage.SPATIAL_REGIONS,
        Stage.INDEXING,
    }
)


class VideoVisualStoreNotFoundError(LookupError):
    """The worker no longer owns the requested version and stage."""


class VideoVisualStoreConflictError(RuntimeError):
    """Derived visual data is incomplete or belongs to another version."""


@dataclass(frozen=True)
class SelectedFrameInput:
    frame_index: int
    timestamp_ms: int
    selection_reasons: tuple[str, ...]
    full_storage_backend: str
    full_storage_key: str
    full_content_hash: str
    preview_storage_backend: str
    preview_storage_key: str
    preview_content_hash: str
    perceptual_hash: str
    width: int
    height: int


@dataclass(frozen=True)
class StoredFrame:
    id: int
    owner_id: UUID
    video_id: UUID
    ingestion_version_id: UUID
    frame_index: int
    timestamp_ms: int
    selection_reasons: tuple[str, ...]
    full_storage_backend: str
    full_storage_key: str
    full_content_hash: str
    preview_storage_backend: str
    preview_storage_key: str
    preview_content_hash: str
    perceptual_hash: str
    width: int
    height: int
    ocr_text: str
    ocr_confidence: float | None
    ocr_engine: str | None
    ocr_version: str | None


@dataclass(frozen=True)
class PersistedFrameBatch:
    frames: tuple[StoredFrame, ...]
    replayed: bool
    replaced: bool


@dataclass(frozen=True)
class OCRResultInput:
    frame_id: int
    text: str
    confidence: float | None
    engine: str
    version: str


@dataclass(frozen=True)
class PersistedOCRBatch:
    frames: tuple[StoredFrame, ...]
    replayed: bool


@dataclass(frozen=True)
class VisualObservationInput:
    """One VLM result for a frame-anchored analysis group.

    ``group_frame_ids`` records every before/after or deduplicated frame shown
    to the model. The row is anchored by ``frame_id`` because the canonical
    schema intentionally permits one current observation per selected frame.
    Raw region proposals, including diagram/drawing bounding boxes, belong in
    ``technical_details`` and are retained without normalization for the
    spatial-cropping stage.
    """

    frame_id: int
    group_frame_ids: tuple[int, ...]
    status: str
    visual_types: tuple[str, ...]
    summary: str | None
    visible_text: str
    technical_details: dict[str, Any]
    importance: float | None
    confidence: float | None
    model_name: str
    model_revision: str
    prompt_version: str
    input_hash: str
    cost_usd: Decimal | str | float = Decimal("0")
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PersistedVisualObservation:
    id: int
    frame_id: int
    cost_usd: Decimal
    replayed: bool
    replaced: bool


def visual_group_is_complete(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    frame_ids: tuple[int, ...],
    model_name: str,
    prompt_version: str,
    input_hash: str,
) -> bool:
    """Report whether an exact paid visual group is already durable.

    The lease-fenced read deliberately happens before the provider call. A
    worker that died after committing one frame pair can therefore resume at
    the next pair without buying the same analysis again.
    """

    group = tuple(frame_ids)
    if not group or len(group) > 2 or len(set(group)) != len(group):
        raise ValueError("visual analysis group must contain one or two frames")
    if CONTENT_HASH.fullmatch(input_hash) is None:
        raise ValueError("input_hash must be a SHA-256 digest")
    scope = _locked_scope(
        connection,
        job_id=job_id,
        worker_id=worker_id,
        attempt_count=attempt_count,
        stage=Stage.VISUAL_ANALYSIS,
    )
    rows = connection.execute(
        """
        select frame_id, technical_details_json
        from video.visual_observations
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
          and frame_id = any(%s) and status = 'success'
          and model_name = %s and prompt_version = %s and input_hash = %s
        """,
        (
            scope["owner_id"],
            scope["video_id"],
            scope["version_id"],
            list(group),
            model_name.strip(),
            prompt_version.strip(),
            input_hash,
        ),
    ).fetchall()
    return len(rows) == len(group) and all(
        tuple(row["technical_details_json"].get("analysis_group_frame_ids", ()))
        == group
        for row in rows
    )


def _clean_required(value: str, *, field: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} is required")
    return clean


def _decimal_cost(value: Decimal | str | float) -> Decimal:
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("cost_usd must be a valid amount") from error
    if not cost.is_finite() or cost < 0:
        raise ValueError("cost_usd cannot be negative or non-finite")
    if cost.as_tuple().exponent < -6:
        raise ValueError("cost_usd supports at most six decimal places")
    return cost


def _locked_scope(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    stage: Stage,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select j.id as job_id, j.owner_id, j.video_id,
               j.target_version_id as version_id, v.duration_ms
        from video.ingestion_jobs as j
        join video.videos as v
          on v.id = j.video_id and v.owner_id = j.owner_id
        join video.ingestion_versions as version
          on version.id = j.target_version_id
         and version.video_id = j.video_id
         and version.owner_id = j.owner_id
        where j.id = %s and j.status = 'running' and j.stage = %s
          and j.lease_owner = %s and j.attempt_count = %s
          and j.lease_expires_at >= now()
        for update of j, version
        """,
        (UUID(str(job_id)), str(Stage(stage)), worker_id, attempt_count),
    ).fetchone()
    if row is None:
        raise VideoVisualStoreNotFoundError(
            "visual evidence is unavailable to this worker attempt"
        )
    return row


def _stored_frame(row: dict[str, Any]) -> StoredFrame:
    return StoredFrame(
        id=int(row["id"]),
        owner_id=row["owner_id"],
        video_id=row["video_id"],
        ingestion_version_id=row["ingestion_version_id"],
        frame_index=int(row["frame_index"]),
        timestamp_ms=int(row["timestamp_ms"]),
        selection_reasons=tuple(row["selection_reasons"]),
        full_storage_backend=row["full_storage_backend"],
        full_storage_key=row["full_storage_key"],
        full_content_hash=row["full_content_hash"],
        preview_storage_backend=row["preview_storage_backend"],
        preview_storage_key=row["preview_storage_key"],
        preview_content_hash=row["preview_content_hash"],
        perceptual_hash=row["perceptual_hash"],
        width=int(row["width"]),
        height=int(row["height"]),
        ocr_text=row["ocr_text"],
        ocr_confidence=(
            float(row["ocr_confidence"])
            if row["ocr_confidence"] is not None
            else None
        ),
        ocr_engine=row["ocr_engine"],
        ocr_version=row["ocr_version"],
    )


def _version_frames(
    connection: Connection, scope: dict[str, Any]
) -> tuple[StoredFrame, ...]:
    rows = connection.execute(
        """
        select * from video.frames
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
        order by frame_index
        """,
        (scope["owner_id"], scope["video_id"], scope["version_id"]),
    ).fetchall()
    return tuple(_stored_frame(row) for row in rows)


def _validate_frame_batch(
    frames: tuple[SelectedFrameInput, ...],
    *,
    owner_id: UUID,
    duration_ms: int | None,
) -> None:
    if not frames:
        raise ValueError("at least one selected frame is required")
    if duration_ms is None or duration_ms <= 0:
        raise VideoVisualStoreConflictError(
            "video duration is required before frame selection"
        )
    previous_timestamp = -1
    for expected_index, frame in enumerate(frames):
        if frame.frame_index != expected_index:
            raise ValueError("selected frames must have contiguous ordered indexes")
        if (
            frame.timestamp_ms <= previous_timestamp
            or frame.timestamp_ms >= duration_ms
        ):
            raise ValueError(
                "selected frame timestamps must increase and fall within the video"
            )
        previous_timestamp = frame.timestamp_ms
        reasons = tuple(reason.strip() for reason in frame.selection_reasons)
        if not reasons or any(not reason for reason in reasons):
            raise ValueError("selection reasons cannot be empty")
        if len(set(reasons)) != len(reasons):
            raise ValueError("selection reasons must be unique")
        if frame.full_storage_backend not in STORAGE_BACKENDS:
            raise ValueError("unsupported full-frame storage backend")
        if frame.preview_storage_backend not in STORAGE_BACKENDS:
            raise ValueError("unsupported preview storage backend")
        for key in (frame.full_storage_key, frame.preview_storage_key):
            if not key.startswith(f"{owner_id}/"):
                raise ValueError("frame storage keys must be owner-scoped")
        for digest in (frame.full_content_hash, frame.preview_content_hash):
            if CONTENT_HASH.fullmatch(digest) is None:
                raise ValueError("frame content hashes must be SHA-256 digests")
        if PERCEPTUAL_HASH.fullmatch(frame.perceptual_hash) is None:
            raise ValueError("perceptual_hash must be lowercase hexadecimal")
        if frame.width <= 0 or frame.height <= 0:
            raise ValueError("frame dimensions must be positive")


def _frame_signature(frame: StoredFrame | SelectedFrameInput) -> tuple[Any, ...]:
    return (
        frame.frame_index,
        frame.timestamp_ms,
        tuple(frame.selection_reasons),
        frame.full_storage_backend,
        frame.full_storage_key,
        frame.full_content_hash,
        frame.preview_storage_backend,
        frame.preview_storage_key,
        frame.preview_content_hash,
        frame.perceptual_hash,
        frame.width,
        frame.height,
    )


def persist_selected_frames(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    frames: Iterable[SelectedFrameInput],
) -> PersistedFrameBatch:
    """Atomically replace a version's complete selected-frame manifest."""

    batch = tuple(frames)
    with connection.transaction():
        scope = _locked_scope(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=Stage.FRAME_SELECTION,
        )
        _validate_frame_batch(
            batch, owner_id=scope["owner_id"], duration_ms=scope["duration_ms"]
        )
        existing = _version_frames(connection, scope)
        wanted = tuple(_frame_signature(frame) for frame in batch)
        if tuple(_frame_signature(frame) for frame in existing) == wanted:
            return PersistedFrameBatch(existing, replayed=True, replaced=False)

        replaced = bool(existing)
        if replaced:
            # Frames and everything derived from them are rebuildable. Cascades
            # prevent a changed manifest from leaving stale observations behind.
            connection.execute(
                """
                delete from video.frames
                where owner_id = %s and video_id = %s
                  and ingestion_version_id = %s
                """,
                (scope["owner_id"], scope["video_id"], scope["version_id"]),
            )
        for frame in batch:
            connection.execute(
                """
                insert into video.frames (
                    owner_id, video_id, ingestion_version_id, frame_index,
                    timestamp_ms, selection_reasons, full_storage_backend,
                    full_storage_key, full_content_hash,
                    preview_storage_backend, preview_storage_key,
                    preview_content_hash, perceptual_hash, width, height
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    scope["owner_id"],
                    scope["video_id"],
                    scope["version_id"],
                    frame.frame_index,
                    frame.timestamp_ms,
                    list(frame.selection_reasons),
                    frame.full_storage_backend,
                    frame.full_storage_key,
                    frame.full_content_hash,
                    frame.preview_storage_backend,
                    frame.preview_storage_key,
                    frame.preview_content_hash,
                    frame.perceptual_hash,
                    frame.width,
                    frame.height,
                ),
            )
        return PersistedFrameBatch(
            _version_frames(connection, scope), replayed=False, replaced=replaced
        )


def load_stage_frames(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    stage: Stage,
) -> tuple[StoredFrame, ...]:
    """Load owner/version-scoped frames for a visual processing stage."""

    stage = Stage(stage)
    if stage not in FRAME_READER_STAGES:
        raise ValueError("this stage cannot load selected frames")
    with connection.transaction():
        scope = _locked_scope(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=stage,
        )
        frames = _version_frames(connection, scope)
        if not frames:
            raise VideoVisualStoreConflictError("selected frames are unavailable")
        return frames


def persist_ocr_results(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    results: Iterable[OCRResultInput],
) -> PersistedOCRBatch:
    """Persist one complete OCR result for every selected version frame."""

    batch = tuple(results)
    if len({result.frame_id for result in batch}) != len(batch):
        raise ValueError("OCR frame ids must be unique")
    for result in batch:
        _clean_required(result.engine, field="OCR engine")
        _clean_required(result.version, field="OCR version")
        if result.confidence is not None and not 0 <= result.confidence <= 1:
            raise ValueError("OCR confidence must be between zero and one")

    with connection.transaction():
        scope = _locked_scope(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=Stage.OCR,
        )
        frames = _version_frames(connection, scope)
        if {frame.id for frame in frames} != {result.frame_id for result in batch}:
            raise VideoVisualStoreConflictError(
                "OCR results must cover exactly the selected version frames"
            )
        by_id = {result.frame_id: result for result in batch}
        replayed = all(
            (
                frame.ocr_text,
                frame.ocr_confidence,
                frame.ocr_engine,
                frame.ocr_version,
            )
            == (
                by_id[frame.id].text,
                by_id[frame.id].confidence,
                by_id[frame.id].engine.strip(),
                by_id[frame.id].version.strip(),
            )
            for frame in frames
        )
        if not replayed:
            for result in batch:
                connection.execute(
                    """
                    update video.frames
                    set ocr_text = %s, ocr_confidence = %s,
                        ocr_engine = %s, ocr_version = %s
                    where id = %s and owner_id = %s and video_id = %s
                      and ingestion_version_id = %s
                    """,
                    (
                        result.text,
                        result.confidence,
                        result.engine.strip(),
                        result.version.strip(),
                        result.frame_id,
                        scope["owner_id"],
                        scope["video_id"],
                        scope["version_id"],
                    ),
                )
        return PersistedOCRBatch(_version_frames(connection, scope), replayed)


def _validate_observation(
    value: VisualObservationInput,
) -> tuple[Decimal, dict[str, Any]]:
    if value.frame_id <= 0:
        raise ValueError("frame_id must be positive")
    group = tuple(value.group_frame_ids) or (value.frame_id,)
    if len(set(group)) != len(group) or any(frame_id <= 0 for frame_id in group):
        raise ValueError("analysis group frame ids must be unique and positive")
    if value.frame_id not in group:
        raise ValueError("the observation anchor must belong to its analysis group")
    if value.status not in {"success", "failed"}:
        raise ValueError("observation status must be success or failed")
    types = tuple(value.visual_types)
    if len(set(types)) != len(types) or not set(types) <= VISUAL_TYPES:
        raise ValueError("observation contains an unsupported visual type")
    if value.status == "success":
        if value.summary is None or not value.summary.strip() or not types:
            raise ValueError(
                "successful observations require a summary and visual type"
            )
        if value.error_code is not None or value.error_message is not None:
            raise ValueError("successful observations cannot contain an error")
    else:
        if value.summary is not None or types:
            raise ValueError(
                "failed observations cannot contain a summary or visual type"
            )
        if value.error_code is None or not value.error_code.strip():
            raise ValueError("failed observations require an error code")
    for score, field in (
        (value.importance, "importance"),
        (value.confidence, "confidence"),
    ):
        if score is not None and not 0 <= score <= 1:
            raise ValueError(f"{field} must be between zero and one")
    if not isinstance(value.technical_details, dict):
        raise ValueError("technical_details must be an object")
    details = dict(value.technical_details)
    reserved = "analysis_group_frame_ids"
    if reserved in details and details[reserved] != list(group):
        raise ValueError("technical_details contains a conflicting analysis group")
    details[reserved] = list(group)
    _clean_required(value.model_name, field="model_name")
    _clean_required(value.model_revision, field="model_revision")
    _clean_required(value.prompt_version, field="prompt_version")
    if CONTENT_HASH.fullmatch(value.input_hash) is None:
        raise ValueError("input_hash must be a SHA-256 digest")
    return _decimal_cost(value.cost_usd), details


def persist_visual_observation(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    observation: VisualObservationInput,
) -> PersistedVisualObservation:
    """Cache one frame-group VLM result, replacing only derived drift.

    An exact retry returns the existing row and therefore cannot duplicate its
    provider cost. A changed result replaces the observation transactionally;
    dependent region crops cascade so they can never describe stale geometry.
    """

    cost, details = _validate_observation(observation)
    with connection.transaction():
        scope = _locked_scope(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=Stage.VISUAL_ANALYSIS,
        )
        group = tuple(observation.group_frame_ids) or (observation.frame_id,)
        owned_ids = {
            int(row["id"])
            for row in connection.execute(
                """
                select id from video.frames
                where owner_id = %s and video_id = %s
                  and ingestion_version_id = %s and id = any(%s)
                """,
                (
                    scope["owner_id"],
                    scope["video_id"],
                    scope["version_id"],
                    list(group),
                ),
            ).fetchall()
        }
        if owned_ids != set(group):
            raise VideoVisualStoreConflictError(
                "analysis group contains a frame outside the target version"
            )

        expected = {
            "status": observation.status,
            "visual_types": list(observation.visual_types),
            "summary": observation.summary,
            "visible_text": observation.visible_text,
            "technical_details_json": details,
            "importance": observation.importance,
            "confidence": observation.confidence,
            "model_name": observation.model_name.strip(),
            "model_revision": observation.model_revision.strip(),
            "prompt_version": observation.prompt_version.strip(),
            "input_hash": observation.input_hash,
            "cost_usd": cost,
            "error_code": observation.error_code,
            "error_message": observation.error_message,
        }
        existing = connection.execute(
            """
            select * from video.visual_observations
            where owner_id = %s and video_id = %s
              and ingestion_version_id = %s and frame_id = %s
            for update
            """,
            (
                scope["owner_id"],
                scope["video_id"],
                scope["version_id"],
                observation.frame_id,
            ),
        ).fetchone()
        exact_replay = existing is not None and all(
            existing[key] == value for key, value in expected.items()
        )
        if exact_replay:
            return PersistedVisualObservation(
                int(existing["id"]),
                int(existing["frame_id"]),
                existing["cost_usd"],
                replayed=True,
                replaced=False,
            )

        replaced = existing is not None
        if replaced:
            connection.execute(
                """
                delete from video.visual_observations
                where id = %s and owner_id = %s
                """,
                (existing["id"], scope["owner_id"]),
            )
        row = connection.execute(
            """
            insert into video.visual_observations (
                owner_id, video_id, ingestion_version_id, frame_id, status,
                visual_types, summary, visible_text, technical_details_json,
                importance, confidence, model_name, model_revision,
                prompt_version, input_hash, cost_usd, error_code, error_message
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) returning id, frame_id, cost_usd
            """,
            (
                scope["owner_id"],
                scope["video_id"],
                scope["version_id"],
                observation.frame_id,
                expected["status"],
                expected["visual_types"],
                expected["summary"],
                expected["visible_text"],
                Jsonb(expected["technical_details_json"]),
                expected["importance"],
                expected["confidence"],
                expected["model_name"],
                expected["model_revision"],
                expected["prompt_version"],
                expected["input_hash"],
                expected["cost_usd"],
                expected["error_code"],
                expected["error_message"],
            ),
        ).fetchone()
        return PersistedVisualObservation(
            int(row["id"]),
            int(row["frame_id"]),
            row["cost_usd"],
            replayed=False,
            replaced=replaced,
        )
