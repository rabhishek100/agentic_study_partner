# Ingestion operations runbook

How to observe, diagnose, and repair the PDF ingestion service. Postgres is
the source of truth for every claim in this document: the job table is the
queue, the lease, the checkpoint, and the progress record.

## Health

| Endpoint | Meaning |
|---|---|
| `GET /api/health` | API liveness plus canonical/retrieval schema readiness. Stays healthy while books are processing. |
| `GET /api/health/queue` | Aggregate queue state: queued/processing/retry counts, failures in the last day, oldest queued-job age, freshest worker heartbeat age, expired leases. |

Readings and what they mean:

- **`worker_heartbeat_seconds` rising past ~300 with processing jobs**: the
  worker is stuck or dead. Its lease will expire and the job will be
  reclaimed automatically; investigate the worker logs.
- **`oldest_queued_seconds` growing with a fresh heartbeat**: the worker is
  busy with a long parse. Normal for large books; parsing a ~270-page book
  takes 15–20 minutes on a laptop.
- **`expired_leases` non-zero for more than a poll interval**: reclamation
  runs at the top of each worker loop; a persistent value means no worker is
  polling at all.

## Logs

The worker emits one JSON object per line to stdout, and to `WORKER_LOG_FILE`
when set. Fields include `job_id`, `owner_id`, `stage`, `attempt`,
`elapsed_seconds`, `error_code`, and `exception` with a full traceback on
unexpected failures. Tokens, keys, connection strings, and book text are
never logged.

The append-only `ingestion_job_events` table records every important
transition per job and is the first place to look when a user reports a stuck
or failed upload:

```sql
select created_at, event_type, status, stage, message, metadata_json
from ingestion_job_events
where job_id = '<job-id>'
order by id;
```

## Failure model

`last_error_code` on the job is stable and safe to show. The important
classes:

- **Non-retryable** (`invalid_pdf`, `encrypted_pdf`, `too_many_pages`,
  `missing_table_of_contents`, `unsupported_document_class`,
  `invalid_hierarchy`, quota codes): the document itself is the problem; the
  UI shows the reason without a retry button.
- **Retryable infrastructure** (`storage_unavailable`, `database_unavailable`,
  `provider_rate_limited`, `provider_timeout`, `provider_unavailable`,
  `lease_expired`, `worker_terminated`): retried automatically with capped
  exponential backoff; provider `Retry-After` guidance takes precedence.
- **`unexpected_error`**: an unclassified exception. Bounded automatic
  retries, then `failed` with manual retry allowed. The traceback is in the
  worker log; if it names a recognizable infrastructure failure, extend
  `ingestion.errors.recognize_exception` so it gets a proper code.

A manual retry (`POST /api/ingestions/{id}/retry`, or the UI button) is an
immediately eligible scheduled retry: it resets the attempt budget and
resumes from the recorded stage. Canonical content committed by an earlier
attempt is reused; chunks and embeddings are rebuilt.

## Manual job repair

Rules that keep repair safe:

1. Never edit `status` to a value the state machine could not have reached;
   the claim has a backstop (an illegal stage restarts the pipeline), but
   repairs should not rely on it.
2. To make a job eligible again, prefer exactly this shape — it mirrors what
   `schedule_retry` writes:

```sql
update ingestion_jobs
set status = 'retry_scheduled',
    lease_owner = null, lease_expires_at = null,
    attempt_count = 0, next_attempt_at = now(),
    last_error_code = null, last_error_message = null,
    last_error_retryable = null
where id = '<job-id>';
```

Keep `stage` as recorded to resume there, or set it to null to restart the
whole pipeline. Do not hand-edit `book_id`, and never delete canonical rows
to "reset" a job — delete the book through the owner-scoped cascade instead.

Worked example (2026-07-25): a code defect failed every embedding attempt,
and a design defect then wedged the retried job as `queued` with
`stage = 'build_embeddings'`, which the claim refused. After fixing both
defects, the repair above (with `stage` kept) resumed the job at embedding;
it reused the committed canonical book, rebuilt derived data, and went ready
in under a minute. No re-parse was needed.

## Retention

The worker applies retention between polls
(`INGESTION_CLEANUP_INTERVAL_SECONDS`):

- `awaiting_upload` older than `INGESTION_ABANDONED_UPLOAD_HOURS` → the job
  is cancelled and its reserved object (if any) removed, releasing quota.
- `failed`/`cancelled` completed longer ago than
  `INGESTION_SOURCE_RETENTION_DAYS` → the source object is deleted; the job
  row and its events are kept as history, with `source_deleted_at` recorded
  in provenance. Manual retry stops being possible once the source is gone.
- `ready` jobs keep their sources indefinitely: the original PDF plus the
  recorded parser/chunker/embedding provenance is the rebuild path for all
  derived data.

## Backup and restore

Two independent concerns:

- **Postgres** (canonical content, jobs, derived data): use Supabase's
  point-in-time backups on hosted; locally, `pg_dump` before destructive
  work. Derived tables never need restoring — rebuild them.
- **Storage** (`book-sources`): objects are immutable at
  `{owner}/{job}/original.pdf`. A lost object for a ready book removes the
  rebuild path but not the canonical content.

Documented restore drill: restore the database backup, verify one book with
`restore_book()` equality, rebuild chunks and embeddings for it, and run the
retrieval evaluation before reopening traffic. The full recovery ladder is in
`docs/supabase-migration-plan.md`.

## Testing note

Queue tests claim jobs without an owner filter, exactly like the worker. They
skip themselves when the target database contains live ingestion jobs, so
they can never claim a real user's work. CI always runs them against a fresh
database.

## Deferred

- Slim API image without the parser toolchain (explicit earlier decision to
  defer; the Dockerfile comment marks the split point).
- Page-batched parsing with per-batch checkpoints, which gates the
  1,000-page claim and real mid-parse progress.
- Scanned/TOC-less workflow with reviewed hierarchy confirmation.
