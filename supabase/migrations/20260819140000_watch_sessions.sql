-- Watch sessions: the lecture counterpart of a reading session.
--
-- Same idea as `20260819120000_reading_sessions.sql`, in the schema that owns
-- lectures: a session whose subject is the recording rather than the thread,
-- with its anchored questions hanging off it as side chats. Two columns, for
-- the same reason there were three there — the questions are already
-- conversations, so only the session's own identity is new.
--
-- Kept as a separate migration in the video schema rather than generalising
-- the public one. The video domain has its own storage, retrieval, API and
-- worker by deliberate boundary; a shared table across the two would be the
-- first thing to cross it.

alter table video.conversations
    add column session_kind text,
    add column source_position jsonb;

comment on column video.conversations.session_kind is
    'Null for an ask-first lecture conversation; ''watch'' for a source-first watching session.';
comment on column video.conversations.source_position is
    'Where the viewer last was: {"timestamp_ms": 724000}. Derived and disposable.';

alter table video.conversations
    add constraint video_conversations_session_kind_is_known
        check (session_kind is null or session_kind in ('watch')),
    -- A watch session is the parent its anchored questions hang off, never one
    -- of them.
    add constraint video_conversations_watch_sessions_are_roots
        check (session_kind is null or parent_conversation_id is null),
    add constraint video_conversations_source_position_is_an_object
        check (
            source_position is null
            or jsonb_typeof(source_position) = 'object'
        );

-- One watch session per lecture per viewer, so a second visit lands back in
-- the marks already made rather than beside them.
create unique index idx_watch_session_per_lecture
    on video.conversations (owner_id, video_id)
    where session_kind = 'watch';

create index idx_watch_sessions_by_recency
    on video.conversations (owner_id, updated_at desc)
    where session_kind = 'watch';

-- Recording a position must not count as using the session.
--
-- `video.conversations` stamps `updated_at` on every update, which is right
-- for everything that was writing to it before this migration. The playhead is
-- different in kind: it reports several times a second while a lecture plays,
-- so a lecture left running would climb to the top of "continue watching" and
-- stay there, above the one actually being studied.
--
-- Its own function rather than a change to `video.set_updated_at`, which a
-- dozen other tables share and none of them have a position to ignore.
--
-- The comparison is of the whole row rather than of a named column, which
-- keeps it honest: an update that touches the position *and* something else is
-- a real change and still counts.
create or replace function video.set_conversation_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if to_jsonb(new) - 'source_position' - 'updated_at'
       = to_jsonb(old) - 'source_position' - 'updated_at' then
        new.updated_at = old.updated_at;
        return new;
    end if;
    new.updated_at = clock_timestamp();
    return new;
end;
$$;

drop trigger if exists video_conversations_set_updated_at on video.conversations;
create trigger video_conversations_set_updated_at
    before update on video.conversations
    for each row execute function video.set_conversation_updated_at();
