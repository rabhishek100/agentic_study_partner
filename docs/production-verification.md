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
| Clean release | PASS | Managed worktree, release `74c7f9c`; unrelated changes and secrets excluded |
| Five service deployments | PASS | All five Railway deployments SUCCESS; API/web runtime revision confirmed, adaptive voice runtime checked |
| Health, authentication and ownership | PARTIAL | Direct/proxied health and signed-in reads pass; missing auth 401, missing source 404, invalid study input 422; cross-account isolation still pending |
| Ingestion and durable worker | PENDING | Disposable source, queued job, persistence and cleanup |
| Chat, summaries, video/course, sheets, interviews | PARTIAL | Real providers returned persisted outputs for every primary flow; PDF/recovery/voice and quality checks continue |
| Cards, reminders, reader, speech and narration | PENDING | Live smoke; device-only checks recorded separately |
| Failure recovery and accessibility | PENDING | Safe request failures and browser navigation; destructive faults isolated |
| LangSmith every flow | PARTIAL | Chat/summary finished roots and LLM children read back; remaining flows/job correlation pending |
| Grafana metrics/logs/traces | PARTIAL | Production API/worker/supervisor/all voice RSS samples; chat Tempo trace and correlated Loki log read back |
| PostHog | PARTIAL | Real production pageview/ui_action/reading-session API events, normalized routes; streamed completion/privacy/opt-out checks pending |
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

Live narration subsequently produced speech and a final synthetic-microphone
transcript, but its flush RPC returned `1502 Response timeout`. Both narration
and adaptive workers propagated the expected five-second drain timeout when the
provider remained open after final text. Two regression cases reproduced it.
The fix acknowledges flush after bounded cancellation, retaining delivered text.
Targeted voice/observability checks now pass 45 tests and six subtests. Redeploy
and real transport recheck are required before closing this finding.

The hosted video worker intentionally excludes external acquisition, but this
also stranded completed file uploads at `acquire_source`. A regression reproduced
the queued upload alongside an older YouTube job. Metadata-capable workers now
also claim completed primary uploads, preserving external-download separation
and the acquisition checkpoint. All 45 targeted video tests and three subtests
passed against an isolated database. Production pipeline recheck is pending.

That upload subsequently reached `ready` in production; recorded ingestion cost
was $0.000593. Real narration and active adaptive interview rooms now play
non-silent audio, transcribe a synthetic microphone and acknowledge flush.
An expired room-token reconnect returned 401; refreshing the ten-minute token
through the normal authenticated endpoint restored the connection.

The disposable OCR scan preserved all four pages, but numbered/renamed reviewed
headings failed to open their chapter on the declared page. The last chapter
was empty and its automatic cards failed with `source_unavailable`. Two new
regressions reproduced the assignment defects; a third preserves legitimate
continuation text before a matched heading. Numeric prefixes now normalize and
unlocated headings fall back to the confirmed page boundary. All 75 targeted
transcription/parser/OCR/outline tests passed. Deployment and a new scan check
are pending; existing sources are not rewritten automatically.

### Deployment evidence

| Service | Deployment ID |
|---|---|
| API/worker | `dc9ae14f-0acc-4dde-b677-bc98c7cc7091` |
| Web | `2061dd81-06d2-4752-ab88-d5108481b67c` |
| Adaptive voice | `719ff148-44bc-4d3a-95f5-da301791f709` |
| Ideal voice | `fea01e12-2789-41c0-88a6-3188cc50fdec` |
| Narration voice | `b09b114e-d62d-4628-abfd-a2c56f0eb5c2` |

### First live smoke checkpoint

- Book chat: 11.8 seconds, five evidence items and two citations.
- Complete chapter summary: 28.1 seconds, hierarchy-summary route, 48 evidence
  items and 67 citations. These counts prove returned evidence, not correctness.
- Lecture/course study: 13.4 / 4.9 seconds, persisted cited answers.
- Adaptive interview: create/start, pause/resume, answer/finish/report succeeded.
- Ideal chapter interview: generated and reopened successfully, 267.9 seconds.
- Revision sheet: queued, worker composing, then ready; download/reopen pending.
- Synthetic book/paper PDFs: uploaded to the production source store and queued.
- Provider usage is delayed: first readbacks reported zero, later $0.071954.
  Recheck receipts and final usage before reporting a final total. Voice uses
  separate usage estimates. No manual quality rating has been claimed.
- Correlated chat trace: LangSmith `01a0fd8a-f8c8-7152-9bc4-c895e19a66f9`,
  Tempo `6ca0aada30aa73cfa0e3a6614f1bb10d`; matching production Loki log present.
- First course-create probe omitted its required idempotency header and returned
  the expected 400 contract error. Corrected request returned 201; this was a
  probe-input correction, not an application defect.
