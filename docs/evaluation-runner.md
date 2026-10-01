# Shared evaluation execution

The shared five-flow runner is being delivered in the units tracked in
[evaluation-progress.md](evaluation-progress.md). The budget guard, manifest and
resumable orchestration and native production adapters are implemented. The
review UI and connected journeys are separate units.

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

## Native generation and reports

```bash
# No inference: inventory and save/resume smoke.
uv run --frozen --extra voice python -m scripts.run_evaluations --plan
uv run --frozen --extra voice python -m scripts.run_evaluations --fixture \
  --output evaluation/runs/fixture-smoke

# Paid generation and Luna judging share a $2 ceiling. Repeating the same
# command resumes saved outputs; add --retry-failed only to retry generation.
uv run --frozen --extra voice python -m scripts.run_evaluations --live \
  --case proximity-strong --output evaluation/runs/baseline --max-usd 2
```

Remove `--case` to target all 50 cases; `--flow` selects one flow and its state
prerequisites. The runner calls production book/video/course conversations,
sheet generation, candidate grading and ideal dialogue generation. It does not
persist new user conversations or enqueue/replace user artifacts. Exact generation
requests, sheet PDFs/provenance, partial failed dialogue drafts, expected values,
checks, diagnostic judgments and LangSmith links are inspectable in the private
bundle. The judge receives complete captured contexts and original supplied
images; text-only PDF layout and missing independent sheet labels remain unknown.
Luna judging is a diagnostic from the same model family, pending human calibration.

Book binding requires the original file hash and unique owned canonical TOC.
Gold node ordinals are rebound to current IDs with page/citation validation.
Canonical content, images and retrieval builds are fingerprinted. Video/course
binding freezes published versions, evidence, frames, resource membership and
ready course members. Source drift rejects a resumed experiment. Dataset bytes,
implementation bytes and prompt-affecting configuration are also frozen; a
changed experiment requires a distinct directory. These safeguards intentionally
reject a stale resume rather than quietly mixing results.

`--retrieval-mode bm25` is the default baseline. Production currently uses
`hybrid_rerank`; the command records this difference. `hybrid` enables priced
embedding retrieval. Reranking remains fail-closed because public Cohere rerank
metadata did not supply a usable request charge bound; a zero token price is not
treated as proof of a free endpoint. See the current
[OpenRouter model discovery contract](https://openrouter.ai/docs/guides/overview/models).

Each generated case gets an organized LangSmith root and model children. Reported
latency, model tokens and model cost come from hosted trace readback, counting
physical model leaves to avoid double counting nested totals. Missing receipts
stay unknown. The budget rollup includes generation and judging; the generation
trace metric is labelled separately. CPU seconds and sampled peak RSS cover the
isolated Python process, including background telemetry and excluding remote
models and child browsers. First-content latency is unmeasured in these
non-streaming native evaluations. `report.md` keeps failures and unknowns visible.

## Local human review

```bash
uv run --frozen --extra voice python -m scripts.review_evaluations \
  evaluation/runs/baseline --port 8766
```

Open the local session link printed by the command. The server binds only to
loopback and requires a per-launch session token for source/artifact access.
The page shows planned/generated counts, a flow filter, saved outputs, original
source text/images and sheet PDFs. Grading feedback and ideal dialogues are
readable without opening raw JSON. Diagnostic model scores are initially hidden.
Choose supported/unsupported/unknown, 0–4 scores or unknown, optional concept
labels and layout findings. **Save & next** moves to the next unreviewed generated
case; **Save** keeps the current case. Source tabs retain unsaved drafts.

Labels persist in private `reviews.json`, tied to the experiment fingerprint and
exact output hash. Edits retain history; reload resumes saved labels; export
downloads only labels and their diagnostic agreement summary. Saving also updates
the report's human-review section. The UI never calls a generator or judge.
Changed capture/artifact bytes invalidate their integrity checks. Older captures
without hashes are marked legacy snapshots. Failed generation may show partial
draft/source artifacts but cannot be labelled as a completed output.

Review-token access, same-origin writes, unsafe-content handling, source integrity,
PDF bytes and blind diagnostics have API tests. The explicit browser smoke covers
save/reload/export, unsaved tab drafts, PDFs, keyboard focus, a 390px viewport,
reduced-motion mode and hostile content. It uses fixtures and never creates a real
human quality label:

```bash
uv run --frozen --extra voice python -m tests.check_eval_review_browser
```

## Automated checks

```bash
LANGSMITH_TRACING=false OTEL_ENABLED=false uv run --frozen --extra voice \
  python -m pytest tests/test_eval_reviews.py tests/test_eval_adapters.py tests/test_eval_suite.py \
  tests/test_eval_budget.py tests/test_eval_integrity.py -q
```

Tests use real httpx clients with fixture transports: no paid inference. They
cover sync/async requests, retries, concurrent reservations, interrupted resume,
unknown receipts, refused calls, file permissions and exclusive process locks.
