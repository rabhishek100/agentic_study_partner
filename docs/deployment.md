# Deployment: Railway + hosted Supabase

Two Railway services against one hosted Supabase project.

```text
Railway project
  web      Next.js standalone                    public
  api      FastAPI + ingestion worker, one       public
           container, one attached volume        no sleep
  worker   vestigial: no deployment. Kept from
           the books-only split; deploy nothing
           to it while video needs one volume.

Supabase project (usuulfckhbeypjxwjpfn)
  Auth (ES256 access tokens, JWKS)
  Postgres + pgvector
  private book-sources bucket
```

**Nothing here deploys itself.** No Railway service is connected to a GitHub
repository — `railway status --json` reports `source: {image: null, repo: null}`
for every one of them — so merging to `main` deploys nothing at all. Every
deployment is a `railway up` from a laptop, per service, and `main` and
production drift apart the moment either moves without the other. Check which
commit is actually serving with `/api/health`, never by reading the git log.

The `api` service runs *both halves*: `python -m scripts.serve` supervises
uvicorn and the ingestion worker in one container and exits if either of them
does. The name is left over from the books-only split, when the API and the
worker were separate services; it has run the combined container since video
shipped.

**Why they share a container.** Video ingestion writes canonical sources,
frames, and diagram crops to a filesystem media root, and the API reads those
same bytes back to serve frame images and linked PDFs. A Railway volume mounts
to exactly one service, so two services cannot share one media root — an
upload accepted by a separate API would be invisible to the worker. Supabase
Storage is not an alternative here: the free plan caps an object at 50 MB and
the project at 1 GB, while a 1080p lecture is 1–3 GB. One service with one
volume is the arrangement that actually works, and it is the tradeoff
`AGENTS.md` asks for by name.

Books alone do not need the volume — their sources live in Supabase Storage —
so `railway.api.json` and `railway.worker.json` remain valid for a books-only
deployment split across two services. Video requires the combined service,
which is why the `worker` service currently has nothing deployed to it.

The `api` and `web` services share nothing but the API URL: `api` builds from
the repository-root `Dockerfile`, `web` from `frontend/Dockerfile`.

## The media volume

Attach a Railway volume to the `api` service and point the media root at it:

| Setting | Value |
|---|---|
| Mount path | `/var/lib/agentic-study-partner/video-media` |
| `VIDEO_MEDIA_ROOT` | the same path |

Size it for the videos you intend to keep: the canonical copy of a 100-minute
1080p lecture is 1–3 GB, and its frames, previews, and crops add roughly
50–150 MB. Derived objects are rebuildable, so a volume that fills can be
pruned back to canonical sources without losing anything permanently.

Without `VIDEO_MEDIA_ROOT`, the media store falls back to a path inside the
container, which is wiped on every deploy. Video ingestion would then appear
to work and lose its frames on the next redeploy.

## Status

The hosted database is migrated and serving: the ingestion schema, book
lifecycle columns, and the private `book-sources` bucket with its three
owner-scoped policies are applied, and the existing book is backfilled as
`ready` with 332/332 compatible embeddings. Canonical row counts were
unchanged by the migration.

Both halves are deployed and serving. The media volume is attached, and one
102-minute lecture is ingested and answering questions in production — book
ingestion, book question answering, video ingestion, and lecture conversations
have all run end to end against the hosted stack.

Because nothing deploys itself, this section says nothing about what is *in*
production. Ask the service:

```bash
curl -s https://<web-domain>/api/health
```

`build_revision` is the commit the running image was built from, which is the
only trustworthy answer — `main` moving does not move it.

## Connection choice

Use the Supabase **session pooler** connection string as `DATABASE_URL`. The direct connection is IPv6-only, and the transaction
pooler does not support the prepared statements psycopg uses by default. Keep
the direct connection for migrations only (`MIGRATION_DATABASE_URL`, run from
a laptop, never set on a Railway service).

## Service configuration

Config-as-code files live in the repository; point each service at its file
in Railway's service settings (Settings → Config-as-code):

| Service | Root directory | Config file |
|---|---|---|
| api | `/` | `railway.app.json` |
| web | `/frontend` | `railway.json` |

`railway.app.json` — the file name, not a service name — starts
`python -m scripts.serve`, keeps the `/api/health` health check, and restarts
always. `railway.api.json` and
`railway.worker.json` are retained for a books-only two-service split; do not
use them for video, which needs one volume shared by both processes.

**Disable App Sleeping on the `api` service.** A sleeping service stops polling
Postgres, and queued jobs would sit until a request woke it.

**One process failing takes the container down.** The supervisor stops the
other process and exits non-zero, so Railway restarts a known state rather
than leaving a service that answers health checks with a dead queue behind it.

## Variables

Set `PORT=8000` explicitly on the `api` service so private networking has a
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

### api, ingestion half

The same service, plus the variables only the worker reads:

| Variable | Value |
|---|---|
| `VIDEO_MEDIA_ROOT` | the volume mount path |
| `OPENROUTER_VIDEO_VISION_MODEL`, `OPENROUTER_AUDIO_MODEL` | copy from `.env` |
| `OPENROUTER_VIDEO_TEXT_EMBEDDING_MODEL`, `OPENROUTER_VIDEO_IMAGE_EMBEDDING_MODEL` | copy from `.env` |
| `INGESTION_MAX_PAGES` | `1000` |
| `INGESTION_MAX_SOURCE_BYTES` | `52428800` |
| `INGESTION_MAX_QUEUED_JOBS_PER_OWNER` | `3` |
| `INGESTION_LEASE_SECONDS` | `300` |
| `INGESTION_WORKER_POLL_SECONDS` | `5` |
| `INGESTION_ABANDONED_UPLOAD_HOURS` | `24` |
| `INGESTION_SOURCE_RETENTION_DAYS` | `7` |
| `INGESTION_CLEANUP_INTERVAL_SECONDS` | `3600` |

