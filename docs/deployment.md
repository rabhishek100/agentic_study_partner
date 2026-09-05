# Deployment: Railway Postgres, Cloudflare R2, Supabase Auth

Production's application database is Railway Postgres as of 2026-09-03.
Supabase holds identities, and — until the source-PDF move finishes — the
private `book-sources` bucket. This document describes production internals;
the isolated local/staging contracts and commands are in
[`environments.md`](environments.md).

```text
Railway project
  web       Next.js standalone                    public
  api       FastAPI + ingestion worker, one       public
            container, one attached volume        no sleep
  Postgres  PostgreSQL 18.6 + pgvector 0.8.6      private
            5 GB volume, ~281 MB used
  worker    vestigial: no deployment. Kept from
            the books-only split; deploy nothing
            to it while video needs one volume.

Supabase project (usuulfckhbeypjxwjpfn)
  Auth only: ES256 access tokens, published JWKS
  private book-sources bucket (source PDFs, pending the R2 move)
  its application tables are dead weight; see "Retiring the old database"

Cloudflare R2
  agentic-study-partner-video-media-prod   video media and evidence frames
  agentic-study-partner-book-media-prod    4,342 book figures
  agentic-study-partner-book-sources-prod  created, awaiting a scoped token
```

## Why the database is not on Supabase any more

