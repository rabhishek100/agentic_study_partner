# Operational observability and product analytics

Use **Grafana Cloud Free + OpenTelemetry** for application health and **PostHog
Cloud Free** for product usage. LangSmith continues to own AI traces, prompts,
retrieved evidence, tokens and cost. The five-flow quality evaluation/review UI
is a separate [workstream](evaluation-metrics-plan.md).

This setup adds no monitoring server or collector container. Python sends three
signals through one OTLP/HTTP endpoint. JSON logs also work locally without a
hosted account. Both hosted integrations are disabled until configured.

## Free limits and the simplicity tradeoff

Checked 2026-10-01 against official sources:

| Tool | Use here | Free allowance |
| --- | --- | --- |
| [Grafana Cloud Free](https://grafana.com/pricing/) | Loki logs, Tempo traces, Prometheus metrics | 10,000 active metric series; 50 GB logs/month; 50 GB traces/month; 14-day retention; 3 active Grafana users |
| [PostHog Cloud](https://posthog.com/) | Usage, paths, funnels and retention | First 1 million product analytics events/month |

Choose the actual **Free** plan, leave payment details unset, and do not upgrade
to paid usage. PostHog also offers [per-product billing limits](https://posthog.com/docs/billing/estimating-usage-costs).
Free allowances can change; check each account's usage page. Existing LangSmith
and model usage have their own budgets. This integration makes no model calls.

Grafana documents [direct SDK export](https://grafana.com/docs/grafana-cloud/send-data/otlp/send-data-otlp/)
as the quickstart architecture for development/testing or when a collector isn't
practical. It fits this low-traffic demo. SDK queues are bounded, batched and
best effort: outages can drop telemetry. Add Alloy/a collector only if durable
buffering, centralized sampling or fleet-wide configuration becomes necessary.

## Activate Grafana

1. Create a Grafana Cloud **Free** stack. In its OpenTelemetry tile, choose
   Configure and generate a write token for metrics, logs and traces. Copy the
   generated endpoint and headers into local `.env` or backend service secrets.
2. Set these values on **each** API, worker, interview voice and narration voice
   service. Compose already passes `.env` to backend services. The combined
   Railway service passes its environment to both child processes.

```dotenv
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=<generated base endpoint, including /otlp if supplied>
OTEL_EXPORTER_OTLP_HEADERS=<generated encoded Authorization header>
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
APP_ENV=local
```

Do not add `/v1/traces` to the base endpoint: the exporters append the signal
paths. Do not put the token in browser variables or commit `.env`. Leave
`OTEL_SERVICE_NAME` unset when API/worker share an environment: each process
gets a distinct default service name. To rename a service, set it individually.

3. Restart the processes. Ordinary requests generate spans; CPU/RSS metrics
   export every 60 seconds. No automatic provider query is needed to verify
   basic delivery. Import [the dashboard template](grafana-study-partner.json)
   in Grafana and select your Prometheus data source. Metric name translation
   follows the default OTLP-to-Prometheus convention; check Explore if your
   stack uses a different translation setting.
4. Use Explore's Tempo data source with the returned `X-Trace-Id`. Logs carry
   `trace_id`, `span_id` and, when enabled, `langsmith_trace_id`. Look up the AI
   ID in LangSmith. LogQL example:

```logql
{service_name="study-partner-api"} | json | trace_id="<X-Trace-Id>"
```

## What is measured

| Signal | Meaning and limits |
| --- | --- |
| Structured access logs | Route template, status, correlation IDs; no query strings, authorization or request bodies. Emitted after streaming finishes. |
| Operational traces | Shared HTTP/Python workflow/provider boundaries, queue attempts and voice operations. Copied threads keep context; HTTP spans include final SSE delivery. |
| `study.operation.count` | Completed root requests/jobs, split by flow and outcome. Counts attempts, including retries. |
| `study.operation.duration` | Root wall-clock latency in seconds; 5 histogram boundaries for p50/p95 trends. Streaming duration includes the full response. |
| `process.cpu.time` | Cumulative process user + system CPU seconds. Rate gives CPU cores used; multiply by 100 for percent of one core. Can exceed 100% on multicore work. |
| `process.memory.usage` | Current process RSS bytes; API, worker and voice are measured independently. This is neither host/container total memory nor per-eval peak RSS. |
| AI usage and cost | Remain in LangSmith; missing receipts and estimates remain explicitly distinguished. |

Metrics never label series by job/session/book ID, raw path or prompt. Flow
names are bounded by code. Traces/logs carry IDs for investigation. SDKs export
in the background; initialization/export failures preserve application results.
Exception traces capture type/status without raw exception messages. Structured
logging retains diagnostic `error_detail`, redacts common credentials/URL query
strings, and emits stack locations without exception text or locals. Do not
put source text or secrets in new log messages.

Background jobs are separate traces joined by `job_id`, not one span held open
across processes. LiveKit commands likewise have their own roots and session
IDs. This does not instrument each SQL statement, external SDK internals,
host/container metrics, continuous profiles, or the Next.js server. LangSmith
provides LangGraph and LangChain model internals.

Suggested investigations: a latency regression → find the slow operational
stage → inspect its LangSmith retrieval/model trace; CPU/RSS spikes → compare
worker stages and ingestion sizes; retry/error increases → filter by job ID
and check the structured failure code. Measure improvements before changing
retrieval or orchestration.

## Activate PostHog

Create a free PostHog Cloud project, copy its **public project key**, and select
the US or EU ingestion host shown in project settings. Configure the web build:

```dotenv
NEXT_PUBLIC_ANALYTICS_ENABLED=true
NEXT_PUBLIC_POSTHOG_KEY=<public project key>
NEXT_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com
# EU projects use https://eu.i.posthog.com
```

For local Next dev put these in `frontend/.env.local` and restart. Compose
uses root `.env` as build arguments: rebuild the web image after setting them.
Railway web deployments require these variables at build time too.

The single root observer covers **every current page**, navigation, buttons,
links, tabs and menu controls. Generic controls have stable type/slot labels;
important entry buttons have explicit static action names. Browser API writes
across the app report request start, HTTP response/error and safe route. GET
polling/images are excluded. No global fetch monkey patch is used; Supabase and
storage SDK traffic keep their original behavior.

| Event | Interpretation |
| --- | --- |
| `$pageview` | Route visits, with dynamic IDs replaced by `:id` |
| `ui_action` | Control activation, safe navigation target or static action |
| `signed_in`, `signed_out` | Auth lifecycle; identity uses only opaque Supabase user ID |
| `api_action_started` | Mutating API request started |
| `api_action_response` | HTTP response status and time to headers, with trace IDs |
| `api_action_failed` | Network failure/cancellation, without error text |
| `study_answer_completed` / `study_answer_failed` | Parsed final streamed answer / failed or cancelled stream for chat, side chat, video and course study |

An HTTP 200/202 means response/acceptance, **not** finished background generation
or answer quality. Queued summaries/cards/revision sheets use backend operational
traces to verify completion. Binary external storage uploads are not API events.
Slider/input changes, media time updates and every individual clickable source
are not semantic events yet; generic control usage is available. Avoid claiming
full feature conversion from these generic counts.

DOM text capture and session replay are off. Events omit prompts, answers,
source filenames, URLs/queries, form values, email, auth tokens, and automatic
referrer/title/person fields. `before_send` enforces an event/property allowlist;
geolocation enrichment is disabled. PostHog uses localStorage for anonymous
identity, opaque authenticated IDs for retention, and respects Do Not Track.
Ad blockers/DNT/missing keys can prevent collection, so analytics counts are
not an authoritative audit or billing source.

Create one small PostHog dashboard: active users and retention, page usage,
API action failure rate, and a chat funnel (`api_action_started` filtered to
`/api/chat/stream` → `study_answer_completed` filtered to `flow=chat`). For
other actions, begin with API response status and investigate backend completion
in Grafana. Refine events only when a product question needs them.

## Verification

Local wire-format smoke test exports actual protobuf logs, traces and metrics
into an HTTP test receiver and checks shared trace IDs and process CPU/RSS.
Separate tests cover disabled behavior, copied threads, full SSE lifetime,
redaction and exporter failures. Frontend tests check event filtering, disabled
behavior, unconsumed response bodies and error preservation.

```bash
uv run python -m pytest tests/test_operations_telemetry.py tests/test_observability.py tests/test_docker_image_contents.py tests/test_environment_contracts.py tests/test_serve.py -q
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

## Hosted setup status (2026-10-01)

Both official CLIs are authenticated locally: `gcx` uses the `study-partner`
OAuth context, and `posthog-cli` can access project `639444`. Management login
is separate from application ingestion credentials.

- [PostHog usage dashboard](https://us.posthog.com/project/639444/dashboard/2157665)
  is saved with five insights: daily visitors by page, weekly retention, API
  response/transport outcomes, streamed study outcomes by flow, and the chat
  request-to-answer funnel. Reusable definitions are in
  [posthog-study-partner.json](posthog-study-partner.json). The existing starter
  dashboard was preserved. Local root `.env` and `frontend/.env.local` contain
  the public ingestion key and enable analytics. Real local browser page views and UI actions were read back from the hosted
  events table. Their property keys match the allowlist plus PostHog’s own
  `$sent_at`; no email, prompt, title, or referrer fields appeared. The visit
  appears in the hosted visitor chart and retention cohort. Full authenticated
  study-flow conversions have not been exercised in this setup check.
- [Grafana operations dashboard](https://petitecicada3339.grafana.net/d/study-partner-operations)
  defaults to the `production` environment. Its Environment dropdown also offers
  `local`; all five panel queries filter `deployment_environment_name`, preventing
  local test traffic from entering production charts. The source template matches
  the hosted queries. On 2 October, server validation, stored-query readback,
  all five production queries and a rendered dashboard inspection passed.
  It is saved in the **Study Partner** folder using the stack's existing
  `grafanacloud-prom` data source. Resource validation, server dry-run, push,
  saved-object readback, and image rendering succeeded. After enabling export,
  all five panel queries returned data from the temporary
  `study-partner-api-verification` service. Hosted metric names and `job`,
  `instance`, `flow`, and `outcome` labels match the portable template. Healthy
  observed flows display zero failures even without a failed series.
- With explicit user approval, created the `study-partner-telemetry` access
  policy with exactly `metrics:write`, `logs:write`, and `traces:write`, and its
  `study-partner-local-telemetry` token, expiring **2026-12-30**. Saved only its
  encoded OTLP Authorization header in ignored local `.env` (mode `0600`),
  enabled `OTEL_ENABLED`, and removed the temporary secret file. No read or
  management permissions were granted to this application token.
- Real application ASGI requests to `/openapi.json` (200) and `/api/books`
  without authentication (401) exported successfully. Loki logs and Tempo
  traces were read back with matching HTTP correlation IDs; Prometheus
  received operation counters/duration histograms and CPU/RSS metrics across
  multiple samples. This verifies delivery, not AI quality or representative
  application performance. LangSmith was disabled for this isolated check.
  Already-running API, worker and voice processes still need a restart to
  load the new `.env`; they were not interrupted during this setup.

Live setup caught and fixed a privacy-filter regression: PostHog requires its
public project token in event properties. The filter now restores only the
configured token, preserves opaque event identity, and removes SDK-added
top-level `$set`, `$set_once`, and `$unset` profile enrichment. The existing
analytics regression test covers this transport/privacy boundary. Six targeted
analytics tests and TypeScript checks passed.

These settings activate local development only. No hosted app deployment,
payment method, billing upgrade, or real model inference was performed by this
setup. Rebuild deployed frontend services with their analytics build variables
when deploying separately.

Local validation on 2026-10-01: production Next build and TypeScript checks
passed; all 753 frontend tests passed (including six new analytics tests).
With operational tracing enabled, 86 workflow tests passed and 164 spans
exported; the cited-document-image test returned the known baseline 404
(previously reproduced on unchanged code). Dedicated telemetry/setup/supervisor
checks cover the three OTLP signals and both tracing systems together.
