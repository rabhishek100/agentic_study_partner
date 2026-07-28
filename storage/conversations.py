"""Owner-scoped persistence for study conversations.

`conversation_turns` is canonical; `conversations.state_json` is a derived
resume checkpoint. See the migration for why both are stored.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id


# Long enough to tell two conversations apart in a list, short enough not to
# wrap in the sidebar.
TITLE_LIMIT = 60


def derive_title(question: str) -> str:
    """Name a conversation after the question that started it."""

    collapsed = " ".join(question.split())
    if not collapsed:
        return "New conversation"
    if len(collapsed) <= TITLE_LIMIT:
        return collapsed
    # Cut on a word boundary when there is one reasonably close to the limit.
    clipped = collapsed[:TITLE_LIMIT]
    boundary = clipped.rfind(" ")
    if boundary >= TITLE_LIMIT // 2:
        clipped = clipped[:boundary]
    return f"{clipped.rstrip()}…"


def create_conversation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_ids: Sequence[int],
    retrieval_mode: str,
    title: str,
) -> dict[str, Any]:
    scope = sorted({int(identifier) for identifier in book_ids})
    if not scope:
        raise ValueError("a conversation must be scoped to at least one book")
    return connection.execute(
        """
        insert into conversations (owner_id, title, book_ids, retrieval_mode)
        values (%s, %s, %s, %s)
        returning id, title, book_ids, retrieval_mode, state_json,
                  created_at, updated_at
        """,
        (parse_owner_id(owner_id), title, scope, retrieval_mode),
    ).fetchone()


def load_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    """Load one conversation, or None when it is not this owner's.

    Missing and someone else's are deliberately indistinguishable.
    """

    return connection.execute(
        """
        select id, title, book_ids, retrieval_mode, state_json,
               created_at, updated_at
        from conversations
        where id = %s and owner_id = %s
        """,
        (conversation_id, parse_owner_id(owner_id)),
    ).fetchone()


def list_conversations(
    connection: Connection,
    *,
    owner_id: str | UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List one owner's conversations, most recently used first."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    return connection.execute(
        """
        select
            conversations.id,
            conversations.title,
            conversations.book_ids,
            conversations.retrieval_mode,
            conversations.created_at,
            conversations.updated_at,
            count(conversation_turns.id) as turn_count
        from conversations
        left join conversation_turns
          on conversation_turns.conversation_id = conversations.id
         and conversation_turns.owner_id = conversations.owner_id
        where conversations.owner_id = %s
        group by conversations.id
        order by conversations.updated_at desc
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()


def load_turns(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select turn_index, question, answer, result_json, created_at
        from conversation_turns
        where conversation_id = %s and owner_id = %s
        order by turn_index
        """,
        (conversation_id, parse_owner_id(owner_id)),
    ).fetchall()


def append_turn(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    question: str,
    answer: str,
    result: dict[str, Any],
    state: dict[str, Any],
) -> int:
    """Record one settled turn and refresh the resume checkpoint.

    The turn index is derived inside the statement rather than passed in, so
    two concurrent turns cannot both claim the same index — the unique
    constraint would reject the second, which is the intended outcome.
    """

    owner = parse_owner_id(owner_id)
    row = connection.execute(
        """
        insert into conversation_turns (
            owner_id, conversation_id, turn_index, question, answer, result_json
        )
        select
            %s, %s,
            coalesce(max(turn_index) + 1, 0),
            %s, %s, %s
        from conversation_turns
        where conversation_id = %s and owner_id = %s
        returning turn_index
        """,
        (
            owner,
            conversation_id,
            question,
            answer,
            Jsonb(result),
            conversation_id,
            owner,
        ),
    ).fetchone()
    connection.execute(
        """
        update conversations
        set state_json = %s, updated_at = now()
        where id = %s and owner_id = %s
        """,
        (Jsonb(state), conversation_id, owner),
    )
    return row["turn_index"]


def update_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str | None = None,
    retrieval_mode: str | None = None,
) -> dict[str, Any] | None:
    """Rename a conversation or change its retrieval mode.

    The book selection is deliberately not editable: answers already in the
    conversation were grounded in the current selection, so a different one
    starts a new conversation instead.
    """

    assignments = []
    parameters: list[Any] = []
    if title is not None:
        assignments.append("title = %s")
        parameters.append(title)
    if retrieval_mode is not None:
        assignments.append("retrieval_mode = %s")
        parameters.append(retrieval_mode)
    if not assignments:
        return load_conversation(connection, conversation_id, owner_id=owner_id)

    assignments.append("updated_at = now()")
    parameters.extend([conversation_id, parse_owner_id(owner_id)])
    return connection.execute(
        f"""
        update conversations set {", ".join(assignments)}
        where id = %s and owner_id = %s
        returning id, title, book_ids, retrieval_mode, state_json,
                  created_at, updated_at
        """,
        parameters,
    ).fetchone()


def delete_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> bool:
    """Delete a conversation and its turns. Returns whether one was removed."""

    row = connection.execute(
        "delete from conversations where id = %s and owner_id = %s returning id",
        (conversation_id, parse_owner_id(owner_id)),
    ).fetchone()
    return row is not None
