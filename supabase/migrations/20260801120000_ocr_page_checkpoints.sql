-- Page-level checkpoints for the OCR stage.
--
-- Transcribing a scanned book is the first ingestion stage that costs real
-- money per unit of work: 778 pages across this corpus at roughly $0.0019 a
-- page. A worker that dies at page 700 and restarts from page 1 pays twice for
-- the same reading, so a completed page is committed the moment it returns
-- rather than held in memory until the stage ends.
--
-- Rows are keyed by job rather than by book because they exist *before* a book
-- does: the transcription is what the outline proposal is derived from, and
-- the job is still sitting in `needs_toc_review` when these rows are written.
-- They cascade with the job.
--
-- Reuse is conditioned on `model_id` and `prompt_hash`, not on page number
-- alone. A retry that runs under a changed model or a changed instruction is
-- producing a different reading, and silently mixing the two would leave a
-- book whose pages were transcribed by two engines with one set of
-- provenance.

create table public.ingestion_ocr_pages (
    job_id uuid not null,
    owner_id uuid not null,
    -- One-based, matching the PDF page numbering used everywhere else.
    page integer not null check (page > 0),
    text text not null,
    provider text not null check (btrim(provider) <> ''),
    model_id text not null check (btrim(model_id) <> ''),
    render_dpi integer not null check (render_dpi > 0),
    prompt_hash text not null default '',
    input_tokens integer not null default 0 check (input_tokens >= 0),
    output_tokens integer not null default 0 check (output_tokens >= 0),
    cost_usd numeric(12, 6) not null default 0 check (cost_usd >= 0),
    -- How far the deterministic cross-check corroborated this reading.
    -- `unassessable` is not a failure: a figure-only page defeats the
    -- reference engine, and absence of evidence is recorded as such rather
    -- than counted against the model.
    fabrication_verdict text not null check (
        fabrication_verdict in ('supported', 'flagged', 'unassessable')
    ),
    fabrication_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (job_id, page),
    foreign key (job_id, owner_id)
        references public.ingestion_jobs(id, owner_id) on delete cascade
);

-- The stage's two reads: which pages of this job are already done, and which
-- of them a reviewer needs to look at.
create index idx_ingestion_ocr_pages_job
    on public.ingestion_ocr_pages (owner_id, job_id, page);
create index idx_ingestion_ocr_pages_flagged
    on public.ingestion_ocr_pages (owner_id, job_id)
    where fabrication_verdict = 'flagged';

alter table public.ingestion_ocr_pages enable row level security;

create policy ingestion_ocr_pages_owner_read on public.ingestion_ocr_pages
    for select to authenticated
    using (owner_id = (select auth.uid()));

grant select on public.ingestion_ocr_pages to authenticated;
