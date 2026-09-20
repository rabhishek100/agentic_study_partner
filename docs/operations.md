# Development and operations

## Local stack

Required tools: Docker, Node.js 24, `npm`, and `uv`.

```bash
cp .env.example .env
# Set OPENROUTER_API_KEY
scripts/local.sh setup
scripts/local.sh up
scripts/local.sh doctor
```

Services:

| Service | Local address | Role |
|---|---|---|
| Web | `http://localhost:3000` | Next.js UI |
| API | `http://localhost:8000` | FastAPI and OpenAPI `/docs` |
| Supabase | `http://127.0.0.1:54321` | Local Auth, Storage, REST |
| Mail inbox | `http://127.0.0.1:54324` | Local auth email capture |
| Postgres | `127.0.0.1:54322` | Migrated application database |

Useful commands:

```bash
scripts/local.sh logs app
scripts/local.sh logs web
scripts/local.sh down
scripts/local.sh reset       # destructive: rebuilds only local Supabase data
```

`scripts/local_postgres.sh` is a schema-only fallback when Supabase cannot run.
It supports database tests but not Auth or Storage-backed ingestion.

## Configuration

`.env.example` is the complete annotated reference. The minimum model-backed
local setup is:

```text
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
AUTH_SUPABASE_URL=http://127.0.0.1:54321
OPENROUTER_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=agentic-study-partner
```

The setup script fills local Auth/Storage credentials automatically. Do not put
database URLs, service-role keys, OpenRouter keys, or LiveKit secrets in
`NEXT_PUBLIC_*` variables. Browser-visible demo credentials must belong only to
a disposable, restricted account.

Important optional groups:

- `OPENROUTER_*`: generation, control, embedding, reranking, OCR, video, and
  speech models;
- `SOURCE_*`, `BOOK_IMAGE_*`, `VIDEO_*`: filesystem or S3-compatible object
  storage;
- `LIVEKIT_*`: optional interview and narration voice transport;
- `INGESTION_*`: upload, queue, lease, cleanup, and page limits;
- `SUMMARY_*`, `REVISION_*`: explicit context/output budgets.

Install optional LiveKit dependencies with `uv sync --extra voice`.

## Processes

```bash
# API only
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000

# All durable queues
uv run python -m worker.main

# Local combined supervisor used by scripts/local.sh
uv run python -m scripts.serve
```

The worker polls book ingestion, video ingestion, deck, and revision queues. It
also reconciles initial decks/review reminders and performs guarded retention
cleanup. `SIGTERM` stops new claims and lets the current stage reach a safe
boundary.

## Ingestion operations

Book uploads are reserved with an `Idempotency-Key`, uploaded directly to
private storage, verified, then queued. A digital book with an unsafe outline
pauses at `needs_toc_review`; confirming the proposed hierarchy resumes the same
job. Failed jobs can retry only when classified retryable.

Video jobs run these versioned stages:

```text
acquire source → media metadata → transcript → linked resources → frame selection
→ OCR → visual analysis → spatial regions → evidence index → embeddings
→ quality gates → publish
```

Caption coverage below 90% can trigger the paid audio fallback when the course
or request permits it. Every stage stores a dependency hash so unchanged work
is reused safely.

## Database and storage

Apply schema changes by adding an ordered file under `supabase/migrations/`.
Never edit a deployed migration. Local setup and CI apply the entire chain.

Source backends can be Supabase Storage, filesystem, or S3-compatible R2.
Rows record which backend owns each object, allowing a staged migration. Book
figures and video media must use separate buckets because their retention
sweeps have different ownership ledgers.

Before enabling cleanup against existing object storage, run with the relevant
dry-run flag and inspect structured logs. Orphan-fraction guards intentionally
stop deletion when the database cannot account for the bucket.

## Production shape

Railway production uses a `web` service, an `api` service that supervises both
FastAPI and the background worker, and optional voice-worker services. The
standalone Railway `worker` service is vestigial and should not be deployed.
The API and worker share a container because a Railway volume can mount to only
one service. Deploy through `scripts/deploy.sh`, which records the Git revision
and uses the correct upload root for each image. A sound deployment order is:

1. back up the database and object ledgers;
2. apply migrations through the direct migration connection;
3. deploy the combined API/worker service from the release revision;
4. deploy the web service from the same revision;
5. check `/api/health` and `/api/health/queue`;
6. run one authenticated read and one queued-job smoke test.

The repository does not include infrastructure-as-code for provisioning
Railway, Supabase, R2, OpenRouter, LangSmith, or LiveKit accounts.

## CI

`.github/workflows/ci.yml` runs:

- locked Python dependency installation;
- optional LiveKit tests;
- migrated local Supabase plus the backend suite;
- frontend dependency audit, type-check, tests, and production build;
- production Docker build and dependency-boundary checks;
- a real generated-PDF parse inside the image.

## Recovery rules

- Expired worker leases return jobs to the queue.
- Book and video stages resume from committed canonical checkpoints.
- Derived chunks and embeddings can be rebuilt from canonical records.
- Source/media restoration scripts verify hashes before changing database
  references.
- Never point clone, cleanup, or migration scripts at an environment without
  first checking their `--help`, target URL, and dry-run behavior.
