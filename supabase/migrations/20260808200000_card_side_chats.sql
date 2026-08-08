-- Anchoring a side chat to a passage of a flashcard.
--
-- A side chat is a child conversation whose anchors name a parent *turn*, and
-- whose pinned evidence is derived from that turn's stored `result_json`. A
-- card is not a turn, so highlighting one had nothing to anchor to.
--
-- Rather than teach anchors a second parent shape, a card is recorded as a
-- real turn the first time it is asked about: its front is the question, its
-- back is the answer, and its citations are resolved into the chunk-bearing
-- evidence a turn carries. Everything downstream — pinning, seeding, the
-- streaming endpoint, reopening a closed window — then works untouched.
--
-- These two columns are the only new state: which deck a conversation stands
-- for, and which card a seeded turn came from.

alter table public.conversations
    add column deck_id uuid;

comment on column public.conversations.deck_id is
    'Set on the single conversation that holds a deck''s cards as turns. Null for an ordinary conversation.';

alter table public.conversations
    add constraint conversations_deck_owner_fk
        foreign key (deck_id, owner_id)
        references public.decks (id, owner_id)
        on delete cascade,
    -- A deck conversation is a container for cards, not something a reader
    -- opened over a passage. Being both would make its own turns anchorable
    -- to themselves.
    add constraint conversations_deck_is_not_a_side_chat
        check (deck_id is null or parent_conversation_id is null);

-- One conversation per deck. Two would split a deck's cards across threads
-- and make "the questions I asked about this deck" unanswerable.
create unique index idx_conversations_deck
    on public.conversations (owner_id, deck_id)
    where deck_id is not null;

alter table public.conversation_turns
    add column deck_card_id uuid;

comment on column public.conversation_turns.deck_card_id is
    'Set when a turn was seeded from a flashcard rather than asked by a reader.';

alter table public.conversation_turns
    add constraint conversation_turns_card_owner_fk
        foreign key (deck_card_id, owner_id)
        references public.deck_cards (id, owner_id)
        on delete cascade;

-- A card is recorded once per conversation; the second highlight on the same
-- card reuses the turn the first one wrote.
create unique index idx_conversation_turns_card
    on public.conversation_turns (conversation_id, deck_card_id)
    where deck_card_id is not null;
