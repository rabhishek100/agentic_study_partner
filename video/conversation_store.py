"""Server-authoritative persistence for video conversations and their turns.

This mirrors the book conversation store's mechanics — the client never holds
the truth, the turn index is derived inside the insert so two concurrent turns
cannot claim the same one — over the video tables, whose turns additionally
pin the published ingestion version that supplied their evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from video.models import MAXIMUM_LECTURE_COST_USD, MAXIMUM_TURN_COST_USD


MAXIMUM_TITLE_CHARACTERS = 80
# Routes that read the whole lecture rather than a search of it.
LECTURE_SCOPE_ROUTES = frozenset({"lecture_summary", "topic_inventory"})
# What a conversation is called before anything has been asked in it. Kept as
# a constant because the first turn has to recognize it to replace it.
PLACEHOLDER_TITLE = "New conversation"


class VideoConversationNotFoundError(LookupError):
    """No such conversation exists for this owner."""


class VideoTurnCostExceeded(RuntimeError):
    """The turn reported more spend than one answer is allowed to cost."""


def cost_ceiling(route: str) -> float:
    """The most one turn on this route is allowed to have cost."""

    return (
        MAXIMUM_LECTURE_COST_USD
        if route in LECTURE_SCOPE_ROUTES
        else MAXIMUM_TURN_COST_USD
    )


def derive_title(question: str) -> str:
    cleaned = " ".join(question.split())
    if len(cleaned) <= MAXIMUM_TITLE_CHARACTERS:
        return cleaned or PLACEHOLDER_TITLE
    return cleaned[: MAXIMUM_TITLE_CHARACTERS - 1].rstrip() + "…"


CONVERSATION_COLUMNS = """
    id, video_id, title, retrieval_mode, retrieval_config_json,
    prompt_snapshot_json, state_json, parent_conversation_id, anchors_json,
    session_kind, source_position, created_at, updated_at
"""


def create_conversation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    title: str,
    retrieval_mode: str = "hybrid",
    retrieval_config: dict[str, Any] | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
    parent_conversation_id: str | UUID | None = None,
    anchors: Sequence[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    session_kind: str | None = None,
) -> dict[str, Any]:
    """Create a lecture conversation, or a side chat when a parent is named.

    A side chat may be seeded with state — the anchored turn's answer, evidence
    and citations — so asking it to reword that answer works on its first turn.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    if anchors and parent_conversation_id is None:
        raise ValueError("anchors require a parent conversation")
    row = connection.execute(
        f"""
        insert into video.conversations (
            owner_id, video_id, title, retrieval_mode, retrieval_config_json,
            prompt_snapshot_json, state_json, parent_conversation_id,
            anchors_json, session_kind
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        where exists (
            select 1 from video.videos
            where id = %s and owner_id = %s
        )
        returning {CONVERSATION_COLUMNS}
        """,
        (
            owner,
            video,
            derive_title(title),
            retrieval_mode,
            Jsonb(retrieval_config or {}),
            Jsonb(prompt_snapshot or {}),
            Jsonb(state or {}),
            UUID(str(parent_conversation_id)) if parent_conversation_id else None,
            Jsonb(list(anchors or ())),
            session_kind,
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
        f"""
        select {CONVERSATION_COLUMNS}
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
               count(turn.id) as turn_count,
               (
                   select count(*)
                   from video.conversations as side
                   where side.parent_conversation_id = conversation.id
                     and side.owner_id = conversation.owner_id
               ) as side_thread_count
        from video.conversations as conversation
        join video.videos as video
          on video.id = conversation.video_id
         and video.owner_id = conversation.owner_id
        left join video.conversation_turns as turn
          on turn.conversation_id = conversation.id
         and turn.owner_id = conversation.owner_id
        where conversation.owner_id = %s {predicate}
          and conversation.parent_conversation_id is null
          -- A watch session is a root too, but it is not a thread: it would
          -- sit in the history named after a lecture with none of its
          -- questions under it, because those are its side chats.
          and conversation.session_kind is null
        group by conversation.id, video.title
        order by conversation.updated_at desc, conversation.id
        limit %s
        """,
        parameters,
    ).fetchall()


def watch_session(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
) -> dict[str, Any] | None:
    """This viewer's watch session for one lecture, if they have started it."""

    return connection.execute(
        f"""
        select {CONVERSATION_COLUMNS}
        from video.conversations
        where owner_id = %s and video_id = %s and session_kind = 'watch'
        """,
        (parse_owner_id(owner_id), UUID(str(video_id))),
    ).fetchone()


def list_watch_sessions(
    connection: Connection,
    *,
    owner_id: str | UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Lectures this viewer has started, most recently watched first."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    return connection.execute(
        """
        select
            conversations.id,
            conversations.video_id,
            conversations.title,
            conversations.source_position,
            conversations.updated_at,
            (
                select count(*)
                from video.conversations as side
                where side.parent_conversation_id = conversations.id
                  and side.owner_id = conversations.owner_id
            ) as question_count
        from video.conversations as conversations
        where conversations.owner_id = %s and conversations.session_kind = 'watch'
        order by conversations.updated_at desc
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()


def set_source_position(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    position: dict[str, Any],
) -> dict[str, Any] | None:
    """Record where the viewer is, without disturbing anything else.

    Deliberately does not touch `updated_at`, exactly as the reading side does
    not: the playhead moves several times a second while a lecture plays, and
    recency here means "was used" rather than "was left running".

    It takes more to be true here than on the book side. `video.conversations`
    carries a trigger that stamps `updated_at` on every update, so this rests
    on `video.set_conversation_updated_at` skipping the stamp when nothing but
    the position changed — which is why that function exists and the shared
    `video.set_updated_at` was left alone.
    """

    return connection.execute(
        f"""
        update video.conversations
        set source_position = %s
        where id = %s and owner_id = %s and session_kind = 'watch'
        returning {CONVERSATION_COLUMNS}
        """,
        (Jsonb(position), UUID(str(conversation_id)), parse_owner_id(owner_id)),
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
    maximum_cost_usd: float = MAXIMUM_TURN_COST_USD,
) -> int:
    """Record one settled turn, refresh the checkpoint, and name the thread.

    A video conversation is created before its first question is answered, so
    it is born holding the placeholder title. The first turn to land replaces
    it with the question, which is what the book conversations have always
    been called. A conversation the reader has already named keeps that name,
    and so does one whose title came from a client that supplied it.
    """

    owner = parse_owner_id(owner_id)
    cost = Decimal(str(cost_usd)).quantize(Decimal("0.000001"))
    if cost > Decimal(str(maximum_cost_usd)):
        raise VideoTurnCostExceeded(
            f"one video answer may not cost more than ${maximum_cost_usd}"
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
        turn_index = int(row["turn_index"])
        connection.execute(
            """
            update video.conversations
            set state_json = %s,
                title = case
                    when %s = 0 and title = %s then %s
                    else title
                end,
                updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                Jsonb(state),
                turn_index,
                PLACEHOLDER_TITLE,
                derive_title(question),
                UUID(str(conversation_id)),
                owner,
            ),
        )
    return turn_index


