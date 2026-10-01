"""Owner-scoped course aggregation over canonical lecture records."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from observability import traced
from storage.database import parse_owner_id
from video.repository import (
    FULL_QUALITY_PROFILE,
    create_youtube_video,
    load_video,
    reingest_video,
)
from video.sources import YouTubeSource, parse_youtube_url


class CourseNotFoundError(LookupError):
    pass


class CourseConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class CourseCreation:
    course_id: UUID
    created: bool


@dataclass(frozen=True)
class BatchLectureCreation:
    video_id: UUID
    ingestion_job_id: UUID | None
    reused: bool


@dataclass(frozen=True)
class BatchCourseCreation:
    course_id: UUID
    created: bool
    lectures: tuple[BatchLectureCreation, ...]


@dataclass(frozen=True)
class CourseQualityUpgrade:
    course_id: UUID
    ingestion_cost_cap_usd: Decimal
    lectures: tuple[BatchLectureCreation, ...]


@traced("video.course_repository.upgrade_course_quality", flow="course_ingestion")
def upgrade_course_quality(
    connection: Connection,
    *,
    owner_id: str | UUID,
    course_id: str | UUID,
    idempotency_key: str | UUID,
    ingestion_cost_cap_usd: str | Decimal = "4.250000",
) -> CourseQualityUpgrade:
    """Queue an atomic, resumable full-quality replacement for each lecture."""

    owner = parse_owner_id(owner_id)
    course = UUID(str(course_id))
    key = UUID(str(idempotency_key))
    try:
        cap = Decimal(str(ingestion_cost_cap_usd)).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("course ingestion cost cap must be a valid amount") from error
    if not cap.is_finite() or not Decimal("0") < cap < Decimal("5"):
        raise ValueError("course ingestion cost cap must be greater than 0 and below 5")

    with connection.transaction():
        course_row = connection.execute(
            """
            select id, actual_ingestion_cost_usd
            from video.courses
            where id = %s and owner_id = %s
            for update
            """,
            (course, owner),
        ).fetchone()
        if course_row is None:
            raise CourseNotFoundError("course does not exist")
        if cap < course_row["actual_ingestion_cost_usd"]:
            raise ValueError("course ingestion cost cap is below its existing spend")
        members = connection.execute(
            """
            select lecture.video_id
            from video.course_lectures as lecture
            join video.videos as video
              on video.id = lecture.video_id and video.owner_id = lecture.owner_id
            where lecture.course_id = %s and lecture.owner_id = %s
            order by lecture.lecture_index
            for update of lecture
            """,
            (course, owner),
        ).fetchall()
        if not members:
            raise CourseConflictError("course has no lectures to upgrade")
        connection.execute(
            """
            update video.courses
            set ingestion_cost_cap_usd = %s,
                metadata_json = metadata_json || %s,
                updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                cap,
                Jsonb(
                    {
                        "semantic_embeddings": True,
                        "quality_profile": FULL_QUALITY_PROFILE,
                        "text_embedding_dimension": 768,
                    }
                ),
                course,
                owner,
            ),
        )
        queued: list[BatchLectureCreation] = []
        for member in members:
            video_id = member["video_id"]
            creation = reingest_video(
                connection,
                owner_id=owner,
                video_id=video_id,
                idempotency_key=uuid5(key, f"course:{course}:video:{video_id}"),
                quality_profile=FULL_QUALITY_PROFILE,
            )
            connection.execute(
                """
                update video.course_lectures
                set ingestion_job_id = %s
                where course_id = %s and owner_id = %s and video_id = %s
                """,
                (creation.job_id, course, owner, video_id),
            )
            queued.append(
                BatchLectureCreation(
                    video_id=video_id,
                    ingestion_job_id=creation.job_id,
                    reused=not creation.created,
                )
            )
    return CourseQualityUpgrade(
        course_id=course,
        ingestion_cost_cap_usd=cap,
        lectures=tuple(queued),
    )


