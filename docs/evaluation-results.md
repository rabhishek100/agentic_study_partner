# Five-flow evaluation: results and improvements

2 October 2026. Implementation, commits and resume instructions are tracked in
[evaluation-progress.md](evaluation-progress.md). Human calibration is deferred
by the user; all model quality scores remain diagnostic.

## Delivery status

| Workstream | Implemented and verified | Remaining limitation |
| --- | --- | --- |
| Five-flow evals | 54-case manifest; native adapters; complete source/image capture; shared budget; resumable runs; simple human review UI; baseline and improvement runs | Zero human labels; selected rechecks use different configurations, so there is no combined final 54-case quality score |
| Repository-wide LangSmith | HTTP/SSE, LangGraph, Python workflows, providers and workers; correlation IDs, consistent operation names, flow and experiment tags | Hosted live readback covers the evaluated five flows; real voice room and every deployed process still need runtime acceptance |
| Metrics/logs/traces | OpenTelemetry to Grafana Cloud; structured correlated logs; latency/errors, CPU and memory dashboard | Existing application processes need restart to load exporter credentials; no hosted application deployment performed |
| UI analytics | PostHog page/control/flow events, opt-in and privacy filtering; hosted events and dashboard verified | No session replay, prompt/form contents or automatic capture; usage funnels need real users |
| Tests | Discovery fixed; dead duplication removed; reproduced regressions protected; full isolated backend and connected journeys pass | Storage/corpus skips remain explicit; browser checks do not prove full accessibility, real sign-in or voice quality |

The five flows are chat, complete summaries, dedicated video/course study,
revision sheets and interviews. Their content, grounding, behavior, UI/recovery
and performance dimensions are mapped in [the plan](evaluation-metrics-plan.md)
and [the manifest](../evaluation/five_flow_manifest.json). Deterministic
contracts, retrieved evidence and generated quality are evaluated separately.
The separate repository-wide tracing request includes flows outside these five.

The local review command is:

```bash
uv run --frozen --extra voice python -m scripts.review_evaluations \
  evaluation/runs/unit7b-bm25-corrected --port 8766
```

The UI presents immutable outputs and original evidence, with save/resume/export.
The ten-output calibration can happen later; no agent-created human ratings exist.

## Baseline

The frozen baseline attempted 50 native production workflows: 19 chat, six
summaries, 12 lecture/course questions, five sheets and eight interview cases.
**43 generated/judged; seven failed explicitly.** Provider receipts recorded
**$0.348342240**, including failed generation and judging, with no unknown
reservations. The earlier aborted transport attempt cost $0.008091475.

Book retrieval uses labelled **BM25** for this budgeted baseline. Production's
`hybrid_rerank` default remains unchanged. Available metadata cannot bound
reranker pricing, so a paid comparison was not run under the hard ceiling.
The baseline is [inspectable here](../evaluation/five_flow_baseline_20261001.json).
Exact prompts, original images, source snapshots, PDFs and receipts remain in
ignored private run directories; no source prose or credentials were committed.

| Flow | Generated / attempted | Median completed case | Longest completed case |
| --- | --- | --- | --- |
| Chat | 19 / 19 | 8.19s | 12.11s |
| Complete summaries | 4 / 6 | 24.58s | 27.98s |
| Lecture/course | 12 / 12 | 8.95s | 9.93s |
| Revision sheets | 2 / 5 | 231.07s | 239.74s |
| Interviews | 6 / 8 | 7.07s | 8.33s |

These small completed-case samples are descriptive, not SLOs or statistical
claims. Interview times measure candidate grading; both ideal dialogues failed.
Native generation excludes browser/API overhead. LangSmith readback supplies
latency, tokens and generation cost, counting physical model leaves once.
First-content latency is unknown for buffered generation. Separate Python
CPU-seconds and sampled RSS exclude child renderers and remote model compute.
Provider receipts enforce the combined generation/judge/retry budget rather
than replacing trace-backed performance attribution.

## Measured changes

| Failure | Change | Evidence |
| --- | --- | --- |
| SDK selected unpriced `/responses` transport | Explicit Chat Completions and provider reasoning payload | Installed SDK payload regressions; corrected baseline completed |
| Video starter questions returned HTTP 500 | Use canonical `start_ms, chapter_index` | Real SQL/cache regression failed before and passed after; connected journey has no HTTP 5xx |
| Two summaries lost planned section identity | Carry owner-checked canonical IDs through execution | Same source/gold: 0/2 generated before, 2/2 after; valid citations and recall 1.0 |
| A refusal was classified as an answer | Recognize straight/curly `isn't enough evidence` | Exact saved-output replay fixes classification with zero new model calls |
| Complete-paper instructions took top-k QA | Deterministic whole/entire/complete scope grammar | Unchanged case takes `hierarchy_summary`; recall 0.5 → 1.0 and citations valid |
| Summary addendum falsely claimed details were absent | Retain full canonical source and preceding answer in repair | Fixed-draft regression; live capture confirms full-source addendum and exact prior-answer retention |
| Three chapter sheets exceeded 64k | Promote measured 128k capacity consistently | Same chapters: 0/3 generated before, 3/3 after; quality findings remain |
| Two ideal dialogues rejected missing citations | One bounded regeneration with explicit missing-marker feedback | Both pass nine deterministic dialogue/citation gates; RAG dialogue used one repair, notification dialogue used none |

