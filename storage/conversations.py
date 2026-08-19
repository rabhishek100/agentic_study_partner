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


CONVERSATION_COLUMNS = """
    id, title, book_ids, document_type, retrieval_mode, prompt_profile_json, state_json,
    parent_conversation_id, anchors_json, session_kind, source_position,
    created_at, updated_at
"""


def create_conversation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_ids: Sequence[int],
    retrieval_mode: str,
    title: str,
    document_type: str | None = None,
    prompt_profile: dict[str, Any] | None = None,
    parent_conversation_id: str | UUID | None = None,
    anchors: Sequence[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    session_kind: str | None = None,
) -> dict[str, Any]:
    """Create a conversation, or a side chat when a parent is named.

    A side chat may be seeded with state — the anchored turn's answer, evidence
    and scope — so that asking it to reword or shorten that answer works on its
    first turn instead of only after it has produced one of its own.
    """

    scope = sorted({int(identifier) for identifier in book_ids})
    if not scope:
        raise ValueError("a conversation must be scoped to at least one book")
    if anchors and parent_conversation_id is None:
        raise ValueError("anchors require a parent conversation")

    if document_type is None:
        row = connection.execute(
            "select document_type from books where id = any(%s) limit 1",
            (scope,),
        ).fetchone()
        document_type = row["document_type"] if row and row.get("document_type") else "book"

    return connection.execute(
        f"""
        insert into conversations (
            owner_id, title, book_ids, document_type, retrieval_mode, prompt_profile_json,
            parent_conversation_id, anchors_json, state_json, session_kind
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {CONVERSATION_COLUMNS}
        """,
        (
            parse_owner_id(owner_id),
            title,
            scope,
            document_type,
            retrieval_mode,
            Jsonb(prompt_profile or {}),
            parent_conversation_id,
            Jsonb(list(anchors or ())),
            Jsonb(state or {}),
            session_kind,
        ),
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
        f"""
        select {CONVERSATION_COLUMNS}
        from conversations
        where id = %s and owner_id = %s
        """,
        (conversation_id, parse_owner_id(owner_id)),
    ).fetchone()


