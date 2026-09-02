"""Queue or inspect a resumable full-quality course upgrade."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from uuid import UUID, uuid5

from storage.database import connection as database_connection
from video.course_repository import upgrade_course_quality
from video.repository import FULL_QUALITY_PROFILE, reingest_video


def _owner(connection, course_id: UUID) -> UUID:
    row = connection.execute(
        "select owner_id from video.courses where id = %s", (course_id,)
    ).fetchone()
    if row is None:
        raise SystemExit("course not found")
    return row["owner_id"]


def start(course_id: UUID, idempotency_key: UUID, cap: str) -> dict:
    with database_connection() as connection:
        result = upgrade_course_quality(
            connection,
            owner_id=_owner(connection, course_id),
            course_id=course_id,
            idempotency_key=idempotency_key,
            ingestion_cost_cap_usd=cap,
        )
    return {
        "course_id": str(result.course_id),
        "ingestion_cost_cap_usd": str(result.ingestion_cost_cap_usd),
        "lectures": [
            {
                "video_id": str(item.video_id),
                "job_id": str(item.ingestion_job_id),
                "reused": item.reused,
            }
            for item in result.lectures
        ],
    }


def status(course_id: UUID) -> dict:
    with database_connection(readonly=True) as connection:
        owner = _owner(connection, course_id)
        course = connection.execute(
            """
            select title, ingestion_cost_cap_usd, actual_ingestion_cost_usd,
                   metadata_json
            from video.courses where id = %s and owner_id = %s
            """,
            (course_id, owner),
        ).fetchone()
        rows = connection.execute(
            """
            select lecture.lecture_index, video.id as video_id, video.title,
                   video.readiness_status, video.current_ingestion_version_id,
                   job.id as job_id, job.status as job_status, job.stage,
                   job.attempt_count, job.max_attempts, job.actual_cost_usd,
                   job.last_error_code, job.last_error_message,
                   job.target_version_id,
                   (select count(*) from video.frames as frame
                    where frame.ingestion_version_id = job.target_version_id) as frames,
                   (select count(*) from video.visual_observations as observation
                    where observation.ingestion_version_id = job.target_version_id
                      and observation.status = 'success') as observations,
                   (select count(*) from video.evidence_embeddings as embedding
                    where embedding.ingestion_version_id = job.target_version_id) as embeddings
            from video.course_lectures as lecture
            join video.videos as video
              on video.id = lecture.video_id and video.owner_id = lecture.owner_id
            left join video.ingestion_jobs as job
              on job.id = lecture.ingestion_job_id and job.owner_id = lecture.owner_id
            where lecture.course_id = %s and lecture.owner_id = %s
            order by lecture.lecture_index
            """,
            (course_id, owner),
        ).fetchall()
    lectures = [
        {
            key: (str(value) if key.endswith("_id") or key.endswith("_usd") else value)
            for key, value in dict(row).items()
        }
        for row in rows
    ]
    return {
        "course_id": str(course_id),
        "title": course["title"],
        "ingestion_cost_cap_usd": str(course["ingestion_cost_cap_usd"]),
        "actual_ingestion_cost_usd": str(course["actual_ingestion_cost_usd"]),
        "metadata": course["metadata_json"],
        "counts": {
            state: sum(item["job_status"] == state for item in lectures)
            for state in ("queued", "running", "retry_scheduled", "ready", "degraded", "failed")
        },
        "lectures": lectures,
    }


def repair(
    course_id: UUID,
    idempotency_key: UUID,
    lecture_indexes: list[int],
    per_video_cap: str,
) -> dict:
    """Queue checkpoint-preserving replacement versions for named lectures."""

    indexes = sorted(set(lecture_indexes))
    if not indexes or indexes[0] < 0:
        raise SystemExit("at least one non-negative lecture index is required")
    cap = Decimal(per_video_cap).quantize(Decimal("0.000001"))
    if cap <= 0:
        raise SystemExit("per-video cap must be positive")

    repaired: list[dict] = []
    for index in indexes:
        with database_connection() as connection:
            course = connection.execute(
                """
                select owner_id, ingestion_cost_cap_usd
                from video.courses where id = %s
                """,
                (course_id,),
            ).fetchone()
            if course is None:
                raise SystemExit("course not found")
            if course["ingestion_cost_cap_usd"] != Decimal("4.250000"):
                raise SystemExit("repair requires the existing $4.25 course cap")
            lecture = connection.execute(
                """
                select video_id from video.course_lectures
                where course_id = %s and owner_id = %s and lecture_index = %s
                """,
                (course_id, course["owner_id"], index),
            ).fetchone()
            if lecture is None:
                raise SystemExit(f"lecture index {index} is not in this course")
            creation = reingest_video(
                connection,
                owner_id=course["owner_id"],
                video_id=lecture["video_id"],
                idempotency_key=uuid5(
                    idempotency_key,
                    f"course:{course_id}:timeline-repair:{index}",
                ),
                quality_profile=FULL_QUALITY_PROFILE,
                cost_cap_usd=cap,
                rebuild_frames=True,
            )
            connection.execute(
                """
                update video.course_lectures set ingestion_job_id = %s
                where course_id = %s and owner_id = %s and lecture_index = %s
                """,
                (creation.job_id, course_id, course["owner_id"], index),
            )
            repaired.append(
                {
                    "lecture_index": index,
                    "video_id": str(lecture["video_id"]),
                    "job_id": str(creation.job_id),
                    "reused": not creation.created,
                }
            )
    return {"course_id": str(course_id), "repairs": repaired}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_id", type=UUID)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--idempotency-key", type=UUID, required=True)
    start_parser.add_argument("--cost-cap-usd", default="4.25")
    subparsers.add_parser("status")
    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--idempotency-key", type=UUID, required=True)
    repair_parser.add_argument(
        "--lecture-index", type=int, action="append", required=True
    )
    repair_parser.add_argument("--per-video-cap-usd", default="0.25")
    args = parser.parse_args()
    if args.command == "start":
        payload = start(args.course_id, args.idempotency_key, args.cost_cap_usd)
    elif args.command == "repair":
        payload = repair(
            args.course_id,
            args.idempotency_key,
            args.lecture_index,
            args.per_video_cap_usd,
        )
    else:
        payload = status(args.course_id)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
