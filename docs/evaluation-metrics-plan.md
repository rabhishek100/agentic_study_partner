# Evaluation and metrics: five flows in depth

Draft v6 · 1 October 2026 · proposed work, not completed implementation.

The goal is thorough coverage of the five most important product flows with a
small shared evaluation system. Target implementation today and $1–$2 per paid
experiment. Human review uses a simple local UI. The five-flow selection below
is confirmed by the user.

## Scope and meaning of coverage

Confirmed top five:

1. Grounded chat, including retrieval, follow-ups and anchored side chats.
2. Complete chapter/section/paper summaries.
3. Dedicated lecture/video and course study.
4. Revision sheets and source-grounded follow-up questions.
5. Interviews: question generation, candidate assessment, adaptation and report;
   include ideal-interview artifacts and applicable voice behavior.

Book/paper chat and summaries have their own checks; video/course study gets
a dedicated suite for its multimodal evidence and temporal/source semantics.
Use one shared evaluation/reporting system for all five flows. Do not invent support: for
example, the course graph has no complete-course transcript summary node.
An unsupported request should take its documented route or report its limit.

"All aspects" means every checklist item below has an applicable case and an
explicit result. It does not mean every combination of sources, settings and
failure modes, or statistically proven quality from a handful of examples.
Measure model quality with real outputs, deterministic contracts with fixtures,
and actual transport/persistence with integration journeys. Report these layers
separately. A mocked success does not establish live model quality.

## Flow-by-flow evaluation checklist

| Flow | Inputs and scope | Quality and grounding | Workflow and failure behavior | User journey and efficiency |
|---|---|---|---|---|
| **Grounded chat** | Exact terms, paraphrases, synthesis, multiple sources, unanswerable/ambiguous questions; book/paper scope; selected text/page/section/prior-answer anchors | Production-context Recall@5/@8; valid pages; important-claim support; relevant figures/document evidence; answer completeness; correct abstention and external-source labelling | Follow-up rewriting, route/scope/history continuity; source locks and stale anchors; sufficiency and bounded widening; provider/reranker failure and fallback; judge failure separate from application failure | Actual API/streamed answer and citation navigation; persistence/resume and duplicate submission; first useful content and completion latency; retrieval/planner/generation cost; local CPU/memory |
| **Complete summaries** | Correct chapter/subtree/section or complete paper; short, long, dense, equation/figure-bearing sources; over-budget and insufficient-source requests | Essential concepts independently checked against source; explanations and qualifications, not just citation counts; equations/figures where needed; citation presence/locator/support and omission counts | Full-scope loading rather than top-k sampling; initial vs repaired content; repair limits; no silent truncation; safe warnings vs rejected unsafe drafts; malformed/truncated provider output | Select scope, receive/save/reopen summary, navigate citation; buffered summary behavior distinguished from streamed QA; scope/load/generation/repair latency and cost; CPU/memory |
| **Video/course study** | Lecture vs course scope, selected/excluded/unpublished lectures; moment/time-range/frame/linked-slide anchors; captions vs audio-derived transcript; version/readiness and damaged/missing resources | Required/cited-evidence recall across transcript, frames and slides; visual claims require visual evidence; correct timestamp/page/source locator; full lecture/topic coverage; cross-lecture synthesis and per-lecture balance; supported answer/abstention | Plan/retrieve/check/retry/synthesize; query rewriting and temporal expansion; retry bounds and source-version consistency; one query embedding per model/query across lectures; resource/transcript/frame failure; stale anchors, excluded sources, acquisition/processing cancellation and recovery contracts | Ingest/readiness fixture gate, open/play/seek source, ask anchored and course question, receive/save/reopen answer, navigate timestamp/frame/page; playback and side-chat state; stage latency, cost per lecture/course turn, ingestion vs query cost; worker CPU/memory |
| **Revision sheets** | Full chapter/paper and figure inputs; prose, equations, dense scope, figures and context overflow | Independent essential-concept coverage; claim/equation support; useful source figures/diagram relationships; citation correctness; readable rendered PDF with no clipping; visible unresolved findings | Inventory/compose/validate/render/judge stages; content/fit/quality repair bounds; first vs final quality; render/provider failure; cancellation/retry; source-based follow-up QA | Generate, view/download actual PDF, see warnings, ask follow-up, navigate sources; job persistence/idempotency; stage latency, repair cost, cost per accepted sheet; Chromium/worker CPU/memory |
| **Interviews** | Source-led/concept/system-design settings; weak/mixed/strong candidate answers; relevant coding/screen input; scope, difficulty and duration | Grounded questions/model answers/rubrics; criterion-level score agreement; unsupported extensions cannot improve grades; useful probes and coherent ideal dialogues; final report matches submitted answers | Plan/question/grade/verify/adapt/finish; appropriate probe vs advance; bounded follow-ups; hints and clarification; pause/resume/finish; provider failure; duplicate/stale submissions; persisted state | Setup to report and cited revision areas; editable dictation, voice interruption/reconnect and first audio where enabled; per-stage/turn/session cost and latency; CPU/memory; cache behavior |

