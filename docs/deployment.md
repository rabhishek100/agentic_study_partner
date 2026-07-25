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

Railway services are not created yet.

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
| `INGESTION_MAX_PAGES` | `400` until batched parsing lands |
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

## Resources

Not yet measured, so start generous and tighten from Railway's metrics:

- **worker**: the `hi_res` parser loads layout and table models and holds a
  whole book's parsed elements in memory. Start at 4 GB and watch peak
  memory across a large ingestion; the spec wants 25–30% headroom over the
  measured peak. A 269-page book took roughly 16 minutes of parsing on a
  laptop, so allow generous request timeouts nowhere — nothing waits on it.
- **api**: small; it never parses. 512 MB–1 GB.
- **web**: small.

The shared image carries Torch and the parser toolchain, so builds are slow
and the image is multiple gigabytes on both api and worker. Splitting a slim
api image is the documented next optimisation.

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