The Free-plan disk filled on 2026-09-01 and took production down; there are no
managed backups on that plan, so a local clone was the only copy. Railway
Postgres removes the two properties that made that unrecoverable: the volume is
sized independently of a plan's shared allowance, and this project owns its own
backup and restore path (see [Backups](#backups)).

Two things about the target differ from what the migration runbook assumed, and
both are deliberate:

**It is PostgreSQL 18.6, not 17.** Railway's managed template provisions 18,
and 18 carries pgvector 0.8.6 with `halfvec`, which is what the schema needs.
Pinning a 17 image would have meant owning SSL termination, the volume wiring
and upgrades by hand, for a version number. The consequences are handled rather
than ignored: the row copy uses text-format COPY because binary COPY is a wire
format defined per type and shared between two servers, and the backup script
refuses to run with mismatched client tools, because a `pg_dump` 17 against an
18 server fails in a way that reads as a corrupt archive.

**The runtime role is not the superuser.** `app_runtime` can read and write
rows and insert into the identity registry, and nothing else — it is denied
CREATE on `public`. The superuser URL stays with the operator and is not set on
any service.

## Row-level security is off, on purpose

The migrations attach RLS policies comparing `owner_id` to `auth.uid()`, which
reads JWT claims PostgREST used to set on the connection. There is no PostgREST
here: the browser never connects to Postgres, the API does, and it sets no
claims. `auth.uid()` therefore returns NULL and those policies match no rows at
all.

That combination is a trap. `postgres` is superuser and bypasses RLS silently,
so the application worked while it was superuser and would have gone blank the
moment it stopped being one. A least-privilege role plus enforced `auth.uid()`
policies is not a stricter deployment, it is an empty library.

So `ops/postgres/harden_runtime_role.sql` drops the 54 policies and disables
RLS on all 55 application tables, rather than leaving them present and inert
where they would read as a second line of defence that does not exist. Owner
scoping in this deployment is enforced in application SQL — every
request-serving query filters on the verified JWT subject — and that is what
`tests/test_multi_user_isolation.py` asserts. Local Supabase keeps its policies
and keeps testing them, because there they are real.

## Building and loading the database

```bash
# Schema, from the repository's own migrations rather than a restore.
TARGET_DATABASE_URL=... uv run python -m scripts.bootstrap_postgres

# Assert the shape, not just the migration head.
TARGET_DATABASE_URL=... uv run python -m scripts.audit_postgres_target

# Least-privilege runtime role, and take the inert RLS off.
psql "$OPERATOR_URL" -v ON_ERROR_STOP=1 \
    -v runtime_password="$(...)" -v DBNAME=railway \
    -f ops/postgres/harden_runtime_role.sql

# Rows only. Never pg_restore from a Supabase dump; see the script's header.
uv run python -m scripts.copy_database \
    --source-env SOURCE_DATABASE_URL --target-env TARGET_DATABASE_URL
```

The public TCP proxy on the Postgres service exists only for operator
import/restore and should be removed once the rollback window closes. It is
also not reliable for sustained transfers: a `pg_dump` over it failed twice
with `server closed the connection unexpectedly` partway through
`chunk_embeddings` while the server was demonstrably healthy and still
checkpointing. Retrying works; treat a failure as transient and check the
server's own logs before believing the client.

**Nothing here deploys itself.** No Railway service is connected to a GitHub
repository — `railway status --json` reports `source: {image: null, repo: null}`
for every one of them — so merging to `main` deploys nothing at all. Every
deployment is a `railway up` from a laptop, per service, and `main` and
production drift apart the moment either moves without the other. Check which
commit is actually serving with `/api/health`, never by reading the git log.

The Railway project also has a separate `staging` environment. Deploy it only
through `scripts/staging.sh`; its variables point at Supabase project
`xtkbcireogbjjiuruvzp`, its volume is independent, and its LangSmith traces use
`agentic-study-partner-staging`. Never use the production project reference in
staging variables.

The `api` service normally runs *both halves*: `python -m scripts.serve`
supervises uvicorn and the ingestion worker in one container and exits if
either of them does. `VIDEO_WORKER_STAGES` can narrow that worker to a
comma-separated stage allowlist. The first 1080p course excludes
`acquire_source` in Railway because YouTube challenges the datacenter IP; a
checkpointed laptop worker handles only acquisition and uploads directly to
R2. Railway continues every job from `media_metadata` through `publish`.

**Why they currently share a container.** The deployed video path predates the
course feature and writes canonical sources, frames, and diagram crops to a
filesystem media root. A Railway volume mounts to exactly one service, so the
API and worker currently share one container. The course rollout replaces
that canonical media boundary with private Cloudflare R2; Supabase remains
Auth/Postgres and Railway keeps only a disposable read-through cache.

Books alone do not need the volume — their sources live in Supabase Storage —
so `railway.api.json` and `railway.worker.json` remain valid for a books-only
deployment split across two services. Video requires the combined service,
which is why the `worker` service currently has nothing deployed to it.

The `api` and `web` services share nothing but the API URL: `api` builds from
the repository-root `Dockerfile`, `web` from `frontend/Dockerfile`.

## R2 media for the course rollout

Create one private R2 Standard bucket with no public development URL. Give the
API/worker an object read/write token scoped only to that bucket, then set:

| Variable | Value |
|---|---|
| `VIDEO_MEDIA_BACKEND` | `r2` |
| `VIDEO_S3_BUCKET` | private bucket name |
| `VIDEO_S3_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
| `VIDEO_S3_ACCESS_KEY_ID` | bucket-scoped token access-key ID |
| `VIDEO_S3_SECRET_ACCESS_KEY` | bucket-scoped token secret |
| `VIDEO_S3_REGION` | `auto` |
| `VIDEO_MEDIA_CACHE_ROOT` | `/tmp/study-partner-r2-cache` |

Database rows continue to store owner-prefixed relative object keys and the
backend value `s3`. Every downloaded cache miss is checked against the remote
size and SHA-256 metadata before ffmpeg, OpenCV, Tesseract, or the API sees the
file. Cache bytes are not canonical and may disappear on every deploy.

The source video, captions, selected frames, previews, and diagram crops live
in R2. Normal lecture playback stays on YouTube; R2 is the private ingestion
archive and evidence store, not a streaming CDN. The measured 20-lecture
playlist contains 15.25 GiB of 1080p source media and projects about 18.7 GiB
including derived artifacts. At $0.015/GB-month that is about $0.28 per month
or $3.37 for twelve months without relying on the included allowance; the
allowance can only reduce that figure. Confirm current provider prices before
deployment.

The media backend is a deployment-wide setting. Do not switch an existing
filesystem deployment to R2 until its named objects have been copied and
verified; changing the variable alone does not migrate old objects.

The ordinary video cleanup pass also lists R2 with pagination. It builds each
owner's live reference set from canonical rows and checkpoint manifests,
ignores objects inside the 24-hour grace window, and deletes at most 500 old
orphans per pass. Use `VIDEO_CLEANUP_DRY_RUN=1` for the first R2 deployment as
well; bucket listing or deletion failures are logged and retried on the next
pass.

## Legacy filesystem media volume

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

### What reclaims space

The worker runs `video.cleanup.run_video_cleanup` on the same interval as the
book retention pass (`INGESTION_CLEANUP_INTERVAL_SECONDS`). It releases four
things, none of which anything else ever reclaimed:

| Released | When | Window |
|---|---|---|
| The staging copy of an uploaded source | The job reached `ready` and the canonical object is present | immediate |
| A reserved upload that never completed | The job is still `awaiting_upload` | `VIDEO_ABANDONED_UPLOAD_HOURS` (24) |
| A failed or cancelled job's upload | Retry would no longer be attempted | `VIDEO_STAGING_RETENTION_DAYS` (7) |
| Objects no surviving row names | Any file on the volume outside the reference set | `VIDEO_MEDIA_ORPHAN_GRACE_HOURS` (24) |

The first row is the one that matters on an existing volume: the acquisition
stage copies an uploaded file into canonical storage and left the staging copy
in place, so every uploaded lecture has occupied the volume twice. The grace
window on the last row is not tunable downward without care — media is written
before the row that names it commits, and the window is what stops the sweep
racing a running ingest.

One sweep deletes at most 500 orphaned objects and logs what it left, so a
worker pointed at the wrong database prunes a bounded amount visibly rather
than emptying a volume quietly.

**Deploy this once in observe mode.** The worker runs inside the `api` service
(`scripts.serve`), and its first retention pass fires on the first loop
iteration — minutes after the deploy, unattended, against a volume no test
fixture stands in for. Set `VIDEO_CLEANUP_DRY_RUN=1` for that deploy:

```bash
railway variables --set VIDEO_CLEANUP_DRY_RUN=1 --service api
```

Every pass then logs `would release …` / `would delete …` lines and a
`video cleanup pass (DRY RUN, nothing deleted)` summary. Read them, confirm
the only staging object it names is the uploaded lecture's duplicate and that
nothing under `canonical/` is listed that should survive, then remove the
variable and redeploy. A dry run predicts exactly what the real pass does —
there is a test for that — so the second deploy holds no surprises.

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

`DATABASE_URL` on the `api` and `worker` services is Railway's **private**
domain, as `app_runtime`:

```text
postgresql://app_runtime:<password>@postgres.railway.internal:5432/railway
```

Not a Railway reference variable (`${{Postgres.DATABASE_URL}}`), because that
resolves to the superuser. Not the public proxy, because nothing at runtime
should reach the database over the internet. `MIGRATION_DATABASE_URL` must
never be set on a Railway service; migrations run from a laptop against the
operator URL.

The old Supabase advice — use the session pooler, the direct connection is
IPv6-only, the transaction pooler breaks psycopg's prepared statements — now
applies only to reading the legacy database during the rollback window.

## Service configuration

Config-as-code files live in the repository; point each service at its file
in Railway's service settings (Settings → Config-as-code):

| Service | Root directory | Config file |
|---|---|---|
| api | `/` | `railway.app.json` |
| web | `/frontend` | `railway.json` |

**`railway up` uploads a directory chosen by the link, not by your shell.** The
link entry in `~/.railway/config.json` carries a `projectPath`, and that is the
upload root — `cd` somewhere else and it still uploads the linked path. Deploying
from a clean `git worktree` therefore requires either correcting `projectPath`
for the new path or passing the root explicitly:

```bash
railway up "$PWD" --path-as-root --service api --detach
```

Getting this wrong is quiet and expensive: `scripts/deploy.sh` reads the commit
from the shell's directory, so `BUILD_REVISION` records the clean worktree while
the image is built from the dirty repository root. On 2026-09-06 that shipped an
unmigrated feature's worker into production, which then failed its loop every
five seconds against a table that did not exist — with health checks green and
`build_revision` reporting a commit whose code was not what was running.

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
| `VIDEO_MEDIA_BACKEND` | `r2` for the course production target |
| `VIDEO_S3_BUCKET`, `VIDEO_S3_ENDPOINT` | private R2 bucket and S3 endpoint |
| `VIDEO_S3_ACCESS_KEY_ID`, `VIDEO_S3_SECRET_ACCESS_KEY` | bucket-scoped credentials |
| `VIDEO_S3_REGION` | `auto` |
| `VIDEO_MEDIA_CACHE_ROOT` | `/tmp/study-partner-r2-cache` |
| `VIDEO_WORKER_STAGES` | optional comma-separated allowlist; production excludes `acquire_source` while residential acquisition is required |
| `VIDEO_YTDLP_HTTP_CHUNK_SIZE_BYTES` | `5242880` for resilient YouTube range requests |
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
psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -f ops/postgres/disable_inert_rls.sql
```

Apply the migration **before** deploying an API that depends on it, or every
request touching the new tables fails until it lands.

The second command is not optional. Every migration in this repository enables
row-level security and attaches `auth.uid()` policies, because on local Supabase
they are real and tested. Here they are inert, and `app_runtime` is
`nobypassrls` — so a table that arrives with RLS on is not a stricter table,
it is one the runtime cannot see. The new table would read as empty, with no
error anywhere. `disable_inert_rls.sql` is the RLS half of
`harden_runtime_role.sql` on its own; the full script also rotates the
`app_runtime` password, which would cut off every running service.

Grants need no equivalent step: `harden_runtime_role.sql` set default
privileges for the operator role, so a table a later migration creates is
already readable and writable by `app_runtime`.

Check `$MIGRATION_DATABASE_URL` before running either command. A laptop
configured for local development has it pointing at local Supabase, where
`db push` succeeds and production learns nothing.

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