Shared gates also cover ownership/source isolation, valid input, cancellation,
reload/retry, missing sources and version consistency. Ingestion is a prerequisite
for trustworthy results: use existing OCR/outline/extraction checks plus fixture
checks for canonical fidelity, idempotency and rebuilding derived data. Do not
buy a complete book/video re-ingestion for every eval run. Remaining features
such as flashcards, suggestions and notifications retain their existing tests and targeted
smoke checks; they are not silently described as deeply evaluated.

## Case selection and execution layers

Reuse the existing retrieval/multi-turn/video/interview sets and tests. Build
one manifest mapping every checklist item to a case/test, result, source
fingerprint and review tier. Missing mappings are coverage gaps, even when
aggregate scores look good. Keep held-out groups separate by source/topic or
whole conversation/session; preserve the history needed by follow-up cases.

Initial target: **roughly 40–60 workflow cases**, with these practical starting
allocations (a case may cover several checklist items):

- Chat: 12 evidence/answer cases plus 6 short context/policy scenarios.
- Summaries: 6 scopes spanning small/dense/equation-bearing chapters, a section
  and complete papers.
- Video/course: 8 lecture cases spanning transcript, visual and linked slides,
  whole-lecture/time-range summary and anchored follow-ups; 4 course cases
  spanning cross-lecture synthesis, source exclusion, insufficient evidence
  and history. Include complete conversation context and real frames where needed.
- Sheets: 5 representative scope/figure/layout scenarios.
- Interviews: 6 fixed candidate profiles plus 2 short ideal exchanges; reuse
  deterministic session cases for state, coding, clarification and voice errors.

This is 49 starting case units, not 49 calls. Conversations, summaries, sheets and
sessions produce multiple outputs/calls. Add fixtures to cover remaining
checklist cells; case count alone is not the completion criterion. Explicitly
separate three layers:

1. **Contracts:** deterministic and fixture-based checks for validation, scope,
   schedules, retry bounds, persistence, errors and state transitions; no paid
   provider required. Run these in CI.
2. **Live quality:** representative generated outputs on real canonical sources,
   using the production path/configuration, evidence-aware Luna judgments and
   human review. Include all five flows; model scores remain diagnostic where
   human review is pending.
3. **End-to-end/performance:** one meaningful API/UI journey per flow, plus
   boundary recovery checks and resource samples. Attribute success to the
   layers actually exercised; fixture/replay latency excludes provider latency.

## Repair scoring before trusting the baseline

- The general answer judge currently lacks source evidence. Supply exact
  generation evidence, citation mapping, expected points and relevant history.
- Split citation presence, page/timestamp validity and claim support. Empty
  citations cannot pass a grounded factual answer; use route-specific eligibility.
