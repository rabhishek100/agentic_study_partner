-- Adaptive, source-grounded interview sessions.
--
-- A session is canonical study history: what was asked, what the candidate
-- answered, which hints and screen observations were used, and how it was
-- scored. `state_json` and `metrics_json` are derived resume/report
-- checkpoints. Raw microphone audio and screen captures never enter storage.

create table public.interview_sessions (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    source_kind text not null check (source_kind in ('book', 'video')),
    book_id bigint,
    node_id bigint,
    video_id uuid,
    ingestion_version_id uuid,

    scope_key text not null check (btrim(scope_key) <> ''),
    title text not null check (btrim(title) <> ''),
    source_title text not null check (btrim(source_title) <> ''),

    interview_format text not null check (
        interview_format in ('concept', 'system_design', 'source_led')
    ),
    format_source text not null check (format_source in ('detected', 'override')),
    feedback_mode text not null check (feedback_mode in ('realistic', 'guided')),
    target_level text not null check (target_level in ('entry', 'mid', 'senior')),
    maximum_duration_minutes integer not null check (
        maximum_duration_minutes in (15, 30, 45, 60, 90, 120)
    ),
    estimated_min_minutes integer not null check (estimated_min_minutes > 0),
    estimated_max_minutes integer not null check (
        estimated_max_minutes >= estimated_min_minutes
        and estimated_max_minutes <= 120
    ),

    status text not null default 'ready' check (
        status in ('ready', 'active', 'paused', 'completed', 'abandoned')
    ),
    elapsed_seconds integer not null default 0 check (elapsed_seconds >= 0),
    active_since timestamptz,
    started_at timestamptz,
    completed_at timestamptz,

    state_json jsonb not null default '{}'::jsonb,
    metrics_json jsonb not null default '{}'::jsonb,
    total_cost_usd numeric(12, 6) not null default 0 check (total_cost_usd >= 0),
    generation_model text not null,
    prompt_version text not null,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    unique (id, owner_id),
    foreign key (book_id, owner_id)
        references public.books(id, owner_id) on delete cascade,
    foreign key (node_id, book_id, owner_id)
        references public.nodes(id, book_id, owner_id) on delete cascade,
    foreign key (video_id, owner_id)
        references video.videos(id, owner_id) on delete cascade,

    constraint interview_session_source_matches_kind check (
        (source_kind = 'book'
            and book_id is not null
            and node_id is not null
            and video_id is null
            and ingestion_version_id is null)
        or (source_kind = 'video'
            and video_id is not null
            and book_id is null
            and node_id is null)
    ),
    constraint interview_session_state_is_object
        check (jsonb_typeof(state_json) = 'object'),
    constraint interview_session_metrics_is_object
        check (jsonb_typeof(metrics_json) = 'object'),
    constraint interview_session_clock_matches_status check (
        (status = 'active' and active_since is not null)
        or (status <> 'active' and active_since is null)
    )
);

create index idx_interview_sessions_owner_updated
    on public.interview_sessions(owner_id, updated_at desc);
create index idx_interview_sessions_scope
    on public.interview_sessions(owner_id, scope_key, created_at desc);

create table public.interview_turns (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null,
    session_id uuid not null,
    turn_index integer not null check (turn_index >= 0),
    topic_key text not null check (btrim(topic_key) <> ''),
    question_kind text not null check (
        question_kind in ('primary', 'follow_up', 'clarifying', 'hint', 'synthesis')
    ),
    question_text text not null check (btrim(question_text) <> ''),
    question_json jsonb not null,

    answer_text text,
    transcript_corrected boolean not null default false,
    classification text check (
        classification is null or classification in (
            'source_aligned', 'correct_extension', 'partially_correct',
            'incorrect', 'insufficient'
        )
    ),
    scores_json jsonb,
    evaluation_json jsonb,
    citations_json jsonb not null default '[]'::jsonb,
    web_sources_json jsonb not null default '[]'::jsonb,
    screen_observation_json jsonb,
    hints_used smallint not null default 0 check (hints_used between 0 and 2),
    cost_usd numeric(12, 6) not null default 0 check (cost_usd >= 0),
    answered_at timestamptz,
    created_at timestamptz not null default now(),

    unique (session_id, turn_index),
    foreign key (session_id, owner_id)
        references public.interview_sessions(id, owner_id) on delete cascade,
    constraint interview_question_is_object check (jsonb_typeof(question_json) = 'object'),
    constraint interview_citations_are_array check (jsonb_typeof(citations_json) = 'array'),
    constraint interview_web_sources_are_array check (jsonb_typeof(web_sources_json) = 'array'),
    constraint interview_scores_are_object check (
        scores_json is null or jsonb_typeof(scores_json) = 'object'
    ),
    constraint interview_evaluation_is_object check (
        evaluation_json is null or jsonb_typeof(evaluation_json) = 'object'
    ),
    constraint interview_screen_observation_is_object check (
        screen_observation_json is null
        or jsonb_typeof(screen_observation_json) = 'object'
    ),
    constraint interview_answer_is_settled_together check (
        (answer_text is null and classification is null and answered_at is null)
        or (answer_text is not null and classification is not null and answered_at is not null)
    )
);

create index idx_interview_turns_session
    on public.interview_turns(session_id, turn_index);

do $$
declare
    table_name text;
begin
    foreach table_name in array array['interview_sessions', 'interview_turns']
    loop
        execute format('alter table public.%I enable row level security', table_name);
        execute format(
            'create policy %I on public.%I for all to authenticated '
            'using (owner_id = (select auth.uid())) '
            'with check (owner_id = (select auth.uid()))',
            table_name || '_owner_access', table_name
        );
        execute format(
            'grant select, insert, update, delete on public.%I to authenticated',
            table_name
        );
    end loop;
end $$;
