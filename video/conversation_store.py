"""Server-authoritative persistence for video conversations and their turns.

This mirrors the book conversation store's mechanics — the client never holds
the truth, the turn index is derived inside the insert so two concurrent turns
cannot claim the same one — over the video tables, whose turns additionally
pin the published ingestion version that supplied their evidence.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.models import MAXIMUM_TURN_COST_USD


MAXIMUM_TITLE_CHARACTERS = 80


class VideoConversationNotFoundError(LookupError):
    """No such conversation exists for this owner."""


class VideoTurnCostExceeded(RuntimeError):
    """The turn reported more spend than one answer is allowed to cost."""


def derive_title(question: str) -> str:
    cleaned = " ".join(question.split())
    if len(cleaned) <= MAXIMUM_TITLE_CHARACTERS:
        return cleaned or "New conversation"
    return cleaned[: MAXIMUM_TITLE_CHARACTERS - 1].rstrip() + "…"


def create_conversation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    title: str,
    retrieval_mode: str = "hybrid",
    retrieval_config: dict[str, Any] | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    row = connection.execute(
        """
        insert into video.conversations (
            owner_id, video_id, title, retrieval_mode, retrieval_config_json,
            prompt_snapshot_json, state_json
        )
        select %s, %s, %s, %s, %s, %s, %s
        where exists (
            select 1 from video.videos
            where id = %s and owner_id = %s
        )
        returning id, video_id, title, retrieval_mode, retrieval_config_json,
                  prompt_snapshot_json, state_json, created_at, updated_at
        """,
        (
            owner,
            video,
            derive_title(title),
            retrieval_mode,
            Jsonb(retrieval_config or {}),
            Jsonb(prompt_snapshot or {}),
            Jsonb({}),
            video,
            owner,
        ),
    ).fetchone()
    if row is None:
        raise VideoConversationNotFoundError("video does not exist")
    return row


def load_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    return connection.execute(
        """
        select id, video_id, title, retrieval_mode, retrieval_config_json,
               prompt_snapshot_json, state_json, created_at, updated_at
        from video.conversations
        where id = %s and owner_id = %s
        """,
        (UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchone()


def list_conversations(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    owner = parse_owner_id(owner_id)
    predicate, parameters = "", [owner]
    if video_id is not None:
        predicate = "and conversation.video_id = %s"
        parameters.append(UUID(str(video_id)))
    parameters.append(max(1, min(limit, 200)))
    return connection.execute(
        f"""
        select conversation.id, conversation.video_id, conversation.title,
               conversation.retrieval_mode, conversation.created_at,
               conversation.updated_at, video.title as video_title,
               count(turn.id) as turn_count
        from video.conversations as conversation
        join video.videos as video
          on video.id = conversation.video_id
         and video.owner_id = conversation.owner_id
        left join video.conversation_turns as turn
          on turn.conversation_id = conversation.id
         and turn.owner_id = conversation.owner_id
        where conversation.owner_id = %s {predicate}
        group by conversation.id, video.title
        order by conversation.updated_at desc, conversation.id
        limit %s
        """,
        parameters,
    ).fetchall()


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
        from video.conversation_turns
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
    video_id: str | UUID,
    ingestion_version_id: str | UUID,
    question: str,
    rewritten_query: str,
    answer: str,
    result: dict[str, Any],
    state: dict[str, Any],
    cost_usd: float | Decimal = 0,
    trace_id: str | None = None,
) -> int:
    """Record one settled turn and refresh the resume checkpoint."""

    owner = parse_owner_id(owner_id)
    cost = Decimal(str(cost_usd)).quantize(Decimal("0.000001"))
    if cost > Decimal(str(MAXIMUM_TURN_COST_USD)):
        raise VideoTurnCostExceeded(
            f"one video answer may not cost more than ${MAXIMUM_TURN_COST_USD}"
        )
    with connection.transaction():
        row = connection.execute(
            """
            insert into video.conversation_turns (
                owner_id, conversation_id, video_id, ingestion_version_id,
                turn_index, status, question, rewritten_query, answer,
                result_json, actual_cost_usd, trace_id, completed_at
            )
            select %s, %s, %s, %s,
                   coalesce(max(turn_index) + 1, 0),
                   'complete', %s, %s, %s, %s, %s, %s, now()
            from video.conversation_turns
            where conversation_id = %s and owner_id = %s
            returning turn_index
            """,
            (
                owner,
                UUID(str(conversation_id)),
                UUID(str(video_id)),
                UUID(str(ingestion_version_id)),
                question,
                rewritten_query,
                answer,
                Jsonb(result),
                cost,
                trace_id,
                UUID(str(conversation_id)),
                owner,
            ),
        ).fetchone()
        connection.execute(
            """
            update video.conversations
            set state_json = %s, updated_at = now()
            where id = %s and owner_id = %s
            """,
            (Jsonb(state), UUID(str(conversation_id)), owner),
        )
    return int(row["turn_index"])


def rename_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str,
) -> dict[str, Any] | None:
    return connection.execute(
        """
        update video.conversations set title = %s, updated_at = now()
        where id = %s and owner_id = %s
        returning id, video_id, title, retrieval_mode, retrieval_config_json,
                  prompt_snapshot_json, state_json, created_at, updated_at
        """,
        (derive_title(title), UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchone()


def delete_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> bool:
    row = connection.execute(
        """
        delete from video.conversations where id = %s and owner_id = %s
        returning id
        """,
        (UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchone()
    return row is not None
