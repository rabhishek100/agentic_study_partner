-- Record when the current stage began, so progress can be reported as
-- elapsed-versus-expected without querying the event log on every poll.
alter table public.ingestion_jobs
    add column stage_started_at timestamptz;

-- Existing rows: the best available approximation is when the job last
-- changed, which for a finished job is when it finished.
update public.ingestion_jobs
set stage_started_at = coalesce(started_at, created_at)
where stage is not null;
