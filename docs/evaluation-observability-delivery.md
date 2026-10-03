# Evaluation, observability and production delivery

Status recorded on **3 October 2026**. This guide connects the quality evaluation,
AI tracing, operational monitoring, UI analytics, test cleanup and production
fixes. Detailed guides and committed measurements remain the source of truth.

## What is complete

| Area | Delivered | Important limit |
| --- | --- | --- |
| Five-flow evaluations | 54 cases across chat, summaries, video/course study, revision sheets and interviews; source capture, resumable paid runs, cost guards, saved-output review and twenty candidate experiments | Automated judgments, not human-calibrated scores; failed and unavailable cases remain visible |
| LangSmith | HTTP roots, LangGraph nodes, ordinary Python stages, raw provider attempts, streaming, workers and voice; separate projects for notification polling | Monthly unique-trace quota currently blocks new hosted readback |
| Grafana | Structured logs in Loki, operational traces in Tempo, throughput/latency/failure and process CPU/RSS charts | Process metrics, not host totals or per-request peak memory; coarse latency buckets |
| PostHog | Page visits, control actions, auth lifecycle, API outcomes and streamed study outcomes; usage dashboard | Session replay is disabled; generic actions do not establish every feature's completion |
| User identity | Verified auth UUID in logs/traces and the same UUID as PostHog identity | No email in telemetry; anonymous/system operations have no user |
| Tests | Complete pytest discovery, duplicate cleanup, hermetic media/provider settings and isolated database checks | Tests prove contracts; separate evals measure generated quality |
| Production | Earlier full feature-family acceptance, targeted fixes and a later real-account before/after comparison | Device audio, fresh external acquisition, accessibility and load coverage remain bounded |

API/combined worker revision **`d8c9dda`** is deployed. Web and voice retained
their previously accepted builds; the latest evaluation changes did not require
changes to those services. Documentation/report checkpoint **`1207b7c`** records
the completed experiment round. [Production record](production-verification.md).

## Which tool answers which question?

| Question | Open | What to inspect |
| --- | --- | --- |
| Why did an answer omit something or invent a claim? | LangSmith | Input, retrieved evidence, routing, graph/Python stages, prompts, repairs and individual model calls |
| Which API or worker stage failed or was slow? | Grafana Tempo + Loki | Finished spans, status/outcome, structured error fields and matching trace/job IDs |
| Is the app using more CPU or memory? | Grafana dashboard | Process CPU rate and RSS for API, worker, supervisor and voice services |
| What do people use and where do requests fail? | PostHog | Safe page/control events, API outcomes, paths, retention and the chat funnel |
| Is a proposed change actually better? | Evaluation report | Frozen cases, source/settings fingerprints, matched comparisons, repeats, cost and preserved failures |

The integrations use the chosen free hosted tiers, with no new monitoring server
or collector container. Python exports directly over OTLP/HTTP. SDK queues are
bounded and best effort: telemetry outages must not break studying. Free account
allowances, model costs and LangSmith limits are separate; check account usage
before further runs. [Setup and limits](operational-observability.md).

No additional Opik service was introduced: existing LangSmith instrumentation,
the shared runner and the local review viewer cover the chosen trace/evaluation
needs. Adding a second platform would need a demonstrated gap.

Implementation: [AI trace boundaries](../observability.py),
[operational export/logging](../operations_telemetry.py),
[browser analytics](../frontend/lib/analytics.ts),
[root UI observer](../frontend/components/analytics-observer.tsx),
[evaluation suite](../evals/suite.py), [child budget](../evals/budget.py),
[round budget](../evals/round_budget.py) and
[production-window guard](../scripts/production_eval_window.py).

## Find a request across the tools

1. Make a study request and retain its response headers: `X-Trace-Id` for Tempo
   and `X-LangSmith-Trace-Id` for the AI trace. Browser mutating API events also
   carry these IDs when the response supplies them.
2. In Grafana Explore, choose Loki, a recent time range and the production
   environment. Filter by `trace_id`, or by the verified `user_id` below.
3. In Tempo, open the exact trace ID to inspect the request and its Python stages.
4. In LangSmith, select `agentic-study-partner-production`, clear old run-name
   filters, choose **Traces**, and open the matching HTTP root. Expand its tree
   for model calls, retrieval, LangGraph nodes and ordinary Python stages.
5. For queued work, search the stable `job_id`: enqueue and each worker attempt
   are separate roots. Voice operations likewise correlate through session IDs.
   They are not one span held open during queue wait or an entire voice session.

Account-filter examples, using your verified auth UUID:

```logql
{deployment_environment_name="production"} | json | user_id="<account-uuid>"
```

```logql
{deployment_environment_name="production"} | json | trace_id="<X-Trace-Id>"
```

