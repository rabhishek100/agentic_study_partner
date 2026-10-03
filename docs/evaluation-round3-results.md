# Evaluation results and production comparison

The twenty-candidate round is complete. **Keep Luna; retain larger sheet
citations.** The earlier verified retrieval, summary and sheet-generation
changes, plus the citation change, are deployed to the combined production
API/worker at `d8c9dda`. No model or additional prompt experiment is promoted.
Required human review has been replaced by LLM review. Scores are automated,
without human calibration.

Reported provider spend is **$3.867140599**. Another **$0.099636114** remains
reserved for earlier requests whose bills are unknown. Conservative commitment
is **$3.966776713**, within the new $5 ceiling. This includes controls, failed
requests, protocol corrections, repeats, two reviewer models, final generation,
saved-output review and both production windows. The previous round's
$3.904616935 is separate. Every child stayed within its original cap.

## Before evaluation work versus current results

Rejudge the earliest preserved outputs with the same v4 source-evidence rubric
used for the final run. All fifty shared cases have identical input, source
and expected fields. Four subsequently added cases are excluded. The same five
source/policy/capture cases stay visible outside the quality denominator.

| Flow | Earliest saved evaluation | Current, including separate retry |
| --- | ---: | ---: |
| Chat | 10/17 | 12/17 |
| Summaries | 3/6 | 6/6 |
| Video/course | 6/9 | 8/9 |
| Revision sheets | 0/5 | 3/5 |
| Interviews | 5/8 | 5/8 |
| **Shared quality cases clearing every check** | **24/45** | **34/45** |

The earliest run completed 43/50 requests. The final run completes 49/50 shared
requests on its first attempt; one provider failure passes a separate retry.
Across the full later manifest, the first attempt completes 53/54 and clears
35/49 eligible quality cases, or 36/49 with that retry. Preserve both attempts;
the retry is not a clean first-pass success.

The earlier fixes help complete chapter summaries, previously context-limited
large sheets, valid citations and source context around retrieved definitions.
Interviews still have reasoning/support gaps. Better execution does not mean
all outputs meet the quality bar. All six final PDFs are readable, but three
retain native content warnings.

This is the **earliest measured evaluation**, not an exact reconstruction of
pre-evaluation production. It already used Luna 6. Historical code `f705751`
precedes the Luna-default change, but actual pre-chat production bills and
latencies were not saved comprehensively and remain **unavailable**. No old
buggy version was deployed to fabricate that comparison.

The start-of-this-round frozen control clears 41/49 under v3; its sheet source
images needed measurement correction. The current run is not a demonstrated
broad quality gain over that control. Generation and judge variation are
material. Font selection rests on six paired renders of **identical content**:
9-point citations become approximately 10 points, with identical extracted
text, original figures, page counts and fit checks, for $0 inference spend.
Correcting source-image provenance is a measurement repair, not a generator win.

Evidence: [shared historical comparison](../evaluation/round3_historical_comparison.json),
[all candidate/final measurements](../evaluation/round3_screen_results.json),
[paired rendering](../evaluation/round3_free_render_results.json).

## What the twenty experiments established

| # | Plain-language change | Result / decision |
| ---: | --- | --- |
| 1 | DeepSeek writes answers | Coverage and source-support gaps; keep Luna. |
| 2 | Qwen writes answers | Coverage and source-support gaps; keep Luna. |
| 3 | Luna Pro writes difficult answers | Luna judge favours its audit, but a blinded Gemini review does not confirm the gain. More expensive and usually slower; reject. |
| 4 | Gemini writes answers | Some good answers, but incomplete summaries/course coverage; no default switch. |
| 5 | DeepSeek chooses the chat route | Structured-response failure and provider overload break the conversation chain; reject. |
| 6 | Qwen grades answers | All six cases fail in each of three preserved protocol attempts; reject this route/settings combination. |
| 7 | Qwen drafts sheets | Draft compatibility improves, but repair structures still fail; reject. |
| 8 | Gemini reads source figures | First paper gain does not repeat consistently; chapter-8 warning remains. Reject. |
| 9 | Preserve important query words | Does not repair broad audit coverage; reject. |
| 10 | Boost exact section titles | Hurts the definition/control cases; reject. |
| 11 | Split context between two sections | Broad audit remains incomplete; reject. |
| 12 | Add vector search without reranking | Does not fix the target audit omission; reject. |
| 13 | Include neighboring lecture passages | Spanner mechanism coverage remains incomplete; reject. |
| 14 | Allocate the summary word budget by section | Initial paper gain does not survive matched repeats; reject. |
| 15 | Carry sheet qualifications separately | Native content warning remains; reject. |
| 16 | Try more balanced sheet layouts | Same selected layouts on six sheets, with extra attempts; reject. |
| 17 | Make citations larger | Same content and pages, readable citations, no added model call; **retain**. |
| 18 | Add another preparation cache | Existing validated saved-sheet reuse already covers normal repeated opens; no extra cache. |
| 19 | Make grading list requested points first | Uncertain RAG judgments and a fabricated literal-quotation rejection; reject. |
| 20 | Add concrete reasoning guidance to ideal answers | Both outputs retain the native reasoning-signal failure; reject. |

