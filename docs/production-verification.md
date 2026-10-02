# Production verification — 2 October 2026

Requested deployment and complete live verification. Total provider-spend ceiling:
$5; each evaluation experiment remains capped at $1–$2. Manual review is optional.
Never run pytest against the production database or restore a backup into it.

## Deployment target

- Railway project: `agentic-study-partner`, environment `production`.
- [Application](https://web-production-8529e.up.railway.app).
- [API health](https://api-production-08e6b.up.railway.app/api/health).
- Services: combined API/worker, web, adaptive voice, ideal voice and narration.
- Initial API/web revision: `6c5e5bb`; voice services: `de9cad5`.
- Database audit: PostgreSQL 18.6, 60 application tables, migration head
  `20260914120000`, matching the repository. No migration needed.
- Initial queue: no queued/processing/retry work. Historical job heartbeat is
  not proof of a live idle worker; verify a new disposable job after deployment.

## Resumable checklist

| Step | Status | Evidence / remaining action |
|---|---|---|
| Inventory and migration audit | PASS | Authenticated Railway inventory; current production schema |
| Pre-release logical backup | PASS | 432 MB custom archive; SHA-256 check passed, 62 data-table entries; private local backup |
| Hosted settings | CONFIGURED | Grafana on API/all voice processes, LangSmith production project, PostHog web build; runtime delivery pending |
| Clean release | RUNNING | Managed worktree excludes unrelated local changes and private evaluation artifacts |
| Five service deployments | PENDING | Same committed revision; check Railway status and runtime identity |
| Health, authentication and ownership | PENDING | Real HTTP and browser session |
| Ingestion and durable worker | PENDING | Disposable source, queued job, persistence and cleanup |
| Chat, summaries, video/course, sheets, interviews | PENDING | Real providers, evidence/artifacts and bounded spend |
| Cards, reminders, reader, speech and narration | PENDING | Live smoke; device-only checks recorded separately |
| Failure recovery and accessibility | PENDING | Safe request failures and browser navigation; destructive faults isolated |
| LangSmith every flow | PENDING | Finished trace roots/LLM children and worker job correlation |
| Grafana metrics/logs/traces | PENDING | Actual production samples and correlated trace/log readback |
| PostHog | PENDING | Production page/action events; privacy and opt-out checks |
| Final acceptance record | PENDING | PASS/FAIL/BLOCKED by feature, actual spend and open issues |

Use [the complete checklist](verification-checklist.md) for expected behavior and
the [API catalog](api.md#endpoint-catalog) for routes. Existing local test results
are prerequisites; they do not count as production passes. Keep production test
artifacts private, and commit only sanitized findings and trace identifiers.

## Release findings

Voice telemetry initialized inside two session callbacks, after their traced
root had already started; ideal voice never initialized it. Fixed all three
servers using LiveKit's parent startup event and picklable child setup callback.
This also exports idle parent CPU/RSS. Targeted voice/observability verification:
43 tests and six subtests passed. Real room/audio transport remains pending.
