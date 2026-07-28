-- Vision-model captions for canonical figures.
--
-- Captions are *derived*, not canonical: they are produced from the image
-- bytes by a named model and are always rebuildable, so they live beside
-- `image_blocks` rather than inside it. That keeps the parser's lossless
-- output free of anything a model invented, and lets the whole corpus be
-- re-captioned with a better model by deleting these rows.
--
-- `skipped_reason` records figures that were deliberately not captioned —
-- publisher boilerplate and decorative fragments — so "no caption" is
-- distinguishable from "not yet captioned", and the filter is auditable
-- rather than silent.

create table public.image_captions (
    block_id bigint primary key,
    owner_id uuid not null,
    book_id bigint not null,
    caption text,
    -- sha256 of the base64 payload: the same figure appearing twice is
    -- captioned once, and a re-parse that changes the bytes invalidates it.
    content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    model_name text,
    skipped_reason text check (
        skipped_reason is null
        or skipped_reason in ('boilerplate', 'too_small', 'unsupported', 'failed')
    ),
    generated_at timestamptz not null default now(),
    check (
        (caption is not null and skipped_reason is null)
        or (caption is null and skipped_reason is not null)
    ),
    unique (block_id, book_id, owner_id),
    foreign key (block_id, book_id, owner_id)
        references public.image_blocks(block_id, book_id, owner_id)
        on delete cascade
);

create index idx_image_captions_owner_book
    on public.image_captions (owner_id, book_id);
-- One captioning call per distinct image across the whole library: the same
-- publisher badge appears in several books.
create index idx_image_captions_content_hash
    on public.image_captions (owner_id, content_hash);

alter table public.image_captions enable row level security;

create policy image_captions_owner_access on public.image_captions
    for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

grant select, insert, update, delete on public.image_captions to authenticated;

-- Captioning is a real pipeline stage, so the job lifecycle has to admit it.
-- The checks enumerate their values, which is what makes an impossible status
-- a rejected write rather than a job stuck in a state nothing handles.
alter table public.ingestion_jobs drop constraint ingestion_jobs_status_check;
alter table public.ingestion_jobs add constraint ingestion_jobs_status_check
    check (status in (
        'awaiting_upload', 'queued', 'validating', 'parsing', 'persisting',
        'captioning', 'chunking', 'embedding', 'verifying', 'ready',
        'retry_scheduled', 'failed', 'cancelled',
        -- Reserved for the scanned/TOC-less follow-up workflow.
        'classifying', 'ocr', 'needs_toc_review'
    ));

alter table public.ingestion_jobs drop constraint ingestion_jobs_stage_check;
alter table public.ingestion_jobs add constraint ingestion_jobs_stage_check
    check (stage is null or stage in (
        'verify_upload', 'download_source', 'preflight', 'parse_pages',
        'persist_canonical', 'caption_figures', 'build_chunks',
        'build_embeddings', 'verify_book',
        'classify', 'ocr_pages', 'propose_toc'
    ));