- Match production retrieval settings and score actual chunk context, preserving
  historical node-level retrieval results as a separate diagnostic.
- Preserve successful outputs on judge failure. Report attempted, eligible,
  scored, failed, skipped and unknown counts; empty requirements cannot inflate
  recall or groundedness. Separate application failure from evaluator failure.
- Judge summary/lecture/sheet completeness against independently checked concepts,
  not solely against the inventory produced by the same generation pipeline.

## Separate repository-wide observability workstream

LangSmith tracing is a standalone implementation request. It covers **all
functional workflows**, including ingestion, cards, suggestions, dictation,
narration, workers and voice. Deep quality evaluation remains focused on the
five selected flows. Free Grafana operational monitoring and PostHog usage
analytics are a second standalone implementation: see
[setup and coverage](operational-observability.md). These measure system health
and product behavior; quality scoring and the review UI remain in this plan.
See [observability](observability.md) for implementation,
organization, correlation, provider usage and validation; do not count tracing
coverage as quality-evaluation coverage.

The evaluation runner must preserve its experiment project/context, attach
case/source/configuration IDs, link reviewed outputs to their traces, and export
latency/usage/cost with explicit missing-data and estimate labels. Per-case CPU/RSS
sampling (beyond the implemented process metrics), quality scoring, the review UI and regression comparisons remain
part of this evaluation plan.

## One simple review UI, appropriate to each artifact

A local page loads a saved evaluation bundle and shows progress, scope/question,
source excerpts with page/timestamp labels, and a short expected-points checklist.
The central content changes by flow: answer, complete summary, video answer
with transcript/frame/slide evidence, rendered sheet pages, or interview
question/candidate answer/grade/dialogue. Video evidence includes the actual
frame or supporting page where relevant and a timestamp navigation control.
Show source passages as well as references; a citation link alone is insufficient
for quick review. Keep model verdicts hidden until the user submits a rating.

Common review controls:

- Supported by the source? **Yes / Partly / No / Unsure**.
- Covers the important points? **Yes / Partly / No / Unsure**.
- Useful/clear? **Yes / Partly / No / Unsure**.
- Optional issue/note; **Back**, **Skip**, **Save and next**.

Only add relevant controls: correct visual/timestamp support for video, readable
layout for sheets, fair/too-high/too-low grade for interviews. No separate review tool
per flow. Support keyboard/focus/AA contrast, autosave/resume, skip without a
pass, and validated label export keyed to case/output hash. Changed outputs
invalidate old labels. Each item links to its exact LangSmith trace. The page generates no answers or provider calls.

Start human calibration with **ten reviews: two per flow**, including one strong
and one failure/boundary example. For multi-part artifacts/sessions the UI groups the relevant
items, rather than requiring a whole book/session review. Add disputed/low-score
cases afterward as needed. Ten reviews calibrate the judge; they do not make the
entire suite human-verified. Intentionally altered calibration artifacts remain
labelled separately from actual application outputs in the resulting report.

## Metrics for every flow

Each flow receives the same six-part scorecard, with flow-specific definitions:

| Dimension | Measures |
|---|---|
| Quality | Supported/correct output; expected concept coverage; usefulness; interview score agreement or video/sheet-specific correctness |
| Grounding | Citation presence and locator validity; supported/contradicted/unsupported/unclear important claims; correct abstention where applicable |
| Workflow/reliability | Correct route/scope/state; success/error rate; retries/repairs/fallbacks; cancellation/recovery and session/journey completion |
| Latency | End-to-end and stage timing; first useful content/first audio where applicable; queue wait separated from processing |
| Usage/cost | Tokens and reported/estimated/unknown USD; planner/retrieval/generation/repair/speech components; application and judge spend separately; cost per accepted artifact/session |
| Local resources | CPU-seconds and peak memory on representative isolated runs for each flow, including worker subprocesses; warm/cold and hardware/quota labels |