COURSE_SUMMARY_SELECT = """
    select course.id, course.owner_id, course.title, course.description,
           course.metadata_json, course.ingestion_cost_cap_usd,
           course.actual_ingestion_cost_usd,
           course.created_at, course.updated_at,
           count(lecture.id)::integer as lecture_count,
           -- Strictly ready. A degraded lecture answers questions, but it is
           -- missing part of its evidence contract, and folding it in here
           -- told a reader every lecture was complete when some were not.
           count(lecture.id) filter (
               where video.readiness_status = 'ready'
           )::integer as ready_count,
           count(lecture.id) filter (
               where video.readiness_status = 'degraded'
           )::integer as degraded_count,
           count(lecture.id) filter (
               where course_job.status in (
                   'awaiting_upload', 'queued', 'running', 'retry_scheduled'
               )
               or (
                   course_job.id is null
                   and video.readiness_status = 'processing'
               )
           )::integer as processing_count,
           count(lecture.id) filter (
               where course_job.status = 'failed'
               or (
                   course_job.id is null
                   and video.readiness_status = 'failed'
               )
           )::integer as failed_count,
           (
               select source.youtube_video_id
               from video.course_lectures as preview_lecture
               join video.video_sources as source
                 on source.video_id = preview_lecture.video_id
                and source.owner_id = preview_lecture.owner_id
                and source.is_primary
               where preview_lecture.course_id = course.id
                 and preview_lecture.owner_id = course.owner_id
                 and source.youtube_video_id is not null
               order by preview_lecture.lecture_index
               limit 1
           ) as preview_youtube_video_id
    from video.courses as course
    left join video.course_lectures as lecture
      on lecture.course_id = course.id and lecture.owner_id = course.owner_id
    left join video.videos as video
      on video.id = lecture.video_id and video.owner_id = lecture.owner_id
    left join video.ingestion_jobs as course_job
      on course_job.id = lecture.ingestion_job_id
     and course_job.owner_id = lecture.owner_id
"""


