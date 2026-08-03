"""Atomic, owner-scoped persistence for canonical video transcripts."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.transcripts import TranscriptCue, transcript_coverage


CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
STORAGE_BACKENDS = frozenset({"filesystem", "supabase", "s3"})
SOURCE_KINDS = frozenset({"youtube_caption", "openrouter_transcription"})


class TranscriptSourceNotFoundError(LookupError):
    """The requested video source does not belong to the supplied scope."""


class TranscriptSourceConflictError(RuntimeError):
    """The canonical source is not currently eligible for transcripts."""


@dataclass(frozen=True)
class PersistedTranscript:
    id: UUID
    video_id: UUID
    video_source_id: UUID
    source_kind: str
    coverage_ratio: float
    segment_count: int
    created: bool


def _clean_required(value: str, *, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    return cleaned


def _validate_cues(cues: list[TranscriptCue]) -> None:
    if not cues:
        raise ValueError("at least one transcript cue is required")
    indexes: set[int] = set()
    for cue in cues:
        if cue.cue_index < 0 or cue.cue_index in indexes:
            raise ValueError("transcript cue indexes must be unique and non-negative")
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms:
            raise ValueError("transcript cues must have valid time ranges")
        if not cue.text.strip():
            raise ValueError("transcript cue text cannot be blank")
        indexes.add(cue.cue_index)


def _result(
    connection: Connection,
    row: dict[str, Any],
    *,
    created: bool,
) -> PersistedTranscript:
    count = connection.execute(
        """
        select count(*) as segment_count
        from video.transcript_segments
        where owner_id = %s and video_id = %s and transcript_source_id = %s
        """,
        (row["owner_id"], row["video_id"], row["id"]),
    ).fetchone()["segment_count"]
    return PersistedTranscript(
        id=row["id"],
        video_id=row["video_id"],
        video_source_id=row["video_source_id"],
        source_kind=row["source_kind"],
        coverage_ratio=float(row["coverage_ratio"]),
        segment_count=int(count),
        created=created,
    )


def persist_transcript(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    video_source_id: str | UUID,
    source_kind: str,
    language: str,
    provider: str,
    storage_backend: str,
    storage_key: str,
    content_hash: str,
    cues: list[TranscriptCue],
    model_name: str | None = None,
    model_revision: str | None = None,
    provenance: dict[str, Any] | None = None,
    cost_usd: str | Decimal = "0",
) -> PersistedTranscript:
    """Persist raw-transcript provenance and cue rows in one transaction.

    The raw VTT or transcription object must already exist in media storage.
    Replaying the same ``video_source_id`` and ``content_hash`` returns the
    canonical transcript without inserting a second source or any cue rows.
    """

    owner = parse_owner_id(owner_id)
    video, source = UUID(str(video_id)), UUID(str(video_source_id))
    kind = _clean_required(source_kind, field="source_kind")
    if kind not in SOURCE_KINDS:
        raise ValueError("unsupported transcript source kind")
    backend = _clean_required(storage_backend, field="storage_backend")
    if backend not in STORAGE_BACKENDS:
        raise ValueError("unsupported transcript storage backend")
    key = _clean_required(storage_key, field="storage_key")
    if not key.startswith(f"{owner}/"):
        raise ValueError("transcript storage key must be owner-scoped")
    digest = _clean_required(content_hash, field="content_hash")
    if CONTENT_HASH.fullmatch(digest) is None:
        raise ValueError("content_hash must be a lowercase SHA-256 digest")
    transcript_language = _clean_required(language, field="language")
    transcript_provider = _clean_required(provider, field="provider")
    clean_model = model_name.strip() if model_name is not None else None
    clean_revision = model_revision.strip() if model_revision is not None else None
    if kind == "openrouter_transcription" and not clean_model:
        raise ValueError("model_name is required for OpenRouter transcription")
    _validate_cues(cues)
    if provenance is not None and not isinstance(provenance, dict):
        raise ValueError("provenance must be an object")
    try:
        cost = Decimal(cost_usd)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("cost_usd must be a valid amount") from error
    if not cost.is_finite() or cost < 0:
        raise ValueError("cost_usd cannot be negative or non-finite")

    with connection.transaction():
        canonical = connection.execute(
            """
            select v.duration_ms, s.status
            from video.video_sources as s
            join video.videos as v
              on v.id = s.video_id and v.owner_id = s.owner_id
            where s.id = %s and s.video_id = %s and s.owner_id = %s
            for share of s, v
            """,
            (source, video, owner),
        ).fetchone()
        if canonical is None:
            raise TranscriptSourceNotFoundError("video source not found")
        if canonical["status"] not in {"pending", "ready"}:
            raise TranscriptSourceConflictError(
                "video source is not eligible for transcript persistence"
            )
        duration_ms = canonical["duration_ms"]
        if duration_ms is None:
            raise TranscriptSourceConflictError(
                "video duration is required before transcript persistence"
            )
        coverage = transcript_coverage(cues, duration_ms=int(duration_ms))

        row = connection.execute(
            """
            insert into video.transcript_sources (
                owner_id, video_id, video_source_id, source_kind, language,
                provider, model_name, model_revision, storage_backend,
                storage_key, content_hash, timing_kind, coverage_ratio,
                detected_speech, provenance_json, cost_usd
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'cue',
                %s, true, %s, %s
            )
            on conflict (video_source_id, content_hash) do nothing
            returning id, owner_id, video_id, video_source_id, source_kind,
                      coverage_ratio
            """,
            (
                owner,
                video,
                source,
                kind,
                transcript_language,
                transcript_provider,
                clean_model,
                clean_revision,
                backend,
                key,
                digest,
                coverage,
                Jsonb(provenance or {}),
                cost,
            ),
        ).fetchone()
        if row is None:
            existing = connection.execute(
                """
                select id, owner_id, video_id, video_source_id, source_kind,
                       coverage_ratio
                from video.transcript_sources
                where owner_id = %s and video_id = %s
                  and video_source_id = %s and content_hash = %s
                """,
                (owner, video, source, digest),
            ).fetchone()
            if existing is None:
                raise TranscriptSourceConflictError(
                    "transcript source changed during persistence"
                )
            return _result(connection, existing, created=False)

        with connection.cursor() as cursor:
            cursor.executemany(
                """
                insert into video.transcript_segments (
                    owner_id, video_id, transcript_source_id, cue_index,
                    start_ms, end_ms, text, raw_json
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        owner,
                        video,
                        row["id"],
                        cue.cue_index,
                        cue.start_ms,
                        cue.end_ms,
                        cue.text.strip(),
                        Jsonb({"raw_text": cue.raw_text}),
                    )
                    for cue in cues
                ],
            )
        return _result(connection, row, created=True)
