"""Server-authoritative persistence for course conversations and turns."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.conversation_store import (
    MAXIMUM_TITLE_CHARACTERS,
    PLACEHOLDER_TITLE,
    VideoTurnCostExceeded,
    derive_title,
)
from video.course_repository import course_member_ids, load_course
from video.models import MAXIMUM_TURN_COST_USD


class CourseConversationNotFoundError(LookupError):
    pass


CONVERSATION_COLUMNS = """
    id, course_id, title, selected_video_ids, prompt_snapshot_json,
    state_json, created_at, updated_at
"""


def create_conversation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    course_id: str | UUID,
    title: str = PLACEHOLDER_TITLE,
    selected_video_ids: list[UUID] | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    conversation_id: str | UUID | None = None,
) -> dict[str, Any]:
    owner, course = parse_owner_id(owner_id), UUID(str(course_id))
    if load_course(connection, course, owner_id=owner) is None:
        raise CourseConversationNotFoundError("course does not exist")
    members = course_member_ids(connection, course, owner_id=owner)
    selected = list(dict.fromkeys(selected_video_ids or members))
    if not selected:
        raise ValueError("add at least one lecture before starting a conversation")
    if not set(selected).issubset(members):
        raise ValueError("selected lectures must belong to this course")
    identifier = UUID(str(conversation_id)) if conversation_id else uuid4()
    return connection.execute(
        f"""
        insert into video.course_conversations (
            id, owner_id, course_id, title, selected_video_ids,
            prompt_snapshot_json, state_json
        ) values (%s, %s, %s, %s, %s, %s, %s)
        returning {CONVERSATION_COLUMNS}
        """,
        (
            identifier,
            owner,
            course,
            derive_title(title),
            selected,
            Jsonb(prompt_snapshot or {}),
            Jsonb(state or {}),
        ),
    ).fetchone()


def list_conversations(
    connection: Connection,
    *,
    owner_id: str | UUID,
    course_id: str | UUID,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select conversation.id, conversation.course_id, conversation.title,
               conversation.selected_video_ids, conversation.created_at,
               conversation.updated_at, count(turn.id)::integer as turn_count
        from video.course_conversations as conversation
        left join video.course_conversation_turns as turn
          on turn.conversation_id = conversation.id
         and turn.owner_id = conversation.owner_id
        where conversation.owner_id = %s and conversation.course_id = %s
        group by conversation.id
        order by conversation.updated_at desc, conversation.id
        """,
        (parse_owner_id(owner_id), UUID(str(course_id))),
    ).fetchall()


def load_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    return connection.execute(
        f"""
        select {CONVERSATION_COLUMNS}
        from video.course_conversations
        where id = %s and owner_id = %s
        """,
        (UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchone()


def load_turns(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select turn_index, status, question, rewritten_query, answer,
               result_json, actual_cost_usd, trace_id, error_code,
               created_at, completed_at
        from video.course_conversation_turns
        where conversation_id = %s and owner_id = %s
        order by turn_index
        """,
        (UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchall()


def append_turn(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    course_id: str | UUID,
    versions: dict[UUID, UUID],
    question: str,
    rewritten_query: str,
    answer: str,
    result: dict[str, Any],
    state: dict[str, Any],
    cost_usd: float | Decimal = 0,
    trace_id: str | None = None,
) -> int:
    owner, conversation, course = (
        parse_owner_id(owner_id),
        UUID(str(conversation_id)),
        UUID(str(course_id)),
    )
    cost = Decimal(str(cost_usd)).quantize(Decimal("0.000001"))
    if cost > Decimal(str(MAXIMUM_TURN_COST_USD)):
        raise VideoTurnCostExceeded(
            f"one course answer may not cost more than ${MAXIMUM_TURN_COST_USD}"
        )
    with connection.transaction():
        row = connection.execute(
            """
            insert into video.course_conversation_turns (
                owner_id, conversation_id, course_id, turn_index, status,
                question, rewritten_query, answer, result_json,
                actual_cost_usd, trace_id, completed_at
            )
            select %s, %s, %s, coalesce(max(turn_index) + 1, 0), 'complete',
                   %s, %s, %s, %s, %s, %s, now()
            from video.course_conversation_turns
            where conversation_id = %s and owner_id = %s
            returning id, turn_index
            """,
            (
                owner,
                conversation,
                course,
                question,
                rewritten_query,
                answer,
                Jsonb(result),
                cost,
                trace_id,
                conversation,
                owner,
            ),
        ).fetchone()
        for video_id, version_id in versions.items():
            connection.execute(
                """
                insert into video.course_turn_versions (
                    owner_id, conversation_id, turn_id, video_id,
                    ingestion_version_id
                ) values (%s, %s, %s, %s, %s)
                """,
                (owner, conversation, row["id"], video_id, version_id),
            )
        connection.execute(
            """
            update video.course_conversations
            set state_json = %s,
                title = case
                    when %s = 0 and title = %s then %s else title
                end,
                updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                Jsonb(state),
                row["turn_index"],
                PLACEHOLDER_TITLE,
                derive_title(question),
                conversation,
                owner,
            ),
        )
    return int(row["turn_index"])


def rename_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str,
) -> dict[str, Any] | None:
    clean = " ".join(title.split())
    if not clean:
        raise ValueError("conversation title cannot be blank")
    clean = clean[:MAXIMUM_TITLE_CHARACTERS]
    return connection.execute(
        f"""
        update video.course_conversations set title = %s, updated_at = now()
        where id = %s and owner_id = %s returning {CONVERSATION_COLUMNS}
        """,
        (clean, UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchone()


def delete_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> bool:
    return (
        connection.execute(
            """
            delete from video.course_conversations
            where id = %s and owner_id = %s returning id
            """,
            (UUID(str(conversation_id)), parse_owner_id(owner_id)),
        ).fetchone()
        is not None
    )
