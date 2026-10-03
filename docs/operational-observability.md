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

The [saved operations dashboard](https://petitecicada3339.grafana.net/d/study-partner-operations)
uses `grafanacloud-prom` in the **Study Partner** folder. Its Environment dropdown
defaults to `production` and offers `local`; all five queries filter
`deployment_environment_name`. Select the environment and a recent time range
before comparing services. Reuse this dashboard rather than creating a duplicate.

4. Use Explore's Tempo data source with the returned `X-Trace-Id`. Logs carry
   `trace_id`, `span_id` and, when enabled, `langsmith_trace_id`. Look up the AI
   ID in LangSmith. LogQL example:

```logql
{deployment_environment_name="production",service_name="study-partner-api"} | json | trace_id="<X-Trace-Id>"
```

Choose **Explore → Loki** for access/failure logs and **Explore → Tempo** for
the trace tree. Authenticated logs and spans carry verified `user_id`; use the
[account filters](observability.md#user-identity) to join an investigation with
LangSmith and PostHog. Anonymous/system records have no account identity.
User IDs are fields, never Loki stream labels or metric labels.

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
host/container metrics, continuous profiles, browser spans, or the Next.js server. LangSmith
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
referrer/title/person fields. `before_send` enforces an event/property allowlist,
preserves the configured public ingestion token and removes SDK-added profile
enrichment (`$set`, `$set_once`, `$unset`);
geolocation enrichment is disabled. PostHog uses localStorage for anonymous
identity, opaque authenticated IDs for retention, and respects Do Not Track.
Ad blockers/DNT/missing keys can prevent collection, so analytics counts are
not an authoritative audit or billing source.

The [saved usage dashboard](https://us.posthog.com/project/639444/dashboard/2157665)
contains page visitors, retention, API outcomes, streamed study outcomes and a
chat funnel (`api_action_started` filtered to `/api/chat/stream` →
`study_answer_completed` filtered to `flow=chat`). Definitions are in
[posthog-study-partner.json](posthog-study-partner.json). For other actions, begin
with API response status and investigate backend completion in Grafana.

In **Activity → Events**, select `ui_action` or `api_action_response` and filter
by Person, route, action and time. **People and groups → person → activity**
shows an account's event journey; product paths/funnels show navigation and
drop-off. The person ID matches verified backend `user_id`. Playable session
recordings are disabled in the application. If a custom Activity query fails,
inspect its query debugger and compare the standard event view before treating
it as an ingestion outage. Refine semantic events only for a concrete product
question.

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

## Hosted management

Use `gcx` for the Grafana stack and `posthog-cli` for the PostHog project.
The recorded Grafana context is `study-partner`; PostHog project ID is `639444`.
Management authentication is separate from application ingestion credentials.
The application Grafana policy permits only `metrics:write`, `logs:write` and
`traces:write`; its recorded credential expires **2026-12-30**. Rotate it in
backend secrets and restart exporters before expiry. Never use management
credentials or encoded OTLP authorization in a browser build.

Both integrations are configured in production. Real Loki/Tempo/metric exports
and PostHog UI/API/stream events were read back; dashboard queries filter the
production environment and identity filters use verified UUIDs. Exact checks,
release history and scope limits are in
[observability verification](observability-verification.md) and
[production verification](production-verification.md). Local SDK timing or a
configured exporter alone does not establish hosted delivery.

Implementation: [OpenTelemetry and JSON logging](../operations_telemetry.py),
[shared workflow boundaries](../observability.py),
[analytics filtering/API outcomes](../frontend/lib/analytics.ts) and
[root page/control observer](../frontend/components/analytics-observer.tsx).