def list_conversations(
    connection: Connection,
    *,
    owner_id: str | UUID,
    document_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List one owner's root conversations, most recently used first.

    Side chats are deliberately excluded and counted instead. They belong to a
    passage of one conversation, so listing them beside their parent would put
    a dozen "what does this mean?" threads above the session they came from.
    The interface nests them under the parent using `side_thread_count`.
    """

    if limit <= 0:
        raise ValueError("limit must be positive")

    # Reading sessions are roots too, but they are not threads: one would sit
    # in the chat history named after a book, with none of its questions under
    # it, because those are its side chats. The library's continue band lists
    # them instead, through `list_reading_sessions`.
    where_clause = """
        where conversations.owner_id = %s
          and conversations.parent_conversation_id is null
          and conversations.session_kind is null
    """
    params: list[Any] = [parse_owner_id(owner_id)]
    if document_type:
        where_clause += " and conversations.document_type = %s"
        params.append(document_type)

    params.append(limit)

    return connection.execute(
        f"""
        select
            conversations.id,
            conversations.title,
            conversations.book_ids,
            conversations.document_type,
            conversations.retrieval_mode,
            conversations.created_at,
            conversations.updated_at,
            count(conversation_turns.id) as turn_count,
            (
                select count(*)
                from conversations as side
                where side.parent_conversation_id = conversations.id
                  and side.owner_id = conversations.owner_id
            ) as side_thread_count
        from conversations
        left join conversation_turns
          on conversation_turns.conversation_id = conversations.id
         and conversation_turns.owner_id = conversations.owner_id
        {where_clause}
        group by conversations.id
        order by conversations.updated_at desc
        limit %s
        """,
        params,
    ).fetchall()


def list_side_chats(
    connection: Connection,
    parent_conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> list[dict[str, Any]]:
    """List one conversation's side chats, most recently used first."""

    return connection.execute(
        """
        select
            conversations.id,
            conversations.title,
            conversations.book_ids,
            conversations.retrieval_mode,
            conversations.parent_conversation_id,
            conversations.anchors_json,
            conversations.created_at,
            conversations.updated_at,
            count(conversation_turns.id) as turn_count
        from conversations
        left join conversation_turns
          on conversation_turns.conversation_id = conversations.id
         and conversation_turns.owner_id = conversations.owner_id
        where conversations.owner_id = %s
          and conversations.parent_conversation_id = %s
        group by conversations.id
        order by conversations.updated_at desc
        """,
        (parse_owner_id(owner_id), parent_conversation_id),
    ).fetchall()


def reading_session(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_id: int,
) -> dict[str, Any] | None:
    """This reader's reading session for one source, if they have started it."""

    return connection.execute(
        f"""
        select {CONVERSATION_COLUMNS}
        from conversations
        where owner_id = %s and session_kind = 'read' and book_ids = %s
        """,
        (parse_owner_id(owner_id), [int(book_id)]),
    ).fetchone()


def list_reading_sessions(
    connection: Connection,
    *,
    owner_id: str | UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Sources this reader has started, most recently read first.

    Counting the side chats rather than listing them: what the library's
    continue band needs is "you asked five questions in this book", and the
    questions themselves belong on the reading surface.
    """

    if limit <= 0:
        raise ValueError("limit must be positive")
    return connection.execute(
        """
        select
            conversations.id,
            conversations.title,
            conversations.book_ids,
            conversations.source_position,
            conversations.updated_at,
            (
                select count(*)
                from conversations as side
                where side.parent_conversation_id = conversations.id
                  and side.owner_id = conversations.owner_id
            ) as question_count
        from conversations
        where conversations.owner_id = %s and conversations.session_kind = 'read'
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
    """Record where the reader is, without disturbing anything else.

    Deliberately does *not* touch `updated_at`. Position is written on every
    page turn, and letting that reorder conversation history would put a book
    the reader is idly scrolling above the one they are actually working in.
    Recency in this application means "was used", and turning a page is not a
    turn.
    """

    return connection.execute(
        f"""
        update conversations
        set source_position = %s
        where id = %s and owner_id = %s and session_kind = 'read'
        returning {CONVERSATION_COLUMNS}
        """,
        (Jsonb(position), conversation_id, parse_owner_id(owner_id)),
    ).fetchone()


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


def set_conversation_state(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    state: dict[str, Any],
) -> None:
    """Replace the resume checkpoint without recording a turn.

    Used to seed a side chat with the answer it was opened over, which happens
    before it has a turn of its own. `updated_at` is deliberately left alone:
    seeding is not use, and a seeded side chat should not jump above a
    conversation someone is actually reading.
    """

    connection.execute(
        "update conversations set state_json = %s where id = %s and owner_id = %s",
        (Jsonb(state), conversation_id, parse_owner_id(owner_id)),
    )


def update_conversation(
    connection: Connection,
    conversation_id: str | UUID,
    *,
    owner_id: str | UUID,
    title: str | None = None,
    retrieval_mode: str | None = None,
    prompt_profile: dict[str, Any] | None = None,
    anchors: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Rename a conversation, change its retrieval mode, or reset its anchors.

    The book selection is deliberately not editable: answers already in the
    conversation were grounded in the current selection, so a different one
    starts a new conversation instead.

    Anchors are replaced wholesale rather than patched. A side chat's reference
    list is small and the reader edits it by adding and removing chips, so the
    client always knows the whole intended set; merging server-side would only
    add a way for two windows to disagree about it.
    """

    assignments = []
    parameters: list[Any] = []
    if title is not None:
        assignments.append("title = %s")
        parameters.append(title)
    if retrieval_mode is not None:
        assignments.append("retrieval_mode = %s")
        parameters.append(retrieval_mode)
    if prompt_profile is not None:
        assignments.append("prompt_profile_json = %s")
        parameters.append(Jsonb(prompt_profile))
    if anchors is not None:
        assignments.append("anchors_json = %s")
        parameters.append(Jsonb(list(anchors)))
    if not assignments:
        return load_conversation(connection, conversation_id, owner_id=owner_id)

    assignments.append("updated_at = now()")
    parameters.extend([conversation_id, parse_owner_id(owner_id)])
    return connection.execute(
        f"""
        update conversations set {", ".join(assignments)}
        where id = %s and owner_id = %s
        returning {CONVERSATION_COLUMNS}
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
