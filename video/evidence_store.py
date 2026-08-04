"""Deterministic lexical evidence construction and video quality gates."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from video.states import Stage


EVIDENCE_FORMAT_VERSION = "video-evidence-v1"


class VideoEvidenceConflictError(RuntimeError):
    pass


class VideoQualityGateError(RuntimeError):
    def __init__(self, metrics: dict[str, Any]) -> None:
        self.metrics = metrics
        super().__init__("video lacks the minimum transcript or visual evidence")


@dataclass(frozen=True)
class EvidenceBuild:
    transcript_count: int
    visual_frame_count: int
    visual_event_count: int
    total_count: int


@dataclass(frozen=True)
class QualityGateResult:
    readiness: str
    metrics: dict[str, Any]


def _owned_stage(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    stage: Stage,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select j.id, j.owner_id, j.video_id, j.target_version_id
        from video.ingestion_jobs j
        where j.id = %s and j.status = 'running' and j.stage = %s
          and j.lease_owner = %s and j.attempt_count = %s
          and j.lease_expires_at >= now()
        for update
        """,
        (UUID(str(job_id)), str(stage), worker_id, attempt_count),
    ).fetchone()
    if row is None:
        raise VideoEvidenceConflictError(
            "video evidence stage is unavailable to this worker attempt"
        )
    return row


