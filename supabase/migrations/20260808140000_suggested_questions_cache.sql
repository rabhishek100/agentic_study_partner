-- Cache for dynamic suggested questions / starter prompts.

create table if not exists public.suggested_questions_cache (
    owner_id uuid not null,
    scope_type text not null check (scope_type in ('book', 'library', 'video')),
    scope_key text not null,
    questions_json jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (owner_id, scope_type, scope_key),
    constraint suggested_questions_are_array check (jsonb_typeof(questions_json) = 'array')
);

comment on table public.suggested_questions_cache is
    'Cached dynamic starter prompts / suggested questions for book and video chats.';

create index if not exists idx_suggested_questions_updated
    on public.suggested_questions_cache (owner_id, scope_type, updated_at desc);