def rename_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str,
) -> dict[str, Any] | None:
    return connection.execute(
        f"""
        update video.conversations set title = %s, updated_at = now()
        where id = %s and owner_id = %s
        returning {CONVERSATION_COLUMNS}
        """,
        (derive_title(title), UUID(str(conversation_id)), parse_owner_id(owner_id)),
    ).fetchone()


def list_side_chats(
    connection: Connection,
    parent_conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> list[dict[str, Any]]:
    """List one lecture conversation's side chats, most recently used first."""

    return connection.execute(
        """
        select conversation.id, conversation.video_id, conversation.title,
               conversation.retrieval_mode, conversation.parent_conversation_id,
               conversation.anchors_json, conversation.created_at,
               conversation.updated_at, count(turn.id) as turn_count
        from video.conversations as conversation
        left join video.conversation_turns as turn
          on turn.conversation_id = conversation.id
         and turn.owner_id = conversation.owner_id
        where conversation.owner_id = %s
          and conversation.parent_conversation_id = %s
        group by conversation.id
        order by conversation.updated_at desc, conversation.id
        """,
        (parse_owner_id(owner_id), UUID(str(parent_conversation_id))),
    ).fetchall()


def set_anchors(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    anchors: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Replace the passages a side chat is anchored to, wholesale.

    The whole set rather than a patch, for the same reason as the book store: the
    client always knows the intended set, and merging would only add a way for
    two windows to disagree about it.
    """

    return connection.execute(
        f"""
        update video.conversations
        set anchors_json = %s, updated_at = now()
        where id = %s and owner_id = %s and parent_conversation_id is not null
        returning {CONVERSATION_COLUMNS}
        """,
        (
            Jsonb(list(anchors)),
            UUID(str(conversation_id)),
            parse_owner_id(owner_id),
        ),
    ).fetchone()


def set_conversation_state(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    state: dict[str, Any],
) -> None:
    """Seed a side chat with the answer it was opened over.

    Unlike the book store, this does bump `updated_at`: `video.conversations`
    has a trigger that stamps it on every update, and suppressing that for one
    statement would be a worse trade than the ordering it buys. The effect is
    only that a freshly created side chat sorts first among its siblings, which
    it would anyway.
    """

    connection.execute(
        """
        update video.conversations set state_json = %s
        where id = %s and owner_id = %s
        """,
        (Jsonb(state), UUID(str(conversation_id)), parse_owner_id(owner_id)),
    )


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
