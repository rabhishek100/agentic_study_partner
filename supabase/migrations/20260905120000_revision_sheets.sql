-- Small, immutable generated A4 PDFs live with their validated payload. This
-- avoids introducing another media retention domain for bounded artifacts.
create unique index if not exists nodes_revision_scope_identity on public.nodes(id, book_id, owner_id);

create table public.revision_sheets (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    book_id bigint not null,
    chapter_node_id bigint,
    scope_kind text not null check (scope_kind in ('chapter', 'paper')),
    scope_key text not null,
    version integer not null check (version > 0),
    source_title text not null,
    scope_title text not null,
    source_fingerprint text not null,
    config_key text not null,
    content jsonb not null,
    source_references jsonb not null,
    provenance jsonb not null,
    pdf_bytes bytea not null check (octet_length(pdf_bytes) between 1 and 2000000),
    created_at timestamptz not null default now(),
    unique (id, owner_id),
    unique (owner_id, scope_key, version),
    foreign key (book_id, owner_id) references public.books(id, owner_id) on delete cascade,
    foreign key (chapter_node_id, book_id, owner_id) references public.nodes(id, book_id, owner_id) on delete cascade,
    check ((scope_kind = 'chapter' and chapter_node_id is not null and scope_key = 'book:' || book_id::text || ':chapter:' || chapter_node_id::text)
        or (scope_kind = 'paper' and chapter_node_id is null and scope_key = 'paper:' || book_id::text))
);

create table public.revision_sheet_jobs (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    book_id bigint not null,
    chapter_node_id bigint,
    scope_kind text not null check (scope_kind in ('chapter', 'paper')),
    scope_key text not null,
    source_title text not null,
    scope_title text not null,
    source_fingerprint text not null,
    config_key text not null,
    request_key text not null check (length(request_key) between 1 and 128),
    request_aliases jsonb not null default '{}'::jsonb,
    regenerate boolean not null default false,
    status text not null default 'queued' check (status in ('queued','running','ready','failed','cancelled')),
    stage text not null default 'queued',
    lease_owner text,
    lease_expires_at timestamptz,
    cancellation_requested boolean not null default false,
    error_code text,
    error_detail text,
    sheet_id uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_id, request_key),
    foreign key (book_id, owner_id) references public.books(id, owner_id) on delete cascade,
    foreign key (chapter_node_id, book_id, owner_id) references public.nodes(id, book_id, owner_id) on delete cascade,
    foreign key (sheet_id, owner_id) references public.revision_sheets(id, owner_id),
    check ((scope_kind = 'chapter' and chapter_node_id is not null and scope_key = 'book:' || book_id::text || ':chapter:' || chapter_node_id::text)
        or (scope_kind = 'paper' and chapter_node_id is null and scope_key = 'paper:' || book_id::text))
);
create unique index revision_sheet_live_job on public.revision_sheet_jobs(owner_id, scope_key)
    where status in ('queued','running');
create index revision_sheet_queue on public.revision_sheet_jobs(created_at) where status='queued';
create index revision_sheets_owner_created on public.revision_sheets(owner_id, created_at desc);

alter table public.revision_sheets enable row level security;
alter table public.revision_sheet_jobs enable row level security;
create policy revision_sheets_owner_read on public.revision_sheets for select to authenticated
    using (owner_id = (select auth.uid()));
create policy revision_sheet_jobs_owner_read on public.revision_sheet_jobs for select to authenticated
    using (owner_id = (select auth.uid()));
-- All writes pass server validation; authenticated browser clients cannot
-- forge a ready artifact, alter a lease, or edit an immutable version.
grant select on public.revision_sheets, public.revision_sheet_jobs to authenticated;
grant all on public.revision_sheets, public.revision_sheet_jobs to service_role;
