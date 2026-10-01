# LangSmith observability

Tracing is a repository-wide capability, independent of which flows have deep
quality evaluations. [observability.py](../observability.py) supplies SDK run
boundaries for ordinary Python work, HTTP requests and direct provider calls.
Existing LangGraph and LangChain tracing supplies graph nodes and model calls.

## Setup and organization

Set these variables in each API, queue worker and optional voice worker process:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-key
LANGSMITH_PROJECT=agentic-study-partner-local
```

Use a distinct project suffix for local, staging and production. The shared
helper respects an inherited LangSmith project, client, tags and evaluation
metadata; it does not force experiment runs into the application's project.
Set `LANGSMITH_TRACING=false` to disable these boundaries and provider captures.

Filter runs by `flow`, `thread_id`, `conversation_id`, `session_id`, `job_id`,
`attempt_count`, `stage`, source IDs or `flow:*` tags. Names identify the real
Python operation, such as `video.pipeline._run_transcript`. HTTP root names use
route templates, such as `http.POST /api/conversations/{conversation_id}/turns`.
The response includes `X-LangSmith-Trace-Id`, exposed through CORS.

An HTTP request encloses loading, execution and persistence. LangGraph nodes,
model calls and raw provider attempts appear beneath that request. Plain Python
workflows keep their existing implementation. A queued job creates a separate
execution root for each attempt: filter by the stable `job_id` to connect
enqueue, execution, stages and retries across processes. Queue waiting is not
included in execution latency; no span is held open during human outline review.
This correlation does not manufacture one continuous cross-process span.

## Coverage

| Path | Inspectable boundaries |
|---|---|
| All HTTP routes | Request lifetime, route, status, available entity IDs; includes CRUD, health and binary responses |
| Book/paper chat and side chats | Load/execute/persist, routing, hierarchy or retrieval, graph nodes, model calls and streamed execution |
| Summaries | Scope loading, draft, validation, repair and model calls |
| Video and course study | Conversation execution, graph, evidence retrieval, complete lecture summarization and persistence |
| Interviews | Source inventory, create/start/answer, hints, clarification, finish/report and adaptive graph |
| Ideal interviews | Entire Python generation/persistence operation and individual generated exchanges |
| Revision sheets | Worker attempt, source loading, figure reading, graph stages, render/quality repair, publish and follow-up QA |
| Cards | Enqueue, worker, inventory, extraction, generation batches, validation results, persistence, reviews and grounded card conversation |
| PDF ingestion | Enqueue, worker attempt, acquisition, validation, OCR/outline proposal, parsing, canonical persistence and verification |
| Video/course ingestion | Creation/upgrade, worker attempts, media, transcript, resources, frames, OCR, visual/spatial analysis, indexing, embeddings, quality gates and publish |
| Retrieval | BM25, hybrid selection, vector queries, rerank and external search; automatic retriever callbacks retained |
| Direct OpenRouter calls | One `llm`, `embedding` or `tool` child per physical request for OCR, captions, vision, embeddings, rerank, STT and TTS |
| Narration and dictation | Figure descriptions, audio cache decisions, synthesis/transcription and provider attempts |
| LiveKit voice | Session setup, commands, capture/playback and SDK usage events, correlated by session/conversation/ideal-flow ID |
| Reminders and CLIs | Python reconciliation, core study/ingestion/indexing command entrypoints and evaluation entrypoints; visual pilot providers and cache decisions |

Boundaries cover meaningful operations, rather than every utility function or
individual SQL statement. Manual SSE threads and concurrent page OCR inherit
the submitting context through `in_current_context`; async tasks and
`asyncio.to_thread` retain Python's context propagation. Parser subprocess
work is measured by its enclosing batch operation; it has no model calls.
Independent voice callbacks share their persisted identity rather than keeping
a startup span open for the entire room lifetime.

## Usage, failures and capture

Raw chat calls use `run_type="llm"`, OpenAI-shaped messages, model/provider
metadata and `usage_metadata`. Embedding outputs retain vector counts and
dimensions. Request failures and retries remain separate attempts. Existing
LangChain model runs are not wrapped in a second billed model span.

Reported provider usage/cost stays on the physical provider child; never add a
root's aggregate cost to its child costs. Missing receipts remain `unreported`.
Configured TTS and interview/ideal-voice rates are tagged as estimates, separate
from receipts. Narration voice emits SDK usage events; pricing is not inferred
when its provider does not supply a receipt. Cache hits create no new provider
cost. CPU/RSS measurements and quality scores belong to the evaluation/metrics
workstream and are not supplied by this tracing change.

Handled worker failures remain failed traces. SSE failures are marked even
when the transport has already returned HTTP 200. Deliberate task/job
cancellation has a separate `outcome`. Delivery/capture errors log their type
without changing application return values or exceptions. SDK queues flush on
API shutdown, queue-worker exit, voice-job teardown and finite process exit.

Custom captures omit dependency objects and authorization headers, redact
credential fields and signed URL queries, replace inline media, and bound
captured text/collections. The shared SDK client's anonymizer also filters
automatic model inputs/outputs. Prompt text and bounded source evidence are
still trace data: use the appropriate project access and retention settings.
The public HTTP boundary ignores incoming LangSmith trace headers.

## Verification

No-network regression checks:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
  uv run python -m unittest tests.test_observability -v
```

These checks explicitly enable tracing with an in-memory recording client to
verify SDK/LangGraph parentage, metadata, threaded streaming, HTTP/SSE errors,
provider retry receipts, disabled operation and delivery failure isolation.
A coverage check rejects unwrapped raw inference POSTs in OpenRouter clients.

Verify actual hosted delivery with synthetic inputs and no paid inference:

```bash
uv run python -m scripts.check_langsmith_tracing
```

This creates five spans in `${LANGSMITH_PROJECT}-tracing-smoke`, flushes them,
and reads them back to check nesting and correlation. It prints the run link.
Synthetic plumbing validation is distinct from live quality or a real voice
room test. The current validation record is in
[observability verification](observability-verification.md).

SDK details: [manual model traces](https://docs.langchain.com/langsmith/log-llm-trace),
[thread metadata](https://docs.langchain.com/langsmith/threads), and
[cost tracking](https://docs.langchain.com/langsmith/cost-tracking).

## Operational telemetry and product usage

Shared Python boundaries also export optional OpenTelemetry spans to Grafana.
[Operational observability](operational-observability.md) covers JSON logs,
CPU/RSS, root duration/outcomes, PostHog browser events and free hosted setup.
LangSmith remains the model/cost/evidence source.