Nine blinded, counterbalanced alternate-model reviews preserve disagreements
with the Luna judge. No human agreement is claimed. Original paid failures and
protocol corrections remain in immutable private bundles.

## Same real account, before and after deployment

The production account, questions, source selections and canonical fingerprints
match. Production uses `hybrid_rerank`; native gold tests use BM25. These are
separate comparisons. One observation per scenario is descriptive, not a
reliable speed percentile or an isolated causal benchmark.

| Production scenario | Before `0983a1c` | After `d8c9dda` | What happened |
| --- | ---: | ---: | --- |
| Responsible-AI audit | 9.571 s | 16.117 s | Fuller model-card checklist, seven cited sources; unrelated CI/CD image remains. |
| Complete Transformer-paper summary | 25.688 s | 30.165 s | Both complete; no speed gain in this pair. |
| Cold paper sheet | 182.520 s | 184.892 s | Both three pages; current citations use v5. Before has no native warning; after has one. |
| Video BLEU question | 5.768 s | 4.725 s | Both correctly admit the lecture lacks the score. |
| Selected Spanner lecture | 3.463 s | 3.127 s | Both cite consistency and admit the missing TrueTime mechanism. |
| Chapter-1 ideal interview | 85.745 s cold | 0.034 s cached | Same saved 15/15-topic flow reused. **Not faster fresh generation.** |

Synchronous times are Railway web HTTP completion durations. Sheet time is the
persisted enqueue-to-ready interval, including waiting, generation and rendering;
its 45/57 ms enqueue responses are not completion time. The after sheet is
explicit regeneration, preserving baseline version 1 beside version 2. Its
known gap is retained rather than regenerated until it appears to pass.

The before window bills **$0.04421037**. The after window bills **$0.06135062**,
including a separate complete adaptive-interview cost sample. That sample has
44 exact receipts totalling **$0.022431575**. Subtracting those leaves a window
remainder of **$0.038919045**; it is not a precise bill for each UI request or
proof of a settings-driven saving. Cache state and variable repair calls differ.
Total production key usage is **$0.10556099**, inside its $0.75 cap. The normal
production key is restored and read back on the healthy new build.

The native cost sample uses typed synthetic weak answers on the larger
chapter-6 source (102,554 evidence characters, including topic overlap).
It finishes naturally after **18 answers**, covers **9/9 planned areas** from
39 source topics, and creates the report. It includes questions, grading,
follow-ups and repairs, with no web research or voice. It is cost/completion
evidence, not a human interview-quality study or coverage of all 39 topics.

Evidence: [production pairs and cost sample](../evaluation/round3_production_comparison.json).
Actual outputs, PDFs, logs and screenshots stay private under ignored
`evaluation/runs/round3/production-paired/`.

## Cost and speed limits

The fixed 54-case native control's generation/necessary-review bill is
$0.356304615, including its continuation. The current generation bill, including
the separate retry, is $0.301071610. External evaluation judges are excluded
from this product-usage comparison, but included in the $5 experiment ledger.
This observation does not prove font size reduces generation cost: repair
counts and continuation overhead vary. The retained font change adds no
provider call or model-price increase.

For the confirmed light profile, the forecast is **about $0.93/month**, including
20% headroom, cold/cache-write token rates, maximum observed per-flow costs,
100 chats, five summaries, three sheets, two interviews and twenty video/course
questions. Adaptive sizing allows twenty answers at the most expensive observed
turn rate; the entire before-window bill conservatively bounds the selected
cold ideal interview because unsuccessful ideal receipts were not separately
captured. Voice and arbitrary larger ideal chapters are outside this sizing.

The $0.93 scenario assumes one rerank search per chat and allows one entire
8,192-token embedding context per search. At two searches per chat the forecast
becomes **$1.25/month**. This is a stated usage assumption, not a hard application
spending limit or a promise that every month stays under $1. Rejected models
are not adopted on price alone. [Forecast and assumptions](../evaluation/round3_monthly_forecast.json).

An earlier controlled rendering benchmark reduced median render-search time
from 4.468 to 1.110 seconds (75.2%), and browser CPU from 5.335 to 1.479 seconds.
That is **rendering only**; the production sheet pair shows no whole-flow speed
win. Historical production first-content latency, per-request server CPU/RSS
and remote model CPU are unmeasured. Native local process measurements retain
scope labels. [Controlled benchmark](../evaluation/round2_comparison_results.json).

## Verification and remaining limits

The isolated full backend suite passes **2181 tests + 973 subtests**, with
38 skips and no failures. The clean image deploys successfully; health confirms
canonical/retrieval databases, and worker readback confirms v5 layout and Luna.
Both capped windows close with normal-key restoration verified. No frontend or
voice change is selected in this round.

Remaining work is concrete: broad-answer support/coverage, incomplete course
sources and TrueTime evidence, sheet content warnings, unrelated chat figures,
and interview reasoning consistency. Those need further measured experiments;
they are not fixed by changing a model indiscriminately. LangSmith's monthly
unique-trace quota still blocks hosted readback. Local SDK intervals, captured
outputs and provider bills remain available, but they do not establish new
hosted trace delivery. No manual review is required to close this round.
