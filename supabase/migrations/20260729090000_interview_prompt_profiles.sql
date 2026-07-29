-- Owner-scoped interview prompt preferences and immutable conversation snapshots.
--
-- The editable layer never replaces application grounding rules. A conversation
-- snapshots the profile it starts with so changing a global preference does not
-- silently reinterpret an existing study history.

create table public.study_preferences (
    owner_id uuid primary key references auth.users(id) on delete cascade,
    prompt_profile_json jsonb not null,
    updated_at timestamptz not null default now()
);

alter table public.conversations
    add column prompt_profile_json jsonb not null default '{}'::jsonb;

alter table public.study_preferences enable row level security;

create policy study_preferences_owner_access
    on public.study_preferences for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

grant select, insert, update, delete
    on public.study_preferences to authenticated;