Graph/model latency, usage and cost come from LangSmith traces; provider receipts
and labelled estimates fill accounting gaps. Join API/client timings and resource
samples through request/turn/job/session identifiers. Local CPU does not reveal
hosted model CPU/GPU usage. Do not sum rolled-up trace costs and their leaves.
Show sample counts, medians and slow cases; small-sample p95 is exploratory.
Measure lightweight concurrency separately if time/budget permits, and avoid
assigning shared-process CPU to individual concurrent requests.

## Cost control and tools

Use **LangSmith + Luna + local review/report files**. Keep Opik optional for now.
Share task outputs and scorers instead of creating a second evaluation framework.

A paid experiment has a shared **$2 maximum**, including generation, judging,
embeddings/reranking, retries/repairs, voice and both arms if it is paired. Target
$1 when possible. Preflight the case manifest, reserve conservative maxima
before calls, bound output/retries, start paid concurrency at 1, and stop before
overspending. Unknown cost is not zero. Resume/rejudge saved outputs without
regenerating, while excluding replays from live latency results.

Split costly checks into explicitly selected experiments if necessary; never
reset the cap silently per runner or claim unexecuted cases passed. A $2 baseline
run plus a separate $2 improvement experiment could cost $4 total; this is a
per-experiment cap, not an assumed daily allowance. Full ingestion and long voice
sessions require separately bounded benchmarks. If budget/source availability
prevents a live check, show the uncovered aspect and do not mark that flow done.

## Today's order and definition of done

1. Use the confirmed five-flow selection and build the coverage manifest using existing
   cases/tests; fix scoring defects and production-configuration mismatches.
   Establish the shared trace structure and cover Python/provider/worker gaps
   before treating baseline latency or cost as complete.
2. Build the review page and prepare two representative outputs per flow.
   The user reviews while tracing/metrics and remaining cases are prepared.
3. Run the five-flow baseline with component, contract and end-to-end checks;
   collect cost/latency and per-flow CPU/memory; surface coverage gaps explicitly.
4. Choose one or two clear baseline failures. Change one thing per experiment,
   compare on the same frozen cases/judge, and check all five flow regressions.
5. Publish a sanitized tracked report: coverage matrix, per-flow scorecards,
   failure examples, before/after results, trace links and limitations.

A flow is done for this iteration when every applicable checklist item has
execution evidence, at least one real quality output and actual journey are
reviewed, boundary/recovery contracts pass, its end-to-end trace structure
is verified in LangSmith, and its metrics and open findings are visible. Live quality confidence remains limited by sample size/review tier.
Implementation today is the target; the larger scope supersedes v2's 4–8-hour
estimate. Source/provider availability and actual case costs determine execution
time; unavailable checks remain incomplete rather than being replaced by a
smaller claim of coverage. Prioritize all five baselines before extra optimizations.

Use the final report to explain retrieval tradeoffs, evidence-based artifact
quality, explicit state/recovery, and where latency/cost/resources are spent.
Record failures and unsuccessful improvements as well as wins.

Audit context: existing datasets include 15 retrieval cases, 44 multi-turn turns,
47 current video turns and 30 interview-style answer cases. Previous audit ran
74 evaluator-related tests successfully; no live paid eval or hosted-trace audit
has run in this planning session. Reconcile old documented results against exact
artifacts before quoting them. Detailed runs are ignored by Git; commit sanitized
summaries separately, preserving private source/audio/artifact material locally.

References: [evaluation guide](evaluation.md), [implemented flows](flows.md),
[LangSmith evaluation](https://docs.langchain.com/langsmith/evaluation).
Only the plan has changed so far; this document supersedes drafts v2–v4.

## Test scope and maintenance

The [test-suite audit](test-suite-audit.md) distinguishes existing behavioral
coverage from missing browser journeys and evaluator-integrity/budget checks.
Use those gaps to guide new tests; prefer strengthening or replacing weak
checks over increasing the test count. CI uses pytest to include both unittest
and function/parameterized tests.
