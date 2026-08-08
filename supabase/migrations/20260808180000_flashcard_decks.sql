-- Flashcard decks: one deck per book chapter or per lecture.
--
-- Everything here except `deck_review_events` and `deck_preferences` is
-- derived data in the sense the rest of this schema means it: a deck is
-- rebuildable from canonical book or video content and is never hand-edited.
-- Regenerating a scope inserts a new `version` rather than mutating cards,
-- because the scheduling rows in `deck_card_reviews` point at card ids and a
-- reader's review history must survive a regeneration.
--
-- `deck_review_events` is the one canonical table in this migration: it is the
-- append-only record of what was actually reviewed and when. `deck_card_reviews`
-- is a derived checkpoint over that log, stored rather than replayed for the
-- same reason `conversations.state_json` is.

create table public.decks (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    source_kind text not null check (source_kind in ('book', 'video')),

    -- Exactly one source is populated; the check below enforces it. A book
    -- deck names the chapter node it covers, a lecture deck names the video.
    book_id bigint,
    node_id bigint,
    video_id uuid,

    -- Stable identity of the studied unit, independent of version:
    -- 'book:<book_id>:node:<node_id>' or 'video:<video_id>'. Two versions of
    -- the same chapter share it; two chapters never do.
    scope_key text not null check (btrim(scope_key) <> ''),
    version integer not null default 1 check (version > 0),

    title text not null check (btrim(title) <> ''),
    -- The book or lecture the scope sits in, so a deck card in a mixed queue
    -- says where it came from without a join.
    source_title text not null default '',

    status text not null default 'generating'
        check (status in ('generating', 'ready', 'partial', 'failed')),

    -- Provenance. A deck whose cards cannot be traced to a model and a prompt
    -- is not reproducible, and this project's whole claim is reproducibility.
    generation_model text,
    prompt_version text,
    -- The published video ingestion version the lecture evidence came from.
    -- Null for a book deck, whose canonical content is not versioned this way.
    ingestion_version_id uuid,

    topic_count integer not null default 0 check (topic_count >= 0),
    card_count integer not null default 0 check (card_count >= 0),
    -- Deterministic generation metrics: coverage, drops, distributions.
    metrics_json jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    unique (id, owner_id),
    unique (owner_id, scope_key, version),

    constraint decks_metrics_is_an_object
        check (jsonb_typeof(metrics_json) = 'object'),
    constraint decks_source_matches_kind check (
        (source_kind = 'book'
            and book_id is not null
            and node_id is not null
            and video_id is null)
        or (source_kind = 'video'
            and video_id is not null
            and book_id is null
            and node_id is null)
    ),

    foreign key (book_id, owner_id)
        references public.books (id, owner_id) on delete cascade
);

comment on table public.decks is
    'One flashcard deck per book chapter or lecture. Derived and rebuildable; regeneration adds a version.';
comment on column public.decks.scope_key is
    'Version-independent identity of the studied unit: book:<id>:node:<id> or video:<uuid>.';
comment on column public.decks.metrics_json is
    'Deterministic generation metrics: coverage, dropped cards, card-type and priority distributions.';

-- Only one deck per scope is the one you study. Older versions stay readable
-- (their cards carry review history) but never appear in the library twice.
create unique index idx_decks_current_version
    on public.decks (owner_id, scope_key)
    where status in ('ready', 'partial');

create index idx_decks_owner_updated
    on public.decks (owner_id, updated_at desc);

-- What the deck was required to cover. This is the coverage contract: it is
-- written from a deterministic inventory of the scope before any model call,
-- so "covered everything" is checkable against a list the model never chose.
create table public.deck_topics (
    owner_id uuid not null,
    deck_id uuid not null,
    topic_key text not null check (btrim(topic_key) <> ''),
    ordinal integer not null check (ordinal >= 0),
    label text not null check (btrim(label) <> ''),
    required boolean not null default true,

    -- Book locator.
    node_id bigint,
    start_page integer,
    end_page integer,
    -- Lecture locator.
    start_ms integer,
    end_ms integer,
    -- The evidence ranks that satisfy this unit, for a lecture deck.
    evidence_ranks integer[] not null default '{}',

    covered boolean not null default false,

    primary key (deck_id, topic_key),
    foreign key (deck_id, owner_id)
        references public.decks (id, owner_id) on delete cascade
);

