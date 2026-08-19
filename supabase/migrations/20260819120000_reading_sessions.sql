-- Reading sessions: a conversation whose subject is the source, not the thread.
--
-- Source-first study inverts what a conversation is *for*. An ask-first
-- conversation is a thread with a book behind it; a reading session is a book
-- with questions in its margins. What that needs from the schema is small,
-- because the questions are already side chats of the session and a side chat
-- is already a conversation: only the session's own identity is new.
--
-- Hence three columns rather than a table. A reading session gets the same
-- turns, the same row-level security, the same resume path, the same deletion
-- semantics and the same nesting in history that every other conversation has.

alter table public.conversations
    add column session_kind text,
    add column source_position jsonb;

comment on column public.conversations.session_kind is
    'Null for an ask-first conversation; ''read'' for a source-first reading session.';
comment on column public.conversations.source_position is
    'Where the reader last was: {"page": 112}. Derived and disposable — it is a convenience for resuming, never a source of truth about anything.';

alter table public.conversations
    add constraint conversations_session_kind_is_known
        check (session_kind is null or session_kind in ('read')),
    -- A reading session is the parent its anchored questions hang off. It can
    -- never itself be one of them.
    add constraint conversations_reading_sessions_are_roots
        check (session_kind is null or parent_conversation_id is null),
    -- "The source the reader has open" is singular by construction. The
    -- grounding ladder's first rung is that one book, and a session scoped to
    -- three of them would have no first rung to speak of.
    add constraint conversations_reading_sessions_have_one_source
        check (
            session_kind is distinct from 'read'
            or coalesce(array_length(book_ids, 1), 0) = 1
        ),
    add constraint conversations_source_position_is_an_object
        check (
            source_position is null
            or jsonb_typeof(source_position) = 'object'
        );

-- One reading session per source per reader. The endpoint resumes rather than
-- creates, and this is what makes that true rather than merely intended: a
-- second visit to the same book must land back in the margins the reader
-- already wrote, not beside them.
create unique index idx_reading_session_per_source
    on public.conversations (owner_id, (book_ids[1]))
    where session_kind = 'read';

-- The library's "continue reading" band asks one question — which sources has
-- this reader started, most recent first — and this is it.
create index idx_reading_sessions_by_recency
    on public.conversations (owner_id, updated_at desc)
    where session_kind = 'read';
