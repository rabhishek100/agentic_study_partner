-- Successful AI-generated decks for one study scope are cumulative numbered
-- sets.  `version` remains generation-attempt provenance; `set_number` is the
-- stable user-facing sequence and therefore never advances for a failed run.

alter table public.decks
    add column if not exists set_number integer;

update public.decks
set set_number = 1
where status in ('ready', 'partial') and set_number is null;

alter table public.decks
    add constraint decks_readable_set_number_check check (
        (status in ('ready', 'partial') and set_number is not null and set_number > 0)
        or (status not in ('ready', 'partial') and (set_number is null or set_number > 0))
    );

drop index if exists public.idx_decks_current_version;

create unique index idx_decks_readable_set_number
    on public.decks (owner_id, scope_key, set_number)
    where status in ('ready', 'partial');

comment on column public.decks.version is
    'Monotonic generation attempt for a scope, including attempts that fail.';
comment on column public.decks.set_number is
    'Successful user-facing set number. Null until publication; failed attempts do not consume a number.';
comment on table public.decks is
    'Cumulative numbered AI-generated sets, or one replaceable extracted-question deck, for a chapter or lecture.';