create index idx_deck_topics_ordinal on public.deck_topics (deck_id, ordinal);

create table public.deck_cards (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null,
    deck_id uuid not null,
    topic_key text not null,
    card_index integer not null check (card_index >= 0),

    card_type text not null
        check (card_type in ('qa', 'concept', 'mcq', 'system_design')),

    front text not null check (btrim(front) <> ''),
    -- Shape depends on card_type: prose plus optional structured sections for
    -- qa/concept, options and rationales for mcq, components/flow/trade-offs
    -- for system_design. One column rather than four tables: the interface
    -- switches on card_type anyway, and a card back is never queried into.
    back_json jsonb not null,

    interview_priority smallint not null default 3
        check (interview_priority between 1 and 5),
    priority_reason text not null default '',
    difficulty text not null default 'intermediate'
        check (difficulty in ('foundational', 'intermediate', 'advanced')),

    -- Resolved citation markers, validated against the deck's scope before
    -- insert. A card whose markers did not resolve is never stored.
    citations_json jsonb not null default '[]'::jsonb,
    -- Book figures or lecture frames shown with the back. Payloads are never
    -- inlined; the interface fetches bytes from the existing image endpoints.
    figures_json jsonb not null default '[]'::jsonb,

    -- The labelled model-knowledge layer. Never mixed into cited prose, never
    -- counted toward coverage or citation metrics.
    interview_angle text,

    created_at timestamptz not null default now(),

    unique (id, owner_id),
    unique (deck_id, card_index),
    foreign key (deck_id, owner_id)
        references public.decks (id, owner_id) on delete cascade,
    foreign key (deck_id, topic_key)
        references public.deck_topics (deck_id, topic_key) on delete cascade,

    constraint deck_cards_back_is_an_object
        check (jsonb_typeof(back_json) = 'object'),
    constraint deck_cards_citations_are_an_array
        check (jsonb_typeof(citations_json) = 'array'),
    constraint deck_cards_figures_are_an_array
        check (jsonb_typeof(figures_json) = 'array'),
    -- Grounding is the point. A card with no resolved citation is not a card.
    constraint deck_cards_are_cited
        check (jsonb_array_length(citations_json) > 0)
);

comment on column public.deck_cards.interview_angle is
    'Optional model-knowledge follow-up, labelled as such. Excluded from citation and coverage metrics.';

create index idx_deck_cards_deck_priority
    on public.deck_cards (deck_id, interview_priority desc, card_index);
create index idx_deck_cards_topic on public.deck_cards (deck_id, topic_key);

-- Scheduling state: one row per card, created when the deck is stored.
create table public.deck_card_reviews (
    card_id uuid primary key,
    owner_id uuid not null,
    deck_id uuid not null,

    state text not null default 'new'
        check (state in ('new', 'learning', 'review', 'relearning')),
    -- Null only while the card is new and has never entered the queue.
    due_at timestamptz,
    interval_days double precision not null default 0
        check (interval_days >= 0),
    ease double precision not null default 2.5 check (ease >= 1.3),
    reps integer not null default 0 check (reps >= 0),
    lapses integer not null default 0 check (lapses >= 0),
    last_reviewed_at timestamptz,
    last_rating smallint check (last_rating between 1 and 4),

    foreign key (card_id, owner_id)
        references public.deck_cards (id, owner_id) on delete cascade,
    foreign key (deck_id, owner_id)
        references public.decks (id, owner_id) on delete cascade
);

