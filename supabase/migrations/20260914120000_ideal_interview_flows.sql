-- Reproducible, listen-only model interviews over a complete book chapter.
-- The transcript is derived from canonical content and can be regenerated.

create table public.ideal_interview_flows (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    book_id bigint not null,
    node_id bigint not null,
    scope_key text not null check (btrim(scope_key) <> ''),
    title text not null check (btrim(title) <> ''),
    source_title text not null check (btrim(source_title) <> ''),
    interview_format text not null check (
        interview_format in ('concept', 'system_design', 'source_led')
    ),
    target_level text not null check (target_level in ('entry', 'mid', 'senior')),
    topic_count integer not null check (topic_count > 0),
    covered_topic_count integer not null check (
        covered_topic_count = topic_count
    ),
    estimated_duration_seconds integer not null check (estimated_duration_seconds > 0),
    transcript_json jsonb not null check (jsonb_typeof(transcript_json) = 'array'),
    generation_model text not null,
    prompt_version text not null,
    total_cost_usd numeric(12, 6) not null default 0 check (total_cost_usd >= 0),
    voice_cost_usd numeric(12, 6) not null default 0 check (voice_cost_usd >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (id, owner_id),
    unique (
        owner_id, scope_key, interview_format, target_level,
        generation_model, prompt_version
    ),
    foreign key (book_id, owner_id)
        references public.books(id, owner_id) on delete cascade,
    foreign key (node_id, book_id, owner_id)
        references public.nodes(id, book_id, owner_id) on delete cascade
);

create index idx_ideal_interview_flows_owner_updated
    on public.ideal_interview_flows(owner_id, updated_at desc);
create index idx_ideal_interview_flows_scope
    on public.ideal_interview_flows(owner_id, scope_key, created_at desc);

alter table public.ideal_interview_flows enable row level security;
create policy ideal_interview_flows_owner_access
    on public.ideal_interview_flows for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));
grant select, insert, update, delete on public.ideal_interview_flows to authenticated;