Tempo search: `{ span.user_id = "<account-uuid>" }`. LangSmith:
**Filter → Metadata → user_id**. Identity comes from validated authentication,
persisted job ownership or validated voice bindings, never a caller-supplied
identity header. It is not a metric or Loki stream label. Older data is not
backfilled. [Identity and trace coverage](observability.md#user-identity).

Notification GET polls and periodic reminder checks go to
`agentic-study-partner-production-operations`; AI work stays in the main project.
Existing parent/evaluation contexts keep their hierarchy. Old polling history
remains, and sidebar run-name shortcuts are not a complete flow inventory.

If new LangSmith traces are absent, check the correct project, filters/time range,
process configuration and exporter errors. The last experiment round encountered
a monthly quota rejection. Earlier hosted trees verify delivered nesting; local
SDK intervals and captured outputs do not prove current hosted delivery.

## What the evaluations found

The final comparable saved-output check clears **34/45** cases versus **24/45**
in the earliest preserved evaluation, including one separate provider retry.
The full later manifest completes 53/54 first attempts; the remaining request
passes that retry. Quality is not perfect: 36/49 eligible cases clear every
check with the retry. Five source/policy/capture exceptions stay outside that
denominator, rather than being counted as successes.

| Retained change | Evidence |
| --- | --- |
| Bounded neighboring source context | Definition coverage 2/4 → 4/4 and model-card checklist 1/4 → 4/4, including fresh repeats; broad audit remains unstable |
| Full-source summary repairs and large-sheet context/recovery | More complete outputs and recovery of previously blocked sheets; combined historical summary cases 3/6 → 6/6 and sheets 0/5 → 3/5 |
| Actual-marker citation metadata and source-bound figure validation | Output/database regressions protect source identity and reject stale or foreign bindings; valid locators alone do not prove support |
| Browser reuse during layout search | Controlled median rendering 4.468 → 1.110 seconds, with identical content/geometry; whole-sheet production speed did not show that gain |
| Larger sheet citations | Six paired identical-content renders: approximately 9 → 10 point citations, unchanged pages/figures and $0 extra inference |

The latest twenty candidates compared models, routing/grading, figure reading,
retrieval, summary allocation, layouts, caching and interview instructions.
Only the larger citations were newly adopted. Initial model/prompt gains were
inconsistent on repeats or alternate blinded review, so Luna and the original
grading behavior remain. This does not establish a broad gain over the frozen
start-of-round control. [Full experiment decisions](evaluation-round3-results.md).

External review uses original evidence and all rendered sheet pages, preserves
failed judgments and separates source pixels from generated layout pixels.
Luna is the default reviewer; nine alternate-model blinded reviews checked
disagreements. The local review viewer shows those scores; human labels are
optional, and no human agreement or learning-outcome improvement is claimed.
[Runner and review commands](evaluation.md#reproduce).

## Cost and actual production comparisons

The new round reports **$3.867140599**, plus **$0.099636114** reserved for unknown
earlier receipts: **$3.966776713 committed**, within $5. The preceding round's
$3.904616935 is separate. Failed requests, repeats, reviews and production tests
are included. Parent/child ledgers reserve before dispatch and survive interruption.

Same-account production pairs use matching canonical sources and questions.
Chat took 9.571 → 16.117 seconds, summary 25.688 → 30.165 seconds, and cold sheet
182.520 → 184.892 seconds. Video/course requests were slightly faster in this
single pair. The ideal interview's 85.745 → 0.034 seconds reflects saved-output
reuse, not faster fresh generation. No general production speedup is established.

The complete synthetic adaptive cost sample finished naturally after 18 answers,
44 billed provider requests and nine planned areas, costing **$0.022431575**.
Production comparison windows together used **$0.10556099** under their $0.75
provider key cap. The normal production key was restored and verified.

For 100 chats, five summaries, three sheets, two interviews and twenty video/course
questions, the forecast is **about $0.93/month** with one search per chat, or
**$1.25** with two, including 20% headroom. Voice and arbitrary larger ideal
chapters are separate. This is a forecast, not a monthly application cap or an
actual historical bill. Pre-evaluation production bills/timings were not retained
well enough for an exact comparison. [Forecast](../evaluation/round3_monthly_forecast.json).

## Other production fixes and test changes

- **Long ideal-interview generation:** a ten-minute web proxy timeout and
  scope/settings advisory lock prevent the observed 30-second disconnect and
  duplicate-save race. Concurrent real requests returned the same complete flow;
  the retry made no model calls. Generation remains synchronous, not restart-durable.
- **Voice:** initialize telemetry before sessions; retain delivered transcription
  when bounded drain cancellation occurs. Real synthetic-microphone transports
  passed; actual user-device permissions remain a separate check.
- **Ingestion:** process completed hosted video uploads; normalize reviewed OCR
  headings and preserve fallback page boundaries. Repaired scans and card jobs
  were checked without rewriting the historical failed example.
- **Analytics/monitoring:** preserve the public PostHog ingestion token through
  the privacy filter, remove unwanted SDK profile enrichment, and filter every
  Grafana panel by a production-default environment selector.
- **Tests:** remove the overwritten duplicate test class; discover all pytest
  function/parameterized cases in CI; replace a network smoke with a parser
  fixture; scope owner assertions and use an isolated database/media environment.

Latest selected runtime verification: **2,181 backend tests + 973 subtests passed**,
38 skipped. The last unchanged frontend verification passed 753 tests, types and
build. These are local checks, not a claim of a hosted CI run.
[Test audit](test-suite-audit.md), [production fixes](production-verification.md).

## Current limits and next checks

Broad-answer support, unrelated chat figures, missing course evidence, sheet
content warnings and interview reasoning consistency remain quality priorities.
Three of six final sheets retain native content warnings. Device audio, fresh
external YouTube acquisition, a full accessibility/browser matrix and realistic
load/failure tests remain separate acceptance work. Session replay is disabled:
PostHog shows event journeys, not playable recordings.

Use [the verification checklist](verification-checklist.md) for ordered checks.
Do not rerun paid work to reconstruct status: preserve source/settings/output
hashes, receipt ledgers and the private bundles under ignored `evaluation/runs/`.
Public reports contain scalar results; source content, credentials, account
identity and raw outputs stay private. The [delivery tracker](evaluation-progress.md)
records checkpoints and the resume procedure.