-- The daily queue's only hot read: everything due across every deck.
create index idx_deck_card_reviews_due
    on public.deck_card_reviews (owner_id, due_at)
    where due_at is not null;
create index idx_deck_card_reviews_new
    on public.deck_card_reviews (owner_id, deck_id)
    where state = 'new';

-- Canonical: what was actually reviewed. Append-only.
create table public.deck_review_events (
    id bigint generated by default as identity primary key,
    owner_id uuid not null,
    deck_id uuid not null,
    card_id uuid not null,
    rating smallint not null check (rating between 1 and 4),
    prior_state text not null,
    next_due_at timestamptz,
    elapsed_ms integer check (elapsed_ms >= 0),
    -- For an MCQ, whether the selected option was the correct one. Null for
    -- every other card type, where correctness is the reader's own judgement.
    answered_correctly boolean,
    reviewed_at timestamptz not null default now(),

    foreign key (card_id, owner_id)
        references public.deck_cards (id, owner_id) on delete cascade
);

create index idx_deck_review_events_owner_time
    on public.deck_review_events (owner_id, reviewed_at desc);

create table public.deck_preferences (
    owner_id uuid primary key references auth.users(id) on delete cascade,
    -- Ten a day is the number the feature was asked for; both are editable.
    new_cards_per_day integer not null default 10
        check (new_cards_per_day between 0 and 200),
    max_reviews_per_day integer not null default 120
        check (max_reviews_per_day between 1 and 1000),
    updated_at timestamptz not null default now()
);

-- Generation queue. Same shape as the ingestion and video queues: the row is
-- the queue, the lease, the checkpoint, and the progress source of truth.
create table public.deck_jobs (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    deck_id uuid,

    source_kind text not null check (source_kind in ('book', 'video')),
    book_id bigint,
    node_id bigint,
    video_id uuid,
    scope_key text not null check (btrim(scope_key) <> ''),

    status text not null default 'queued'
        check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    stage text not null default 'pending'
        check (stage in ('pending', 'inventory', 'generation', 'repair', 'storing', 'done')),

    topics_total integer not null default 0 check (topics_total >= 0),
    topics_done integer not null default 0 check (topics_done >= 0),

    attempt_count integer not null default 0 check (attempt_count >= 0),
    max_attempts integer not null default 3 check (max_attempts > 0),
    lease_expires_at timestamptz,
    worker_id text,
    available_at timestamptz not null default now(),

    error_code text,
    error_detail text,
    cancellation_requested boolean not null default false,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    unique (id, owner_id),
    constraint deck_jobs_source_matches_kind check (
        (source_kind = 'book'
            and book_id is not null and node_id is not null and video_id is null)
        or (source_kind = 'video'
            and video_id is not null and book_id is null and node_id is null)
    )
);

-- One live job per scope. A second Generate press while one is running must
-- attach to the running job rather than start a duplicate run.
create unique index idx_deck_jobs_one_live_per_scope
    on public.deck_jobs (owner_id, scope_key)
    where status in ('queued', 'running');

create index idx_deck_jobs_claimable
    on public.deck_jobs (status, available_at)
    where status = 'queued';
create index idx_deck_jobs_owner
    on public.deck_jobs (owner_id, created_at desc);

do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'decks',
        'deck_topics',
        'deck_cards',
        'deck_card_reviews',
        'deck_review_events',
        'deck_preferences',
        'deck_jobs'
    ]
    loop
        execute format('alter table public.%I enable row level security', table_name);
        execute format(
            'create policy %I on public.%I for all to authenticated '
            'using (owner_id = (select auth.uid())) '
            'with check (owner_id = (select auth.uid()))',
            table_name || '_owner_access',
            table_name
        );
        execute format(
            'grant select, insert, update, delete on public.%I to authenticated',
            table_name
        );
    end loop;
end $$;

grant usage, select on all sequences in schema public to authenticated;