def rebuild_evidence(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    transcript_source_id: str | UUID,
) -> EvidenceBuild:
    """Atomically rebuild FTS evidence for the target ingestion version."""

    transcript_id = UUID(str(transcript_source_id))
    with connection.transaction():
        job = _owned_stage(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=Stage.INDEXING,
        )
        transcript = connection.execute(
            """
            select id from video.transcript_sources
            where id = %s and owner_id = %s and video_id = %s
            """,
            (transcript_id, job["owner_id"], job["video_id"]),
        ).fetchone()
        if transcript is None:
            raise VideoEvidenceConflictError(
                "selected transcript does not belong to this video"
            )
        connection.execute(
            """
            delete from video.evidence_units
            where owner_id = %s and video_id = %s and ingestion_version_id = %s
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        )

        transcript_rows = connection.execute(
            """
            select id, start_ms, end_ms, text
            from video.transcript_segments
            where owner_id = %s and video_id = %s and transcript_source_id = %s
            order by cue_index
            """,
            (job["owner_id"], job["video_id"], transcript_id),
        ).fetchall()
        for row in transcript_rows:
            _insert_evidence(
                connection,
                job=job,
                modality="transcript",
                source_id=row["id"],
                retrieval_text=row["text"],
                transcript_segment_id=row["id"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
            )

        frame_rows = connection.execute(
            """
            select f.id, f.timestamp_ms, f.ocr_text,
                   observation.status as observation_status,
                   observation.visual_types, observation.summary,
                   observation.visible_text,
                   observation.technical_details_json
            from video.frames f
            left join video.visual_observations observation
              on observation.frame_id = f.id
             and observation.ingestion_version_id = f.ingestion_version_id
             and observation.video_id = f.video_id
             and observation.owner_id = f.owner_id
            where f.owner_id = %s and f.video_id = %s
              and f.ingestion_version_id = %s
            order by f.frame_index
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        ).fetchall()
        visual_count = 0
        for row in frame_rows:
            text = _frame_text(row)
            if not text:
                continue
            _insert_evidence(
                connection,
                job=job,
                modality="visual_frame",
                source_id=row["id"],
                retrieval_text=text,
                frame_id=row["id"],
                start_ms=row["timestamp_ms"],
                end_ms=row["timestamp_ms"],
            )
            visual_count += 1

        event_rows = connection.execute(
            """
            select id, start_ms, end_ms, event_type, summary, details_json
            from video.visual_events
            where owner_id = %s and video_id = %s and ingestion_version_id = %s
            order by start_ms, id
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        ).fetchall()
        for row in event_rows:
            details = json.dumps(
                row["details_json"] or {}, sort_keys=True, separators=(",", ":")
            )
            text = f"{row['event_type']}: {row['summary']} {details}".strip()
            _insert_evidence(
                connection,
                job=job,
                modality="visual_event",
                source_id=row["id"],
                retrieval_text=text,
                visual_event_id=row["id"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
            )

        total = len(transcript_rows) + visual_count + len(event_rows)
        return EvidenceBuild(
            transcript_count=len(transcript_rows),
            visual_frame_count=visual_count,
            visual_event_count=len(event_rows),
            total_count=total,
        )


def evaluate_quality_gates(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    transcript_source_id: str | UUID,
) -> QualityGateResult:
    """Measure a small set of explainable publish gates from canonical rows."""

    transcript_id = UUID(str(transcript_source_id))
    with connection.transaction():
        job = _owned_stage(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=Stage.QUALITY_GATES,
        )
        source = connection.execute(
            """
            select v.duration_ms, s.status as source_status,
                   transcript.source_kind, transcript.coverage_ratio,
                   transcript.provenance_json
            from video.videos v
            join video.ingestion_versions version
              on version.id = %s and version.video_id = v.id
             and version.owner_id = v.owner_id
            join video.video_sources s
              on s.id = version.video_source_id
             and s.video_id = version.video_id and s.owner_id = version.owner_id
            left join video.transcript_sources transcript
              on transcript.id = %s and transcript.video_id = v.id
             and transcript.owner_id = v.owner_id
            where v.id = %s and v.owner_id = %s
            """,
            (
                job["target_version_id"],
                transcript_id,
                job["video_id"],
                job["owner_id"],
            ),
        ).fetchone()
        if source is None or source["duration_ms"] is None:
            raise VideoQualityGateError({"canonical_source": False})
        duration_ms = int(source["duration_ms"])
        frame_rows = connection.execute(
            """
            select f.id, f.timestamp_ms,
                   (observation.status = 'success') as success
            from video.frames f
            left join video.visual_observations observation
              on observation.frame_id = f.id
            where f.owner_id = %s and f.video_id = %s
              and f.ingestion_version_id = %s
            order by f.timestamp_ms
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        ).fetchall()
        successful_timestamps = [
            int(row["timestamp_ms"]) for row in frame_rows if row["success"]
        ]
        gaps = [
            right - left
            for left, right in zip(
                successful_timestamps, successful_timestamps[1:], strict=False
            )
        ]
        chapter_count = connection.execute(
            """
            select count(*) as count from video.chapters
            where owner_id = %s and video_id = %s
            """,
            (job["owner_id"], job["video_id"]),
        ).fetchone()["count"]
        covered_chapters = connection.execute(
            """
            select count(*) as count
            from video.chapters chapter
            where chapter.owner_id = %s and chapter.video_id = %s
              and exists (
                  select 1 from video.frames frame
                  join video.visual_observations observation
                    on observation.frame_id = frame.id
                   and observation.status = 'success'
                  where frame.owner_id = chapter.owner_id
                    and frame.video_id = chapter.video_id
                    and frame.ingestion_version_id = %s
                    and frame.timestamp_ms >= chapter.start_ms
                    and frame.timestamp_ms < chapter.end_ms
              )
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        ).fetchone()["count"]
        counts = connection.execute(
            """
            select
                count(*) filter (where modality = 'transcript') as transcript,
                count(*) filter (where modality in (
                    'visual_frame', 'visual_event'
                )) as visual,
                count(*) as total
            from video.evidence_units
            where owner_id = %s and video_id = %s and ingestion_version_id = %s
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        ).fetchone()
        segment_count = connection.execute(
            """
            select count(*) as count from video.transcript_segments
            where owner_id = %s and video_id = %s and transcript_source_id = %s
            """,
            (job["owner_id"], job["video_id"], transcript_id),
        ).fetchone()["count"]
        embeddings = connection.execute(
            """
            select
                count(distinct evidence_id) filter (
                    where embedding_kind = 'text'
                ) as text_count,
                count(*) filter (where embedding_kind = 'image') as image_count
            from video.evidence_embeddings
            where owner_id = %s and video_id = %s and ingestion_version_id = %s
            """,
            (job["owner_id"], job["video_id"], job["target_version_id"]),
        ).fetchone()

        frame_count = len(frame_rows)
        success_count = len(successful_timestamps)
        first_ms = int(frame_rows[0]["timestamp_ms"]) if frame_rows else None
        last_ms = int(frame_rows[-1]["timestamp_ms"]) if frame_rows else None
        coverage = float(source["coverage_ratio"] or 0.0)
        provenance = source["provenance_json"] or {}
        processed_duration_ms = int(provenance.get("processed_duration_ms") or 0)
        completeness = (
            min(1.0, processed_duration_ms / duration_ms)
            if source["source_kind"] == "openrouter_transcription"
            else coverage
        )
        gates = {
            "canonical_source": source["source_status"] == "ready",
            # ASR timestamps describe speech, so silence lowers cue occupancy
            # without indicating missing transcription. For hosted ASR, the
            # processed audio duration is therefore the completeness signal.
            "transcript_complete": completeness >= 0.95,
            "timeline_frames": bool(
                frame_rows
                and first_ms is not None
                and first_ms <= 30_000
                and last_ms is not None
                and duration_ms - last_ms <= 30_000
            ),
            "visual_analysis_success": (
                success_count / frame_count >= 0.90 if frame_count else False
            ),
            "every_chapter_visual": (
                chapter_count == 0 or covered_chapters == chapter_count
            ),
            "no_visual_gap_over_five_minutes": (
                bool(successful_timestamps) and max(gaps, default=0) <= 300_000
            ),
            "transcript_evidence_complete": (
                segment_count > 0 and counts["transcript"] == segment_count
            ),
            "visual_evidence_present": counts["visual"] > 0,
            # Lexical retrieval alone answers only questions that reuse the
            # lecturer's wording, so an incomplete semantic index is a
            # publishable but degraded state rather than a silent omission.
            "semantic_index_complete": (
                counts["total"] > 0 and embeddings["text_count"] == counts["total"]
            ),
        }
        metrics: dict[str, Any] = {
            "format_version": EVIDENCE_FORMAT_VERSION,
            "gates": gates,
            "duration_ms": duration_ms,
            "transcript_source_kind": source["source_kind"],
            "transcript_coverage_ratio": round(coverage, 6),
            "transcript_completeness_ratio": round(completeness, 6),
            "transcript_segment_count": segment_count,
            "frame_count": frame_count,
            "successful_visual_observation_count": success_count,
            "visual_success_ratio": round(success_count / max(1, frame_count), 6),
            "chapter_count": int(chapter_count),
            "chapters_with_visual_evidence": int(covered_chapters),
            "maximum_visual_gap_ms": max(gaps, default=duration_ms),
            "transcript_evidence_count": int(counts["transcript"]),
            "visual_evidence_count": int(counts["visual"]),
            "evidence_count": int(counts["total"]),
            "text_embedding_count": int(embeddings["text_count"]),
            "image_embedding_count": int(embeddings["image_count"]),
        }
        hard = (
            gates["canonical_source"]
            and completeness >= 0.90
            and gates["transcript_evidence_complete"]
            and frame_count > 0
            and success_count > 0
            and gates["visual_evidence_present"]
        )
        if not hard:
            raise VideoQualityGateError(metrics)
        readiness = "ready" if all(gates.values()) else "degraded"
        return QualityGateResult(readiness=readiness, metrics=metrics)


