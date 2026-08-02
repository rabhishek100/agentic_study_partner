-- Time a job spent waiting on a person, kept apart from time it spent working.
--
-- Outline review parks a job until a human confirms the hierarchy. That wait
-- is unbounded and is not the pipeline's doing, but `elapsed` was measured as
-- now() - started_at, so a book reviewed the next morning resumed reporting
-- fourteen hours of elapsed time, blew past every stage estimate, and showed
-- "taking longer than expected" for a job that was running normally.
--
-- Accumulated on the way out of review rather than derived on read, because
-- the number of pauses is not recoverable from the row afterwards.
alter table ingestion_jobs
    add column awaiting_input_seconds double precision not null default 0;

-- Existing jobs that went through review carry the inflated figure in their
-- history. The wait is recoverable for them: the review request and its
-- confirmation are both in the event log.
update ingestion_jobs as job
set awaiting_input_seconds = greatest(0, extract(epoch from (confirmed.at - requested.at)))
from (
    select job_id, min(created_at) as at
    from ingestion_job_events
    where event_type = 'outline_review_requested'
    group by job_id
) as requested
join (
    select job_id, min(created_at) as at
    from ingestion_job_events
    where event_type = 'outline_review_confirmed'
    group by job_id
) as confirmed on confirmed.job_id = requested.job_id
where job.id = requested.job_id
  and confirmed.at > requested.at;
