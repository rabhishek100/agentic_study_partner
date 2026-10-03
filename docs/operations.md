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

API schemas, Swagger authorization, request examples, and endpoint catalog:
[API reference](api.md).

`scripts/local_postgres.sh` provides schema-only database testing without Auth
or Storage. Logs: `scripts/local.sh logs app` and `scripts/local.sh logs web`.

## Configuration

[.env.example](../.env.example) is the annotated reference.

| Group | Purpose |
|---|---|
| `DATABASE_URL`, `AUTH_*` | Application database and Supabase token issuer |
| `OPENROUTER_*`, `TAVILY_API_KEY` | Model roles and optional web-search provider |
| `LANGSMITH_*` | AI tracing project and key |
| `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_*` | Optional Grafana Cloud logs, traces and process metrics |
| `NEXT_PUBLIC_ANALYTICS_ENABLED`, `NEXT_PUBLIC_POSTHOG_*` | Optional PostHog browser events; web build-time variables |
| `SOURCE_*`, `BOOK_IMAGE_*`, `VIDEO_*` | Storage backends and ingestion settings |
| `INGESTION_*` | Upload, page, lease, queue, and cleanup limits |
| `SUMMARY_*`, `REVISION_*` | Context/output budgets |
| `LIVEKIT_*` and enable flags | Optional speech transport and voices |

Database, service-role, provider, and LiveKit secrets must not use
`NEXT_PUBLIC_*`. Browser demo credentials must identify a restricted disposable
account. Model selection: [design decisions](design-decisions.md#model-defaults).

Backend telemetry settings take effect after a process restart; PostHog
settings require a web rebuild. The Grafana write token expires, so rotate it
in backend secrets before then. Setup, dashboards and queries:
[observability](observability.md).

## Processes

```bash
# API only
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
# Durable queues, card reconciliation, reminders, retention
uv run python -m worker.main
# Combined local supervisor
uv run python -m scripts.serve
```

Daily reminders run in a separate thread of the long-running worker, with a
60-second interval and Postgres-backed preferences/events. Keep the worker
running even when no ingestion jobs are pending. Scheduling and browser
delivery: [daily notifications](flows.md#daily-notifications).

Optional voice workers require `uv sync --extra voice`:

```bash
uv run --extra voice python -m interviews.voice_worker dev
uv run --extra voice python -m interviews.ideal_voice_worker dev
uv run --extra voice python -m narration.voice_worker dev
```

Voice setup and transport recovery: [interview voice](interview-voice.md).

## Persistence and recovery

Queue tables, SQL claims, leases, and worker polling:
[Postgres job queues](architecture.md#postgres-job-queues).
Table inventory and applied-schema inspection: [database schema](database.md).

- Add ordered migrations under `supabase/migrations/`; deployed migrations are
  immutable. Local setup and CI apply the full chain.
- Storage rows record backend ownership. Book figures and video media use
  separate buckets/retention ledgers.
- Expired leases requeue eligible PDF, video, and card work under each queue's
  attempt rules. Interrupted revision-sheet jobs fail for an explicit user
  retry. Checkpoints and dependency hashes govern safe reuse; derived indexes
  can be rebuilt from canonical records.
- Cleanup uses dry-run options, grace periods, and orphan-fraction guards.
  Source restoration verifies hashes before replacing references.
- Book outline review resumes the same job; failed jobs retry only when
  classified retryable. Video replacement versions publish after quality gates.

Publication paths: [ingestion](ingestion.md). Worker code: [worker/main.py](../worker/main.py).

## Testing

Backend tests mutate the database they point at, so run them against a
dedicated, migrated test database with no worker attached, separate from the
application corpus and queues. Create it once in local Postgres, then apply
the schema:

```bash
export TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/study_partner_eval_test
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -c 'create database study_partner_eval_test'
uv run --frozen --extra voice python -m scripts.bootstrap_postgres --url-env TEST_DATABASE_URL
uv run --frozen --extra voice python -m playwright install chromium
uv run --frozen --extra voice python -m pytest tests -q -ra
```

[tests/conftest.py](../tests/conftest.py) points `DATABASE_URL` at
`TEST_DATABASE_URL`, forces filesystem media backends, blanks
`OPENROUTER_API_KEY`, disables hosted telemetry and rejects non-loopback
Supabase endpoints before
application imports load `.env`. Queue tests skip, rather than claim foreign
work, when the database already holds live jobs. Storage integration tests
also need a loopback `SUPABASE_URL` and its local development service-role key;
without them they skip. Inspect skip reasons with `-ra` instead of comparing
counts.

```bash
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run lint:tokens
npm --prefix frontend run build
uv run --frozen --extra voice python -m scripts.export_api_reference --check
uv run --frozen --extra voice python -m scripts.export_langgraph_diagrams --check
```

The generator checks confirm that the [endpoint catalog](api.md) and
[workflow diagrams](langgraph.md) match the code. Two slower checks run
outside CI:

| Command | Checks |
|---|---|
| `uv run --frozen --extra voice python -m tests.check_five_flow_journeys` | Chromium → FastAPI → isolated Postgres for chat/summary, revision sheet, lecture, course and interview journeys: reopen, exclusions, pause/resume, PDF bytes, no JS errors or 5xx, keyboard focus, reduced motion, 390px fit |
| `npm --prefix frontend run verify:proxy` | Real Next server proxies a deliberately slow (>30 s) long-running POST with body and authorization intact |

Both use fixture auth, models and speech, so they prove wiring and recovery,
not provider quality, real sign-in or audio devices. Quality evaluation and
hosted telemetry checks are run explicitly with their own budgets:
[evaluation](evaluation.md#reproduce), [observability](observability.md#verification).

While editing, run the tests for the changed behavior and its callers, plus
typecheck/build when an interface contract changes. Keep database and queue
tests sequential; do not use pytest-xdist.

## Deployment and CI

Railway uses `web`, combined `api`/worker, and optional voice services. API and
worker share one service because the media volume cannot mount to multiple
services. The standalone Railway worker definition is vestigial.

[scripts/deploy.sh](../scripts/deploy.sh)
`<api|web|voice|ideal-interview-voice|narration-voice> [environment]` sets
`BUILD_REVISION`/`BUILD_TIME` on one Railway service and uploads it with
`railway up --path-as-root` from the checkout it runs in (`frontend/` for
`web`), so the recorded revision and the uploaded code always match, including
from a separate worktree. Merging to `main` deploys nothing. A release runs: back up
database/object ledgers → apply migrations → deploy `api` → deploy `web` at
the same revision → check `/api/health`, `/api/health/queue`, an authenticated
read and a queued job. Voice services are deployed with the same script when
their code or configuration changes.
Provider-account provisioning is outside the repository.

[CI](../.github/workflows/ci.yml) runs on pushes to `main` and on pull
requests. It checks locked Python dependencies, generated API/graph references,
the full pytest suite against an empty migrated Supabase instance, frontend
audit/typecheck/tests/build, and the production Docker image with a real
digital-PDF parse.

The frontend manifest requires Next.js 16.3.6 or newer within major version 16,
and the lockfile selects 16.3.6. This is the patched release for
[GHSA-vcvr-r3jv-pc5j](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j);
keep the production dependency audit passing when updating the framework.
