-- Source eligibility lives on the canonical source. Existing sources default
-- enabled for review but are not automation-eligible, preventing a surprise
-- production backfill when this migration lands.

alter table public.books
    add column if not exists cards_enabled boolean not null default true,
    add column if not exists cards_automation_eligible_at timestamptz;

alter table video.videos
    add column if not exists cards_enabled boolean not null default true,
    add column if not exists cards_automation_eligible_at timestamptz;

alter table public.deck_jobs
    add column if not exists automatic_key text;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'deck_jobs_automatic_key_nonblank'
          and conrelid = 'public.deck_jobs'::regclass
    ) then
        alter table public.deck_jobs
            add constraint deck_jobs_automatic_key_nonblank
            check (automatic_key is null or btrim(automatic_key) <> '');
    end if;
end $$;

create unique index if not exists idx_deck_jobs_automatic_once
    on public.deck_jobs (owner_id, automatic_key)
    where automatic_key is not null;

comment on column public.books.cards_enabled is
    'Whether this source generates initial cards automatically and contributes to the mixed Today queue.';
comment on column video.videos.cards_enabled is
    'Whether this source generates initial cards automatically and contributes to the mixed Today queue.';
comment on column public.books.cards_automation_eligible_at is
    'Set when a post-feature ingestion publishes; null for pre-existing sources until explicitly enabled or reingested.';
comment on column video.videos.cards_automation_eligible_at is
    'Set when a post-feature ingestion publishes; null for pre-existing sources until explicitly enabled or reingested.';
comment on column public.deck_jobs.automatic_key is
    'Stable auto:set1:<scope> idempotency key; a failed automatic job is retried, never recreated as Set 2.';
