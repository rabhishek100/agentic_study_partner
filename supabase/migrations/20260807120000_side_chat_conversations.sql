-- Side chats: small conversations anchored to a passage of another one.
--
-- A side chat is a conversation rather than a new kind of object. It gets the
-- same turns, the same row-level security, the same resume path and the same
-- deletion semantics for free, which is why this migration adds two columns
-- instead of a table.
--
-- `anchors_json` records only what the reader selected: which turn of the
-- parent, and the text they highlighted. The citation markers, nodes and
-- chunks that selection implies are derived at turn time from the parent
-- turn's stored `result_json`, which is canonical. Storing them here would
-- duplicate canonical data and let an anchor drift out of agreement with the
-- turn it points at.

alter table public.conversations
    add column parent_conversation_id uuid,
    add column anchors_json jsonb not null default '[]'::jsonb;

comment on column public.conversations.parent_conversation_id is
    'Null for a root conversation; set for a side chat opened over one.';
comment on column public.conversations.anchors_json is
    'Reader selections: [{anchor_id, parent_turn_index, quoted_text}]. Derived pins are never stored here.';

-- The composite reference guarantees a parent belongs to the same owner, so a
-- side chat can never read across owners even if application code slipped.
alter table public.conversations
    add constraint conversations_parent_owner_fk
        foreign key (parent_conversation_id, owner_id)
        references public.conversations (id, owner_id)
        on delete cascade,
    add constraint conversations_anchors_are_an_array
        check (jsonb_typeof(anchors_json) = 'array'),
    -- An anchor points at a turn of a parent, so it means nothing without one.
    add constraint conversations_root_has_no_anchors
        check (
            parent_conversation_id is not null
            or jsonb_array_length(anchors_json) = 0
        ),
    -- The depth trigger below would accept a self-reference: such a row's
    -- parent has no parent of its own.
    add constraint conversations_is_not_its_own_parent
        check (
            parent_conversation_id is null
            or parent_conversation_id <> id
        );

create index idx_conversations_parent
    on public.conversations (owner_id, parent_conversation_id, updated_at desc)
    where parent_conversation_id is not null;

-- Depth is capped at one. A tree of side chats has no reading order and no
-- sensible history rendering, and the interface deliberately opens a sibling
-- instead. This is enforced here because that is where the rest of this
-- schema's invariants live.
create or replace function public.validate_side_chat_depth()
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
    from public.conversations as parent
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
    -- Reparenting a conversation that already has side chats of its own would
    -- produce the same two-deep chain by the other direction.
    if exists (
        select 1
        from public.conversations as child
        where child.parent_conversation_id = new.id
    ) then
        raise check_violation using message =
            'a conversation with side chats of its own cannot become one';
    end if;
    return new;
end;
$$;

create trigger conversations_validate_side_chat_depth
    before insert or update of parent_conversation_id
    on public.conversations
    for each row execute function public.validate_side_chat_depth();