@traced("video.course_repository.create_course", flow="course_ingestion")
def create_course(
    connection: Connection,
    *,
    owner_id: str | UUID,
    creation_key: str | UUID,
    title: str,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CourseCreation:
    owner, key = parse_owner_id(owner_id), UUID(str(creation_key))
    clean_title = " ".join(title.split())
    if not clean_title:
        raise ValueError("course title cannot be blank")
    clean_description = description.strip() if description else None
    row = connection.execute(
        """
        insert into video.courses (
            owner_id, creation_key, title, description, metadata_json
        ) values (%s, %s, %s, %s, %s)
        on conflict (owner_id, creation_key) where creation_key is not null
        do nothing
        returning id
        """,
        (owner, key, clean_title, clean_description, Jsonb(metadata or {})),
    ).fetchone()
    if row is not None:
        return CourseCreation(course_id=row["id"], created=True)
    existing = connection.execute(
        """
        select id from video.courses
        where owner_id = %s and creation_key = %s
        """,
        (owner, key),
    ).fetchone()
    if existing is None:  # defensive: the unique conflict must name a row
        raise CourseConflictError("course creation could not be replayed")
    return CourseCreation(course_id=existing["id"], created=False)


@traced("video.course_repository.create_youtube_course", flow="course_ingestion")
def create_youtube_course(
    connection: Connection,
    *,
    owner_id: str | UUID,
    creation_key: str | UUID,
    title: str,
    lectures: Sequence[dict[str, str | None]],
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BatchCourseCreation:
    """Create one course and queue its ordered lectures atomically.

    Every URL is parsed before the first write. Existing canonical YouTube
    lectures are reused; new ones enter the ordinary video ingestion queue.
    """

    if not 1 <= len(lectures) <= 100:
        raise ValueError("a course must contain between 1 and 100 lectures")
    parsed: list[tuple[YouTubeSource, str | None, str | None]] = []
    seen: set[str] = set()
    for lecture in lectures:
        source = parse_youtube_url(str(lecture.get("url") or ""))
        if source.video_id in seen:
            raise ValueError("a course cannot contain the same YouTube video twice")
        seen.add(source.video_id)
        lecture_title = lecture.get("title")
        lecture_description = lecture.get("description")
        parsed.append(
            (
                source,
                str(lecture_title).strip() if lecture_title else None,
                str(lecture_description).strip() if lecture_description else None,
            )
        )

    owner, key = parse_owner_id(owner_id), UUID(str(creation_key))
    course = create_course(
        connection,
        owner_id=owner,
        creation_key=key,
        title=title,
        description=description,
        metadata={"semantic_embeddings": False, **(metadata or {})},
    )
    if not course.created:
        return BatchCourseCreation(
            course_id=course.course_id,
            created=False,
            lectures=tuple(
                BatchLectureCreation(
                    video_id=row["video_id"], ingestion_job_id=row["job_id"], reused=True
                )
                for row in _course_creation_lectures(
                    connection, owner=owner, course_id=course.course_id
                )
            ),
        )

    created_lectures: list[BatchLectureCreation] = []
    for index, (source, lecture_title, lecture_description) in enumerate(parsed):
        existing = connection.execute(
            """
            select video.id as video_id
            from video.video_sources as source
            join video.videos as video
              on video.id = source.video_id and video.owner_id = source.owner_id
            where source.owner_id = %s and source.youtube_video_id = %s
              and source.is_primary
            """,
            (owner, source.video_id),
        ).fetchone()
        if existing is not None:
            video_id, job_id, reused = existing["video_id"], None, True
        else:
            creation = create_youtube_video(
                connection,
                owner_id=owner,
                idempotency_key=uuid5(NAMESPACE_URL, f"course:{key}:lecture:{index}"),
                url=source.canonical_url,
                title=lecture_title,
                description=lecture_description,
            )
            video_id, job_id, reused = creation.video_id, creation.job_id, False
            # Course ingestion has its own bounded objective: ordered lectures
            # and grounded cross-lecture Q&A. The generic video publish hook
            # must not silently launch a separate paid flashcard workflow for
            # every playlist item.
            connection.execute(
                """
                update video.videos set cards_enabled = false
                where id = %s and owner_id = %s
                """,
                (video_id, owner),
            )
        connection.execute(
            """
            insert into video.course_lectures (
                owner_id, course_id, video_id, lecture_index, title_override,
                ingestion_job_id
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (owner, course.course_id, video_id, index, lecture_title, job_id),
        )
        created_lectures.append(
            BatchLectureCreation(
                video_id=video_id,
                ingestion_job_id=job_id,
                reused=reused,
            )
        )
    return BatchCourseCreation(
        course_id=course.course_id,
        created=True,
        lectures=tuple(created_lectures),
    )


def _course_creation_lectures(
    connection: Connection, *, owner: UUID, course_id: UUID
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select lecture.video_id, lecture.ingestion_job_id as job_id
        from video.course_lectures as lecture
        where lecture.owner_id = %s and lecture.course_id = %s
        order by lecture.lecture_index
        """,
        (owner, course_id),
    ).fetchall()


def list_courses(
    connection: Connection, *, owner_id: str | UUID, limit: int = 50
) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("course limit must be between 1 and 100")
    return connection.execute(
        COURSE_SUMMARY_SELECT
        + """
        where course.owner_id = %s
        group by course.id
        order by course.updated_at desc, course.id desc
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()


def load_course(
    connection: Connection, course_id: str | UUID, *, owner_id: str | UUID
) -> dict[str, Any] | None:
    return connection.execute(
        COURSE_SUMMARY_SELECT
        + """
        where course.owner_id = %s and course.id = %s
        group by course.id
        """,
        (parse_owner_id(owner_id), UUID(str(course_id))),
    ).fetchone()


def list_course_lectures(
    connection: Connection, course_id: str | UUID, *, owner_id: str | UUID
) -> list[dict[str, Any]]:
    owner, course = parse_owner_id(owner_id), UUID(str(course_id))
    memberships = connection.execute(
        """
        select video_id, lecture_index, title_override
        from video.course_lectures
        where owner_id = %s and course_id = %s
        order by lecture_index
        """,
        (owner, course),
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for membership in memberships:
        video = load_video(
            connection, membership["video_id"], owner_id=owner
        )
        if video is not None:
            rows.append({**video, **membership})
    return rows


def update_course(
    connection: Connection,
    course_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str | None = None,
    description: str | None = None,
    update_description: bool = False,
) -> dict[str, Any] | None:
    assignments: list[str] = []
    values: list[Any] = []
    if title is not None:
        clean = " ".join(title.split())
        if not clean:
            raise ValueError("course title cannot be blank")
        assignments.append("title = %s")
        values.append(clean)
    if update_description:
        assignments.append("description = %s")
        values.append(description.strip() if description else None)
    if not assignments:
        raise ValueError("at least one course field is required")
    values.extend((UUID(str(course_id)), parse_owner_id(owner_id)))
    row = connection.execute(
        f"""
        update video.courses set {', '.join(assignments)}
        where id = %s and owner_id = %s returning id
        """,
        values,
    ).fetchone()
    return (
        load_course(connection, row["id"], owner_id=owner_id)
        if row is not None
        else None
    )


def delete_course(
    connection: Connection, course_id: str | UUID, *, owner_id: str | UUID
) -> bool:
    return (
        connection.execute(
            "delete from video.courses where id = %s and owner_id = %s returning id",
            (UUID(str(course_id)), parse_owner_id(owner_id)),
        ).fetchone()
        is not None
    )


def attach_course_lecture(
    connection: Connection,
    course_id: str | UUID,
    video_id: str | UUID,
    *,
    owner_id: str | UUID,
    title_override: str | None = None,
) -> None:
    owner, course, video = (
        parse_owner_id(owner_id),
        UUID(str(course_id)),
        UUID(str(video_id)),
    )
    if load_course(connection, course, owner_id=owner) is None:
        raise CourseNotFoundError("course does not exist")
    if load_video(connection, video, owner_id=owner) is None:
        raise CourseNotFoundError("lecture does not exist")
    index = connection.execute(
        """
        select coalesce(max(lecture_index), -1) + 1 as next_index
        from video.course_lectures where owner_id = %s and course_id = %s
        """,
        (owner, course),
    ).fetchone()["next_index"]
    try:
        connection.execute(
            """
            insert into video.course_lectures (
                owner_id, course_id, video_id, lecture_index, title_override
            ) values (%s, %s, %s, %s, %s)
            """,
            (
                owner,
                course,
                video,
                index,
                title_override.strip() if title_override else None,
            ),
        )
    except Exception as error:
        if getattr(error, "sqlstate", None) == "23505":
            raise CourseConflictError("lecture is already in this course") from error
        raise


def update_course_lecture(
    connection: Connection,
    course_id: str | UUID,
    video_id: str | UUID,
    *,
    owner_id: str | UUID,
    lecture_index: int | None = None,
    title_override: str | None = None,
    update_title: bool = False,
) -> None:
    owner, course, video = (
        parse_owner_id(owner_id),
        UUID(str(course_id)),
        UUID(str(video_id)),
    )
    rows = connection.execute(
        """
        select video_id from video.course_lectures
        where owner_id = %s and course_id = %s order by lecture_index
        """,
        (owner, course),
    ).fetchall()
    ordered = [row["video_id"] for row in rows]
    if video not in ordered:
        raise CourseNotFoundError("course lecture does not exist")
    if lecture_index is not None:
        ordered.remove(video)
        ordered.insert(min(lecture_index, len(ordered)), video)
        offset = len(ordered) + 1
        connection.execute(
            """
            update video.course_lectures
            set lecture_index = lecture_index + %s
            where owner_id = %s and course_id = %s
            """,
            (offset, owner, course),
        )
        for index, identifier in enumerate(ordered):
            connection.execute(
                """
                update video.course_lectures set lecture_index = %s
                where owner_id = %s and course_id = %s and video_id = %s
                """,
                (index, owner, course, identifier),
            )
    if update_title:
        connection.execute(
            """
            update video.course_lectures set title_override = %s
            where owner_id = %s and course_id = %s and video_id = %s
            """,
            (
                title_override.strip() if title_override else None,
                owner,
                course,
                video,
            ),
        )


def detach_course_lecture(
    connection: Connection,
    course_id: str | UUID,
    video_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> bool:
    owner, course, video = (
        parse_owner_id(owner_id),
        UUID(str(course_id)),
        UUID(str(video_id)),
    )
    deleted = connection.execute(
        """
        delete from video.course_lectures
        where owner_id = %s and course_id = %s and video_id = %s
        returning id
        """,
        (owner, course, video),
    ).fetchone()
    if deleted is None:
        return False
    rows = connection.execute(
        """
        select video_id from video.course_lectures
        where owner_id = %s and course_id = %s order by lecture_index
        """,
        (owner, course),
    ).fetchall()
    for index, row in enumerate(rows):
        connection.execute(
            """
            update video.course_lectures set lecture_index = %s
            where owner_id = %s and course_id = %s and video_id = %s
            """,
            (index, owner, course, row["video_id"]),
        )
    return True


def course_member_ids(
    connection: Connection, course_id: str | UUID, *, owner_id: str | UUID
) -> list[UUID]:
    return [
        row["video_id"]
        for row in connection.execute(
            """
            select video_id from video.course_lectures
            where owner_id = %s and course_id = %s order by lecture_index
            """,
            (parse_owner_id(owner_id), UUID(str(course_id))),
        ).fetchall()
    ]
