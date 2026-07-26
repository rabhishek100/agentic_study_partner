# Deployment: Railway + hosted Supabase

Three Railway services against one hosted Supabase project.

```text
Railway project
  web      Next.js standalone            public
  api      FastAPI                       public
  worker   ingestion worker              no ingress, no sleep

Supabase project (usuulfckhbeypjxwjpfn)
  Auth (ES256 access tokens, JWKS)
  Postgres + pgvector
  private book-sources bucket
```

The API and worker share the repository-root `Dockerfile` and differ only by
start command. The web service builds from `frontend/Dockerfile`.

## Status

The hosted database is migrated and serving: the ingestion schema, book
lifecycle columns, and the private `book-sources` bucket with its three
owner-scoped policies are applied, and the existing book is backfilled as
`ready` with 332/332 compatible embeddings. Canonical row counts were
unchanged by the migration.

All three services are deployed and verified end to end.

## Connection choice

Use the Supabase **session pooler** connection string as `DATABASE_URL` for
both API and worker. The direct connection is IPv6-only, and the transaction
pooler does not support the prepared statements psycopg uses by default. Keep
the direct connection for migrations only (`MIGRATION_DATABASE_URL`, run from
a laptop, never set on a Railway service).

## Service configuration

Config-as-code files live in the repository; point each service at its file
in Railway's service settings (Settings → Config-as-code):

| Service | Root directory | Config file |
|---|---|---|
| api | `/` | `railway.api.json` |
| worker | `/` | `railway.worker.json` |
| web | `/frontend` | `railway.json` |

`railway.api.json` sets the `/api/health` health check;
`railway.worker.json` sets `restartPolicyType: ALWAYS` and no health check,
because the worker serves no traffic.

**Disable App Sleeping on the worker.** A sleeping worker stops polling
Postgres, and queued jobs would sit until something else woke the service.

## Variables

Set `PORT=8000` explicitly on the api service so private networking has a
deterministic target.

### api

| Variable | Value |
|---|---|
| `PORT` | `8000` |
| `DATABASE_URL` | Supabase session pooler URL |
| `SUPABASE_URL` | `https://usuulfckhbeypjxwjpfn.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | from Supabase → Settings → API |
| `CORS_ALLOWED_ORIGINS` | the web service's public URL |
| `OPENROUTER_API_KEY` | your key |
| `OPENROUTER_GENERATION_MODEL`, `OPENROUTER_CONTROL_MODEL`, `OPENROUTER_JUDGE_MODEL`, `OPENROUTER_EMBEDDING_MODEL`, `OPENROUTER_RERANKER_MODEL` | copy from `.env` |
| `SUMMARY_CONTEXT_WINDOW_TOKENS`, `SUMMARY_MAX_OUTPUT_TOKENS`, `SUMMARY_SAFETY_MARGIN_TOKENS` | copy from `.env` |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | optional tracing |

### worker

Everything the api has except `PORT` and `CORS_ALLOWED_ORIGINS`, plus:

| Variable | Value |
|---|---|
| `INGESTION_MAX_PAGES` | `1000` |
| `INGESTION_MAX_SOURCE_BYTES` | `52428800` |
| `INGESTION_MAX_QUEUED_JOBS_PER_OWNER` | `3` |
| `INGESTION_LEASE_SECONDS` | `300` |
| `INGESTION_WORKER_POLL_SECONDS` | `5` |
| `INGESTION_ABANDONED_UPLOAD_HOURS` | `24` |
| `INGESTION_SOURCE_RETENTION_DAYS` | `7` |
| `INGESTION_CLEANUP_INTERVAL_SECONDS` | `3600` |

`DEFAULT_OWNER_ID` is for local CLI and evaluation commands only. Do not set
it on either service; request handlers derive the owner from the verified
token and the worker uses the owner on the claimed job.

### web

| Variable | Value |
|---|---|
| `BACKEND_URL` | `http://api.railway.internal:8000` |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://usuulfckhbeypjxwjpfn.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | the publishable anon key |

Both `NEXT_PUBLIC_*` values are inlined into the browser bundle at build
time and are declared as `ARG` in `frontend/Dockerfile`, so Railway supplies
them during the build. The service-role key must never appear here.

After the web service gets its public URL, set `CORS_ALLOWED_ORIGINS` on the
api service to that origin and redeploy the api. Then add the same URL to
Supabase → Authentication → URL Configuration (Site URL and redirect URLs).

## Resources and cost

Railway bills actual per-second CPU and memory consumption, not a reserved
allocation, so what an idle worker *holds* is what an idle worker *costs*.
Measured on this codebase:

| Measurement | Value |
|---|---|
| Worker idle RSS, parser imported eagerly | 571 MB |
| Worker idle RSS, parser imported lazily (current) | 86 MB |
| Peak RSS per parse process, steady across batches | 1,232 MB |
| `hi_res` parse rate, serial, on the worker | 4.03 s/page |
| ...across four processes | 1.03 s/page (3.92x) |
| ...across six processes | 0.70 s/page (5.76x) |

The worker therefore defers `parsing.parser` until a document is actually
being parsed (`parsing.version` carries the version constant so provenance
comparisons stay cheap). Idle cost drops roughly seven-fold, which is the
difference between a worker that is affordable to leave running and one that
is not.

Starting points, to tighten from Railway's metrics:

- **worker**: 8 GB limit. Idle consumption is ~86 MB. Parsing runs four
  processes by default and each holds its own copy of the layout model at
  roughly 1.2 GB, so a long book peaks near 5 GB. Four rather than the
  fastest-measured six leaves headroom; `PARSER_WORKERS` tunes it.
- **api**: 512 MB. It never parses.
- **web**: 512 MB.

The always-on worker is preferred over a cron schedule: Railway's minimum
cron interval is 5 minutes, which would add up to 5 minutes of dead time
before an upload starts processing, and the measured idle cost does not
justify that. If cost ever does become a concern, the cron alternative works
with no code changes — set a schedule and use `python -m worker.main --once`,
which claims one job, finishes it, and exits. Railway skips a scheduled run
while the previous one is still active, which matches the one-job-at-a-time
lease model.

The shared image carries Torch and the parser toolchain, so builds are slow
and the image is multiple gigabytes on both api and worker. Splitting a slim
api image is the documented next optimisation; it would cut the api service's
image pull, not its memory, since the parser is no longer imported there
either.

Set a spending alert and a hard budget limit on the Railway project before
sending it any real traffic.

## Deploy with the CLI

```bash
npm install -g @railway/cli
railway login
railway init            # or: railway link  (existing project)
```

Then per service, from the repository root:

```bash
railway up --service api
railway up --service worker
railway up --service web
```

Railway builds the Dockerfile for each service. The web service needs its
root directory set to `frontend` in service settings first.

## Post-deploy verification

```bash
curl -s https://<api-domain>/api/health
curl -s https://<api-domain>/api/health/queue
```

`/api/health` must report both readiness flags true. `/api/health/queue`
should show zero queued and processing jobs, and a null heartbeat until the
worker claims its first job.

Then, in the browser at the web URL: create an account, upload a small
digital PDF with a table of contents, watch the job progress, and ask a
question once the book is ready. Confirm in Railway's worker logs that the
job was claimed and went ready.

Finally, confirm isolation on the deployed stack: sign in as a second
account and check that the first account's book and jobs are invisible.

## Rollback

Railway keeps previous deployments; roll back from the deployment list. The
database migration is additive (new tables, new nullable columns, a
backfill), so an older API image continues to work against the migrated
schema. There is no need to reverse the migration to roll back code.
