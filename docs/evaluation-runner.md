# Shared evaluation execution

The shared five-flow runner is being delivered in the units tracked in
[evaluation-progress.md](evaluation-progress.md). The transport budget guard is
implemented; the manifest, production adapters and review UI are separate units.

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
  python -m pytest tests/test_eval_budget.py tests/test_eval_integrity.py -q
```

Tests use real httpx clients with fixture transports: no paid inference. They
cover sync/async requests, retries, concurrent reservations, interrupted resume,
unknown receipts, refused calls, file permissions and exclusive process locks.
