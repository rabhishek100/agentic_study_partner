-- Side chats over a lecture conversation.
--
-- The same two columns and the same depth rule as `public.conversations`; see
-- 20260807120000_side_chat_conversations.sql for why anchors record only the
-- reader's selection and why depth is capped at one.
--
-- One thing differs, and it is the reason this is a separate migration rather
-- than a shared one: a video turn pins the published ingestion version that
-- supplied its evidence. A side chat inherits its parent's video, but each of
-- its turns pins whatever version is published when that turn runs — so an
-- anchored evidence unit that a re-ingest replaced is dropped at assembly time
-- and reported, rather than being silently resolved against a different cut of
-- the lecture.

alter table video.conversations
    add column parent_conversation_id uuid,
    add column anchors_json jsonb not null default '[]'::jsonb;

comment on column video.conversations.parent_conversation_id is
    'Null for a root conversation; set for a side chat opened over one.';
comment on column video.conversations.anchors_json is
    'Reader selections: [{anchor_id, parent_turn_index, quoted_text}]. Derived pins are never stored here.';

alter table video.conversations
    -- Composite so a parent always belongs to the same owner and the same
    -- video: a side chat must not be able to reach across lectures.
    add constraint video_conversations_parent_fk
        foreign key (parent_conversation_id, video_id, owner_id)
        references video.conversations (id, video_id, owner_id)
        on delete cascade,
    add constraint video_conversations_anchors_are_an_array
        check (jsonb_typeof(anchors_json) = 'array'),
    add constraint video_conversations_root_has_no_anchors
        check (
            parent_conversation_id is not null
            or jsonb_array_length(anchors_json) = 0
        ),
    add constraint video_conversations_is_not_its_own_parent
        check (
            parent_conversation_id is null
            or parent_conversation_id <> id
        );

create index idx_video_conversations_parent
    on video.conversations (owner_id, parent_conversation_id, updated_at desc)
    where parent_conversation_id is not null;

create or replace function video.validate_side_chat_depth()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    parent_is_side_chat boolean;
begin
    if new.parent_conversation_id is null then
        return new;
    end if;

    select parent.parent_conversation_id is not null
    into parent_is_side_chat
    from video.conversations as parent
    where parent.id = new.parent_conversation_id
      and parent.owner_id = new.owner_id;

    if parent_is_side_chat is null then
        raise check_violation using message =
            'a side chat requires an existing parent conversation with the same owner';
    end if;
    if parent_is_side_chat then
        raise check_violation using message =
            'side chats cannot be nested; anchor to the root conversation instead';
    end if;
    if exists (
        select 1
        from video.conversations as child
        where child.parent_conversation_id = new.id
    ) then
        raise check_violation using message =
            'a conversation with side chats of its own cannot become one';
    end if;
    return new;
end;
$$;

create trigger video_conversations_validate_side_chat_depth
    before insert or update of parent_conversation_id
    on video.conversations
    for each row execute function video.validate_side_chat_depth();
