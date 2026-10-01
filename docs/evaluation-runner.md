# Shared evaluation execution

The shared five-flow runner is being delivered in the units tracked in
[evaluation-progress.md](evaluation-progress.md). The budget guard, manifest and
resumable orchestration are implemented; production adapters and the review UI
are separate units.

## Manifest and resume

[five_flow_manifest.json](../evaluation/five_flow_manifest.json) contains 50
case units: 19 chat (including one context prerequisite), six complete summaries,
eight lecture and four course cases, five sheets, six candidate assessments and
two ideal dialogues. Existing frozen book/video/candidate/ideal datasets are
reused. Course expectations are author-labelled and await human review. Sheet
essential-concept labels are currently missing; this is an explicit quality gap.
Cases are not provider-call counts. A dependency graph retains conversational
context and lets subsequent cases reuse the preceding saved state.

`evals.suite.run_suite` writes a private `bundle.json` after every phase. It saves
generated outputs before judging, fingerprints output/evidence/artifacts, and
rejects changed manifest/configuration on resume. Completed outputs are reused;
failed judges are retried against saved outputs. Interrupted or failed generation
requires explicit retry and retains all previous budget reservations. An
exclusive run lock prevents two processes from racing the same experiment.

Coverage includes case targets, existing contract-test links, separate journey
status and pending human review. Missing mappings stay incomplete. A completed
generation is not a quality pass; fixture execution is not a live baseline.

Regenerate/check the manifest without inference:

```bash
uv run --frozen --extra voice python -m scripts.build_eval_manifest
uv run --frozen --extra voice python -m scripts.build_eval_manifest --check
```

## Experiment budget

`evals.budget.Budget` stores the original ceiling (greater than zero, at most
$2), frozen public model pricing and every physical inference reservation in a
private run directory. It persists reservations **before** sending requests.
Generation, judging, embeddings and retries share the same ledger. Timeouts,
missing receipts and interruptions keep their conservative reservation; they
never become zero spend. Resuming cannot change the ceiling. Provider receipts
release unused reservations; a receipt exceeding its bound persistently blocks
the experiment for investigation.

Inside an isolated evaluation command, the temporary httpx transport guard
covers both raw calls and LangChain/OpenAI calls, including copied threads and
async clients. It bounds output at 16,000 tokens, bounds accepted provider
prices, rejects unpriced models/endpoints, paid plugins, built-in provider tools,
model fallbacks, multiple completions, and raw provider streaming. Text input
uses a conservative byte-based token bound; image input reserves the complete
model context. Audio/video/file input and external paid speech/search are
currently refused; their deterministic contract checks remain separate.

The guard is an evaluation-only boundary, never installed in the serving API.
Its limits are recorded configuration differences from a production run, not
silent optimizations. Exact bounded request bodies are saved locally for source
and prompt inspection, without Authorization headers. Run files are private and
must stay in ignored `evaluation/runs/`; do not commit source excerpts or images.

This ledger controls experimental spend; quality/performance reports still use
LangSmith traces for actual model timing, usage and cost, with missing/estimated
receipts distinguished. It is not a substitute for a provider-side billing cap.
Price filters rely on provider enforcement; unexpected receipts stop further
work rather than retroactively guaranteeing a cap against a billing error.

Current OpenRouter contracts were checked on 2026-10-01:
[chat model metadata](https://openrouter.ai/api/v1/models),
[embedding model metadata](https://openrouter.ai/api/v1/embeddings/models),
[provider price limits](https://openrouter.ai/docs/guides/routing/provider-selection#max-price),
and [credit limits](https://openrouter.ai/docs/api_reference/limits).
Metadata is fetched and frozen per experiment; prices are not hardcoded.

## Verification

```bash
LANGSMITH_TRACING=false OTEL_ENABLED=false uv run --frozen --extra voice \
  python -m pytest tests/test_eval_suite.py tests/test_eval_budget.py tests/test_eval_integrity.py -q
```

Tests use real httpx clients with fixture transports: no paid inference. They
cover sync/async requests, retries, concurrent reservations, interrupted resume,
unknown receipts, refused calls, file permissions and exclusive process locks.
