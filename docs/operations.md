# Operations

## Local setup

Prerequisites: Docker, Node.js 24, `npm`, and `uv`.

```bash
cp .env.example .env
# Set OPENROUTER_API_KEY
scripts/local.sh setup
scripts/local.sh up
scripts/local.sh doctor
```

`setup` starts local Supabase, applies migrations, installs locked dependencies,
and writes local credentials to ignored environment files. `up` starts the web
and combined API/worker. `down` preserves data; `reset` destroys and rebuilds
local Supabase data.

| Service | Address |
|---|---|
| Web | `http://localhost:3000` |
| API / OpenAPI | `http://localhost:8000` / `/docs` |
| Supabase | `http://127.0.0.1:54321` |
| Local auth mail | `http://127.0.0.1:54324` |
| Postgres | `127.0.0.1:54322` |

`scripts/local_postgres.sh` provides schema-only database testing without Auth
or Storage. Logs: `scripts/local.sh logs app` and `scripts/local.sh logs web`.

## Configuration

[.env.example](../.env.example) is the annotated reference.

| Group | Purpose |
|---|---|
| `DATABASE_URL`, `AUTH_*` | Application database and Supabase token issuer |
| `OPENROUTER_*`, `TAVILY_API_KEY` | Model roles and optional web-search provider |
| `LANGSMITH_*` | Graph/model tracing and project |
| `SOURCE_*`, `BOOK_IMAGE_*`, `VIDEO_*` | Storage backends and ingestion settings |
| `INGESTION_*` | Upload, page, lease, queue, and cleanup limits |
| `SUMMARY_*`, `REVISION_*` | Context/output budgets |
| `LIVEKIT_*` and enable flags | Optional speech transport and voices |

Database, service-role, provider, and LiveKit secrets must not use
`NEXT_PUBLIC_*`. Browser demo credentials must identify a restricted disposable
account. Model selection: [design decisions](design-decisions.md#model-defaults).

## Processes

```bash
# API only
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
# Durable queues, card reconciliation, reminders, retention
uv run python -m worker.main
# Combined local supervisor
uv run python -m scripts.serve
```

Optional voice workers require `uv sync --extra voice`:

```bash
uv run --extra voice python -m interviews.voice_worker dev
uv run --extra voice python -m interviews.ideal_voice_worker dev
uv run --extra voice python -m narration.voice_worker dev
```

Voice setup and transport recovery: [interview voice](interview-voice.md).

## Persistence and recovery

- Add ordered migrations under `supabase/migrations/`; deployed migrations are
  immutable. Local setup and CI apply the full chain.
- Storage rows record backend ownership. Book figures and video media use
  separate buckets/retention ledgers.
- Expired leases return work to the queue. Checkpoints and dependency hashes
  govern safe reuse; derived indexes can be rebuilt from canonical records.
- Cleanup uses dry-run options, grace periods, and orphan-fraction guards.
  Source restoration verifies hashes before replacing references.
- Book outline review resumes the same job; failed jobs retry only when
  classified retryable. Video replacement versions publish after quality gates.

Publication paths: [ingestion](ingestion.md). Worker code: [worker/main.py](../worker/main.py).

## Deployment and CI

Railway uses `web`, combined `api`/worker, and optional voice services. API and
worker share one service because the media volume cannot mount to multiple
services. The standalone Railway worker definition is vestigial.

Deployment through [scripts/deploy.sh](../scripts/deploy.sh) records the revision:
backup database/object ledgers → apply migrations → deploy API/worker → deploy
web at the same revision → check `/api/health`, `/api/health/queue`, an
authenticated read, and a queued job. Provider-account provisioning is outside
the repository.

[CI](../.github/workflows/ci.yml) checks locked Python dependencies, optional
voice tests, migrated Supabase/backend tests, frontend audit/typecheck/tests/build,
and the production Docker image with a real PDF parse.