The canonical-scope experiment cost **$0.007109685**, including its prerequisite
clarification turn. The repaired summaries completed in 13.44s/13.57s; Luna
rated both supported and 4/4 for correctness, coverage and usefulness. Original
failed cases remain intact. Classification replay does not prove better prose
or reliable model routing.

The paper routing recheck cost $0.0064758 and still produced an unsupported
addendum: heading-only Training/Results nodes hid their substantive children
from the narrow repair prompt. The full-source repair recheck cost $0.01012690
and completed in 25.68s. Two hosted physical model spans and the captured second
request confirm an actual repair. Citations and recall pass; Luna now reports
supported, 3/3/3, but finds omitted future directions. The fixed-draft regression
establishes prompt integrity. Fresh model draws are not a controlled causal
comparison of prose quality, and citation coverage is not concept coverage.

## Additional coverage and capacity experiment

The manifest adds four independently source-backed cases: paper QA, complete
paper summary, whole-paper sheet and indexed Spanner course study. These add
coverage rather than replace missing-source or failed baseline cases. Paper
criteria were authored from canonical content before generation; original-page
equation and experiment-table fidelity still need human review.

The ten-case `unit8-source-and-repairs` experiment completed for **$0.497982575**
under a $1.50 ceiling. Paper QA and indexed Spanner course have valid citations
and supported diagnostic judgments. The paper sheet generated a two-page PDF
with no open production findings, but diagnostic coverage/usefulness were only
2/4. Both ideal dialogues retain uncalibrated usefulness, and their authored
synthetic evidence limits independent technical judging.

All three larger chapter sheets generated at 128k, justifying capacity promotion
without dropping source content. All retain production findings, including
incomplete generated sentences, missing concepts and figure-evidence mapping
concerns. They took 239.54–267.47s, so sheet speed remains an improvement target.
All nine chapter PDF pages and both paper PDF pages were visually inspected:
readable, without observed layout clipping, but with substantial unused
whitespace. Agent inspection is not user calibration.

[Sanitized improvement results](../evaluation/five_flow_improvements_20261002.json)
retain fingerprints, output hashes, trace links, checks, scores and receipt
totals for four experiments and 15 case attempts. Selected rechecks are not a
full final run under one configuration. Baseline, aborted attempt and improvements
total **$0.878128675**; including the earlier grading smoke, **$0.879461025**.
Every experiment stayed below its ceiling; zero unknown reservations remain.
The initial 11-case sample spend is already included in the full baseline.

## Verification and test decisions

After the final runtime repairs, the isolated backend suite passed **2,080 tests
and 962 subtests**, with 38 skips and 12 warnings (129.73s). Thirty-five skips
need Storage configuration and previously passed in a separate 79-case loopback
Storage run; three require corpus fixtures. Frontend verification passed **753
tests**, TypeScript and a production build. Branch pushes do not establish
hosted CI, which triggers on main or PRs.

The explicit Chromium → FastAPI → isolated Postgres harness passed again after
final runtime changes: chat/summary SSE and reopen; revision enqueue/worker/PDF
and follow-up; lecture answers; course exclusion and reopen; interview
pause/reload/resume, persisted grading and report. No browser JS errors or HTTP
5xx occurred. Auth, speech and models are fixtures. Keyboard focus, reduced
motion and 390px horizontal fit are checks, not a full WCAG audit.

Keep most tests: ownership, cache, queues, cancellation, source contracts and
stateful UI behavior protect real failures. Dead duplicated code was removed,
network smoke strengthened, and previously omitted pytest cases enter CI.
New tests reproduce measured failures. A legacy repair assertion now requires
full source and original-answer retention instead of the old narrow prompt;
a new budget test rejects oversized repairs before a second provider call.
See [test audit](test-suite-audit.md) for replacement candidates and limits.

## Next work, in priority order

1. **Human calibration when ready:** ten outputs, two per flow, plus original
   equations/PDFs. Inspect omitted concepts, claim support and interview utility
   before calling the same-family judge credible quality evidence.
2. **Sheet quality:** use saved findings to improve sentence completion,
   independent concept/figure coverage and page density. Compare unchanged
   source/gold before exploring cheaper context or faster rendering.
3. **Source readiness:** original RPC/OCC course members lack indexed evidence;
   GFS/Raft are unpublished. Ingestion/readiness comes before retrieval tuning.
   Keep safe abstention distinct from an expected-answer mismatch.
4. **Policy-aligned gold:** the user explicitly retained labelled general model
   knowledge fallback. Old source-only LoRA expectations still fail; preserve
   them and version future policy tests. General knowledge is not source-grounded
   evidence, and the classifier fix does not solve that mismatch.
5. **Runtime acceptance:** restart app processes to load Grafana credentials;
   test real sign-in, audio playback and a live voice room. No hosted app
   deployment was performed. Add actual contrast/focus-return browser checks
   before deleting corresponding styling guards.

Repository-wide LangSmith, Grafana and PostHog implementation and hosted delivery
checks are recorded in [observability verification](observability-verification.md).
They establish transport and instrumentation, not all deployed journeys.

## Interview walkthrough

Explain canonical source → deterministic retrieval → explicit LangGraph routing
→ cited output. Show a trace and a failed case. Distinguish valid locators from
supported claims and independent concept coverage. Demonstrate unchanged-gold
canonical-scope or paper-routing before/after results. Explain BM25's bounded
cost, missing-source failures, and why human review is necessary for a
same-family judge. Show Grafana latency/errors/CPU/RSS and PostHog usage events:
tracing, operations and product analytics answer different questions.