def persist_quality_gates(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    result: QualityGateResult,
) -> None:
    with connection.transaction():
        job = _owned_stage(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            stage=Stage.QUALITY_GATES,
        )
        connection.execute(
            """
            update video.ingestion_versions
            set quality_gates_json = %s
            where id = %s and owner_id = %s and status = 'building'
            """,
            (
                Jsonb({**result.metrics, "readiness": result.readiness}),
                job["target_version_id"],
                job["owner_id"],
            ),
        )


def _insert_evidence(
    connection: Connection,
    *,
    job: dict[str, Any],
    modality: str,
    source_id: Any,
    retrieval_text: str,
    transcript_segment_id: int | None = None,
    frame_id: int | None = None,
    visual_event_id: int | None = None,
    start_ms: int,
    end_ms: int,
) -> None:
    clean = " ".join(retrieval_text.split())
    content_hash = sha256(clean.encode()).hexdigest()
    evidence_id = sha256(
        "|".join(
            (
                EVIDENCE_FORMAT_VERSION,
                str(job["target_version_id"]),
                modality,
                str(source_id),
                content_hash,
            )
        ).encode()
    ).hexdigest()
    connection.execute(
        """
        insert into video.evidence_units (
            id, owner_id, video_id, ingestion_version_id, modality,
            transcript_segment_id, frame_id, visual_event_id, retrieval_text,
            start_ms, end_ms, content_hash
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            evidence_id,
            job["owner_id"],
            job["video_id"],
            job["target_version_id"],
            modality,
            transcript_segment_id,
            frame_id,
            visual_event_id,
            clean,
            start_ms,
            end_ms,
            content_hash,
        ),
    )


def _frame_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row["observation_status"] == "success":
        if row["visual_types"]:
            parts.append("Visual types: " + ", ".join(row["visual_types"]))
        parts.extend(
            value.strip()
            for value in (row["summary"], row["visible_text"])
            if value and value.strip()
        )
        details = row["technical_details_json"] or {}
        if details:
            parts.append(json.dumps(details, sort_keys=True, separators=(",", ":")))
    if row["ocr_text"] and row["ocr_text"].strip():
        parts.append("OCR: " + row["ocr_text"].strip())
    return " ".join(parts)
