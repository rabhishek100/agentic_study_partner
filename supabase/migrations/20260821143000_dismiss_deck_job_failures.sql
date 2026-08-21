-- Failed deck jobs are diagnostic history, but they are not permanent alerts.
-- Keep the row for debugging and provenance while letting its owner dismiss it.

alter table public.deck_jobs
    add column if not exists dismissed_at timestamptz;

comment on column public.deck_jobs.dismissed_at is
    'When the owner dismissed this settled job from generation activity. The diagnostic row is retained.';