`DEFAULT_OWNER_ID` is for local CLI and evaluation commands only. Do not set
it in production; request handlers derive the owner from the verified token
and the worker uses the owner on the claimed job.

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
`api` service to that origin and redeploy it. Then add the same URL to
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
| Worker CPUs visible vs cgroup quota | 48 visible, **8 allowed** |
| Worker memory limit | 8 GB |
| `hi_res` parse rate, serial, full-page OCR | 5.24 s/page |
| ...across four processes | 0.93 s/page |
| `hi_res` parse rate, serial, block OCR | 4.16 s/page |
| ...across two processes | 0.99 s/page |
| ...across four processes | **0.53 s/page** |
| ...across six processes | 0.41 s/page |
| ...serial, with the thread cap (current) | **1.14 s/page** |

`os.cpu_count()` reports the host's 48 processors, not the 8 the cgroup
allows, and ONNX Runtime sized its thread pool from the former. A serial
parse was 3.9x slower than it needed to be as a result. The parser now caps
its inference pools at the cgroup allowance; `PARSER_INFERENCE_THREADS`
overrides it, and there is no need to set it on Railway. The batched path was
never affected, so book ingestion timings do not change — the fallback paths
do. See `docs/parser-performance.md`.

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

The image carries Torch, the parser toolchain, ffmpeg, and Tesseract, so
builds are slow and the image is multiple gigabytes. Combining the API and
worker removes the second pull of it. Splitting a slim API image is no longer
available as an optimisation while the two halves share a volume.

Set a spending alert and a hard budget limit on the Railway project before
sending it any real traffic.

## Deploy with the CLI

```bash
npm install -g @railway/cli
railway login
railway init            # or: railway link  (existing project)
```

Then per service, from the repository root. **Both, every time a change spans
both** — there is no trigger that will do the other one for you:

```bash
scripts/deploy.sh api
scripts/deploy.sh web
```

The script records the deployed commit as a `BUILD_REVISION` service variable
before uploading, which is what lets `/api/health` and the worker's startup
line say what they are running. Nothing in a `railway up` image knows its own
commit otherwise, and the worker once ran five commits behind for hours with no
way to notice but reading timestamps against a git log. It also carries the
`--path-as-root` that `web` requires, so the wrong image cannot be deployed to
it by habit.

The underlying commands, if the script is not used:

```bash
railway up --service api
railway up ./frontend --path-as-root --service web
```

**`railway up` uploads the linked project root, not your shell's working
directory.** The link lives in `.railway/` at the repository root, so
`cd frontend && railway up --service web` still uploads the whole repository —
Railway then finds the root `Dockerfile` (the Python API image) and deploys
*that* to the web service, which crash-loops on `node server.js`. `--path-as-root`
is what makes `frontend/` the archive root, so `frontend/railway.json` and
`frontend/Dockerfile` are the ones used. A bare `railway up ./frontend`
without the flag treats the path as a filter prefix and fails with
`prefix not found`.

Neither service has a root directory set in Railway (verify with
`railway status --json`); the `api` service is correct only because the
Dockerfile it wants happens to be the one at the repository root.

If a deploy puts the wrong image on a service, the fastest recovery is the
Railway dashboard — Deployments → the last good one → Redeploy. The CLI's
`railway redeploy` only redeploys the *latest* deployment, which is the broken
one, and there is no redeploy-by-id.

## Schema changes

Migrations are applied from a laptop, never from a service. `MIGRATION_DATABASE_URL`
is the direct connection and exists only for this:

```bash
npx supabase@2.109.1 db push --db-url "$MIGRATION_DATABASE_URL"
```

Apply the migration **before** deploying an API that depends on it, or every
request touching the new tables fails until it lands.

## Source PDFs

A ready book's original PDF is kept so the reading pane can open the page an
answer cites. Nothing else in the system can reproduce it: canonical content,
chunks, embeddings, and captions all survive without it, but the viewer cannot.

`delete_orphaned_sources` is the only path that deletes an object without
recording an event, and it decides what is orphaned by reconstructing paths
from two Storage listings. It now also refuses to delete anything a book
references, so a listing that ever returns a truncated page cannot silently
take a live source with it.

**`SUPABASE_URL` in a local `.env` points at the local Supabase.** Overriding
only `DATABASE_URL` to run a script against production therefore sends its
*database* reads to production and its *storage* calls to localhost. Scripts
that touch both must override both, or they will report confidently on a
bucket nobody is using.

To put a lost source back — a byte copy, not a re-ingest:

```bash
uv run python -m scripts.restore_book_source --check sources/books/*.pdf
uv run python -m scripts.restore_book_source sources/books/*.pdf
```

Files are matched to books by SHA-256 against `books.file_hash`, never by
name, so a different edition or a re-exported copy will not match — which is
the point: only the exact bytes that produced a book's pages can be trusted to
make page 78 the page 78 its answers cite.

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
