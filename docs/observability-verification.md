# Observability verification — updated 2026-10-03

This record separates synthetic transport checks, fixture-backed workflow
checks and real-service acceptance. Setup and code behavior are documented in
[AI tracing](observability.md) and
[operational telemetry](operational-observability.md). The latest quota rejection
means new LangSmith hosted delivery is unavailable; local timings are not a
substitute. Real API/worker/voice, identity and dashboard acceptance is in
[production verification](production-verification.md).

## Original infrastructure verification — 1 October 2026

This validates tracing infrastructure and preserves the distinction between
synthetic plumbing, fixture-backed workflow tests and live application quality.
It is not an evaluation report or a deployed-service acceptance result.

| Check | Result |
|---|---|
| Hosted LangSmith read-back | Five completed synthetic spans with expected Python/provider/thread nesting and shared correlation ID; $0 paid provider spend |
| Tracing regression + container import coverage | 18 tests passed: SDK nesting, inherited experiment context, real book/video/course SSE helpers, HTTP/SSE failures, thread propagation, job correlation, receipts, voice estimates, cancellation, redaction, capture/delivery failure and disabled operation |
| Tracing-enabled workflow group 1 | 132 tests passed; 294 spans created and completed, covering API, book/video conversations, summaries, cards, providers and ingestion workers |
| Tracing-enabled workflow group 2 | 196 tests passed; 306 spans created and completed, covering interviews, revision sheets, OCR, dictation and narration |
| Final provider/streaming regression group | 96 tests passed, including OCR, dictation, hosted-provider clients, video audio and video vision |
| Optional LiveKit transport tests | 19 passed; fixture/mocked transport coverage, no live room |
| Ideal interviews with tracing enabled | Eight pytest tests passed; four exchange spans created and completed |
| Generated documentation | All five LangGraph diagrams and API endpoint catalog current |
| Syntax / patch validation | Compilation and `git diff --check` passed |

Hosted synthetic run:
[LangSmith smoke trace](https://smith.langchain.com/o/6ca75113-2771-470e-9881-ee62b4c7d1ce/projects/p/06e39b78-ccd4-4232-82b5-70f041c3a5d9/r/01a0f70f-4274-75d1-a385-984d661e0eb4).
The reusable command is `python -m scripts.check_langsmith_tracing`.

An initial broad suite ran **1,921 tests: 1,910 passed, ten failed/errored,
one skipped**. All ten failures reproduced in a temporary checkout of unchanged
Git HEAD against the same local services. They are not introduced by tracing:

- Two suggested-question cache permission assertions.
- Four transcript-store tests that expect a globally empty source table
  observed two existing rows.
- One caption-source selection test reached the refused audio fallback.
- One cited resource-page image test returned 404.
- One video upload test could not find the expected local media file.
- One expired-lease attempt-fencing test rejected the resumed worker.

These remain open findings; no unrelated permissions, data or queue behavior was
changed to make the suite green. The broad suite was not repeated after the
final metadata/documentation refinements; the targeted checks above cover those
changes. Host delivery was exercised with synthetic content, not paid model
inference. Actual production/voice-room traces require running those journeys
with tracing configured in each deployed process. No deployment was performed.

Later checkpoints resolved the six remaining environment/cache failures and
passed the isolated backend suite; see [test-suite-audit.md](test-suite-audit.md).
Live five-flow generation now has hosted end-to-end trace readback, tokens,
cost and latency, including saved failures and explicit unmeasured values.
See [evaluation-results.md](evaluation-results.md) for sample counts, paid
spend, measured repairs and deferred human/voice review. Those results extend
the original infrastructure smoke; they do not imply deployed voice coverage.

## Subsequent test-suite audit

The [test-suite audit](test-suite-audit.md) switches CI to pytest so function
and parameterized cases are discovered. Its full run passed 2,029 cases with
six baseline failures and two skips; four transcript tests were repaired to
query only their fixture owners. This supplements rather than replaces the
tracing-specific validation above.

## Hosted Grafana delivery

The approved ingestion token is write-only for metrics, logs and traces and
expires on 2026-12-30. Its encoded authorization is stored in ignored local
`.env`, with operational export enabled. The temporary secret file was removed.

An isolated process imported the real FastAPI application and made 16 ASGI
requests: 15 OpenAPI requests (200) and one unauthenticated books request (401).
It used `study-partner-api-verification`, bypassed startup/model warm-up, and
made no database, model or live voice calls. LangSmith was disabled for this
check. The existing application process was left running unchanged.

- Prometheus returned `study_operation_count_total`,
  `study_operation_duration_seconds_bucket`, `process_cpu_time_seconds_total`
  and `process_memory_usage_bytes` with the expected service/instance labels.
  Multiple exports allowed all five dashboard PromQL expressions to return
  series, including zero failures for the observed successful flow.
- Loki returned JSON request access records with matching `trace_id` and
  `span_id`, safe route/status metadata and no request bodies.
- Tempo returned trace `b9a84933c92cf59531dbe96baccebe37`: `http.GET`,
  `/api/books`, status 401, outcome `failed`, and HTTP 401 error status. Its
  resource identifies the same verification service/instance as the metrics.
- The updated dashboard manifest passed local validation, server dry-run and
  push. Its live view was rendered for visual verification.

These are transport checks, not latency benchmarks or completed study-flow
evaluations. Restart running backend services to activate their exporters.

## Notification isolation — 3 October 2026

The API/combined worker release `8bd52be` separates routine notification GET polls
and periodic reminder reconciliation into `agentic-study-partner-production-operations`.
Full spans/errors remain recorded. Existing parent trees, evaluation projects,
HTTP trace headers and Grafana exports retain their behavior.

Two regressions failed before the change. After repair, 38 targeted tests and
six subtests passed; the full isolated backend suite passed 2,104 tests and 962
subtests, with 38 skips. Hosted readback verified a real notification poll and
two completed reminder roots in operations, and a real grounded AI request in
the main project: 28 connected spans, two physical LLM calls, successful completion.
The deployment/trace identifiers are recorded in
[production acceptance](production-verification.md#follow-up--3-october-notification-trace-noise).
Old polling history is retained; this change routes new traces rather than deleting
historical records or depending on UI filters.
