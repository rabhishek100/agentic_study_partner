# Twenty-candidate evaluation plan

Status: designed, not executed. Updated 2026-10-03. No paid requests, runtime
changes or deployment were made for this design. The previous round's
$3.904616935 is separate from the **new $5 ceiling**, which includes the final
production comparison. Automated LLM review replaces required human review.

## Goal and fixed controls

Find better answers, more reliable grading and more readable sheets without
unnecessary calls or a materially higher bill. Test one change at a time; only
combine candidates that survive individual checks. A model's advertised price
or benchmark score does not establish its quality, speed or total cost here.

Start-of-round code control: `aefe246ec6c196c16148af80170b7842d39006e9`.
This contains the selected round-two components and restored original interview
grader. It has not had a fresh combined 54-case paid evaluation. Production
health was read back on 2026-10-03: API `0983a1c`, healthy canonical and retrieval
databases. Production has not received the round-two changes.

Freeze the [54-case manifest](../evaluation/five_flow_manifest.json), canonical
source/build bindings and external `artifact-review-v3` reviewer. Give changed
prompts, schemas, models and derived caches their own provenance versions.
Preserve original outputs, unsuccessful attempts and unknown judgments.
Keep the labelled general-knowledge fallback authorized for chat. A fallback
must never be judged as a source-backed answer merely because it is plausible.

The five special cases documented in [round-two results](evaluation-round2-results.md)
remain visible: three unavailable course sources, the older abstention
expectation and the uncaptured deterministic chapter listing. Use the same
49 eligible cases for the comparable headline; also publish all 54 outcomes.
Any new policy-aligned or source-ready cases form a separately versioned set.

## Candidate models

Prices below are USD per million input/output tokens, from the public
[OpenRouter model catalog](https://openrouter.ai/api/v1/models), captured
2026-10-03 09:09:56 UTC. They are screening information, not request estimates.

| Model | Input / output | Role in this plan |
| --- | ---: | --- |
| [GPT-6 Luna](https://openrouter.ai/openai/gpt-6-luna) | $0.10 / $0.50 | Existing control and fixed external reviewer. |
| [DeepSeek V4 Flash](https://openrouter.ai/deepseek/deepseek-v4-flash) | $0.028 / $0.056 on the catalog's promotional route | Text-only writer/router trial; eligible provider must be pinned. |
| [Qwen3.5 Flash](https://openrouter.ai/qwen/qwen3.5-flash-02-23) | $0.065 / $0.26 | Writer, native grader and sheet-author trials. |
| [Luna Pro](https://openrouter.ai/openai/gpt-6-luna-pro) | $0.10 / $0.50 | Small hard-question trial; reasoning can make the request much dearer. |
| [Gemini 3.1 Flash Lite](https://openrouter.ai/google/gemini-3.1-flash-lite) | $0.25 / $1.50 | Conditional writer/figure-reader trials under the monthly-cost exception. |

DeepSeek's StreamLake discount is promotional; other routes have different
prices. Validate its current endpoint, structured-output support and allowed
reasoning settings before spending. If the eligible route is unavailable,
record that candidate as blocked rather than silently choosing a dearer one.
Recalculate the monthly forecast without a temporary discount before selection.
Luna/Luna Pro also have higher long-context rates at 272,000 prompt tokens;
cache writes, reasoning, images and repairs belong in the request calculation.
Luna Pro's documentation explicitly warns of more reasoning tokens and slower
responses. Avoid treating identical token prices as identical bills.

Require compatible structured output, cap provider prices, record the actual
provider and prevent fallback outside the cost limit. Different model APIs may
need different supported reasoning settings; record those settings rather than
silently claiming identical configurations. Do not send images to DeepSeek.

## The twenty experiments

Each row is one candidate compared with the unchanged start-of-round control.
Rows 1–4 replace only the text answer writer, keeping retrieval, routing,
vision readers and native review fixed. Their small initial screen spans chat,
summary and transcript-based video/course answers; a surviving writer must
then pass every affected flow. Rows 5–8 change only the named stage.
No candidate is already a claimed improvement.

| # | Change to test | Why / decisive examples | Expected cost direction, to verify |
| ---: | --- | --- | --- |
| 1 | DeepSeek writes text answers. | Can a cheaper writer preserve explanations, source citations and qualifications? Definitions, broad audits, chapter/paper summaries, Spanner. | Lower token rates on an eligible route; reasoning/retries could erase savings. |
| 2 | Qwen writes text answers. | A second economical writer may follow completeness instructions more consistently. Same inputs as #1. | Lower token rates; measure complete workflow. |
| 3 | Luna Pro writes the difficult text answers. | Check whether extra reasoning helps the audit and paper limitations that ordinary writing misses. | Likely higher/slower; conditional, not a whole-app replacement. |
| 4 | Gemini Flash Lite writes text answers. | Test a different model family on remaining omissions, with identical source evidence. | Higher rates; conditional on the aggregate $1/month exception. |
| 5 | DeepSeek chooses the chat route and rewrites the question. | Routing is a small structured task; test cheaper control without losing scope, follow-ups or source intent. | Potentially lower; unsupported schema/settings block the trial. |
| 6 | Qwen grades interview answers. | Test weak, mixed and strong answers for fair scores, correct follow-ups and grounded recommended answers. | Potentially lower; external judge stays Luna. |
| 7 | Qwen drafts the revision sheet. | Test completeness and concise writing with Luna still reading figures, making the independent inventory and reviewing the PDF. | Potentially lower; extra repair passes count. |
| 8 | Gemini reads the original sheet figures. | Test whether important visual details are captured better before drafting. Use the identical original pixels and figure IDs. | Conditional; count image charges and keep all validation. |
| 9 | Preserve important original terms in rewritten questions. | Broad responsible-AI and data-leakage follow-ups sometimes search for the wrong concept. Protect terms without leaking earlier scope into a new topic. | No extra model call; bounded query size. |
| 10 | Add a small exact-title boost to BM25 ranking. | A neighboring section sometimes outranks the named audit section. Test named sections and unrelated-title controls. | No new provider call; fixed evidence allowance. |
| 11 | Share neighboring-context slots across the two strongest relevant sections. | One-section expansion fixes definitions but can miss a second part of a checklist. Test audits, model cards and multi-part questions. | Fixed total 5/8-item evidence cap; tokens still measured. |
| 12 | Test the existing lexical + vector search, without the unpriced reranker. | Paraphrased questions may miss evidence that exact keyword search finds. Compare with BM25 on the same corpus and final context cap. | Adds priced query embeddings; only if compatible derived vectors exist. |
| 13 | Include adjacent transcript passages around a course hit. | Spanner answers miss throughput, write-path and log-ordering details just outside the selected excerpt. Keep selected lectures and timestamp boundaries. | More evidence tokens, bounded by the cost gate. |
| 14 | Give summary sections an explicit share of the existing answer budget. | A summary can cite every section yet omit mechanisms or paper experiment conditions. Allocate essential explanation before optional examples/advice. | Same call/output ceiling; source content stays complete. |
| 15 | Separate essential conditions/limitations in the sheet's source inventory. | AutoML qualifications can disappear even when the main concept is included. Carry cited conditions explicitly into printed notes and review them. | Same inventory/draft calls; schema/output growth and repairs measured. |
| 16 | Choose sheet layouts by measured page fullness as well as fit. | Passing clipping checks still leaves uneven pages. Prefer balanced layouts while preserving source order, every item and the page ceiling. | No inference call; measure extra browser CPU/time. |
| 17 | Increase the sheet citation font floor. | Existing source citations can be too small to read comfortably. Re-render identical saved content; do not reduce main text or omit notes to fit. | No inference call; verify page count, fit and readability. |
| 18 | Reuse verified figure readings and source inventories for the same source. | Repeated demos should not pay to inspect unchanged images/content again. Cold generation still must pass independently. | Lower on repeated use; no promised cold-run saving. |
| 19 | Make the interview grader assess the requested points before scoring. | The rejected generic grading instruction over-credited mixed answers. Test an internal structured account of what was asked, what the candidate actually said and what evidence supports it. | Same grading call; additional output must fit the cost gate. |
| 20 | Give ideal interview exchanges concrete, source-supported reasoning guidance for the planned question type. | Generic dialogue instructions produce inconsistent engineering judgment. Test decision/consequence, comparison or diagnosis exchanges, as appropriate to the source. | Same exchange calls and word limits; repairs measured. |

Implementation boundaries: #9 in `study/analyze.py`; #10/#12 in retrieval;
#11 in `study/query.py`; #13 in `video/course_retrieval.py` and its evidence
formatter; #14 in `study/summarize.py`; #15 in `revision_sheets/review.py`
with matching composition/coverage contracts; #16/#17 in
`revision_sheets/html_render.py`; #18 around the existing sheet preparation;
#19 in interview grading prompts/contracts/evaluation; #20 in
`interviews/ideal_generation.py`. Model trials need explicit stage-specific
client injection, especially sheet author versus inventory/figure reader.

For #18, keys include verified owner, source/build hash, original image hashes,
model/provider/configuration and prompt/schema version. Cache only validated
derived content; rebuild from canonical source on any mismatch. Never reuse
another user's artifact. Cached inventory does not replace final source/PDF
review. For #19, private expected points that the question never asked cannot
become required answers; unasked details belong in an optional next question.
For #20, no invented numbers, failure modes or tradeoffs just to fill a template.

## Monthly cost rule

Confirmed workload: **100 chats + 5 summaries + 3 sheets + 2 complete interview
sessions + 20 video/course questions**. Voice is separate. Size adaptive
interviews initially at 30 minutes and up to 20 answered questions each,
including planning, grading, follow-ups and final reporting. Also measure ideal
generation across the complete selected chapter; use the more expensive of
the two types when forecasting the two sessions. These are explicit sizing
assumptions, not an assertion that every possible interview has this bound.
Record actual source sizes and turn counts in the production comparison.

Adopt a change only when it improves the targeted quality or saves cost/time
without weakening quality, and meets either:

1. Complete affected-flow cost and weighted monthly cost are each at most
   **120% of the fixed start-of-round control**; or
2. Where the percentage increase is large because the original is very cheap,
   the **whole workload above remains at most $1/month**, with 20% headroom
   included in that forecast. This is one aggregate ceiling, not $1 per flow.

Recheck the final combination against the same original control. Do not allow
a sequence of 20% increases to compound. Include routing, embeddings, image
reads, inventory, generation, native reviews, bounded retries and unsuccessful
attempts. Report cost per attempted and successful result. Count cold sheet
generation and cold answers in the conservative forecast; show warm-cache
savings separately. Price promotions cannot be the only reason a setting is
declared affordable for future months.

Calculate the profile as `100*C_chat + 5*C_summary + 3*C_sheet +
2*C_complete_interview + 20*C_video_course`, then add the forecasting headroom.
Use observed expensive representative source sizes and measured bounded retry
costs, not just the cheapest short-case average. Publish per-flow estimates
and the aggregate; no monthly total has been measured for this plan yet.

The offline external judge is part of the $5 experiment bill, not ordinary
monthly usage. Provider AI charges are distinct from hosting, voice and
one-time source ingestion/index rebuilds. Those separate costs must be named
in the report, not hidden or represented as zero. A forecast is not a hard
monthly account spending limit or a guarantee for larger sources/longer use.

## Execution and $5 allocation

| Step | Scope | Maximum allocated spend |
| --- | --- | ---: |
| 0 | Free compatibility, source, pricing and telemetry preflight; resumable round ledger | $0.00 |
| 1 | Screen up to 20 candidates on a few relevant cases each; reuse unchanged outputs | $1.25 |
| 2 | Repeat the best candidates on hard cases and passing controls; independent-model review of selected disagreements | $1.00 |
| 3 | Fresh combined control and finalist on all 54 cases, including generation and fixed LLM review | $1.50 |
| 4 | Paired real-account production measurements before and after deployment | $0.75 |
| 5 | Shared contingency for extra repairs, price variance or unresolved billing | $0.50 |
| | **Hard round ceiling** | **$5.00** |

These are allocations, not promises that every candidate completes. Previous
full-run receipts make two final runs within $1.50 plausible, not guaranteed.
Stop before a request whose conservative reservation cannot fit. A skipped
candidate or incomplete comparison is recorded explicitly; do not exceed $5
to obtain a complete-looking table. Keep each individual eval run within the
existing $1–$2 ceiling and the smaller available round allocation.

Build a small serial parent ledger around the existing per-run budget guard.
Reserve child-run allocations before launching; reconcile every request and
return only settled unused capacity. An interruption or unknown receipt holds
its reservation on resume. Include all paid requests in production as well as
local generation, reviews, embeddings and repairs. This parent ledger is not
implemented yet. Do not run independent child budgets that can sum beyond $5.
Image calls reserve a conservative upper bound that can exceed their actual
bill; phase-sized child budgets must permit that reservation rather than
assigning an unrealistic $0.05 ceiling to each figure experiment.

Start with free retrieval/render replays and inexpensive text trials. Screen
with relevant slices of the frozen cases, including all preceding turns in a
conversation. Carry promising candidates forward only after repeating their
known failure cases and passing controls, aiming for three fresh attempts per
target where the budget permits. Expensive conditional trials come last in
screening; they cannot consume the reserved final/production budget. If actual
spend needs a different allocation, record the revised allocation before the
next request and preserve the total ceiling and production reservation.

Use the fixed Luna reviewer against original source evidence; PDF reviews
include original figures and rendered pages. Review a small common set with
a different model to expose judge disagreement, with neutral output IDs and
counterbalanced presentation. Preserve both judgments. An unresolved factual
or grading disagreement is uncertain, not automatically a win. Human review
is optional and no human calibration is claimed.

For the combined finalist, verify source support, expected content coverage,
citations, refusal/fallback labels, source/owner isolation, conversational
history, flow completion and sheet text/image preservation. Interview checks
include fairness and follow-up routing; video checks include original visuals
and timestamps. Any new source/owner leak or fabricated citation blocks
selection. Add semantic regressions for real changed behavior, run relevant
tests after each coherent change, then run the retained full checks before
deployment. Commit/push each verified unit and update the tracker.

A quality case clears the existing combined threshold only when generation
and all non-diagnostic checks succeed, source support is established, every
external quality score is at least 3/4 and any sheet is readable. A cheaper
candidate must preserve those outcomes; a quality candidate must improve its
targeted failures without a confirmed regression on passing controls. Repeat
any apparent regression before selection, within budget. The combined
selection must preserve overall and per-flow outcomes on the comparable set;
otherwise retain the original component for that flow. Small samples do not
establish a population accuracy or reliability percentage.

## Measurement prerequisites

- LangSmith rejected the last six grading checks because the account's monthly
  unique-trace quota was exhausted. Check current free-account availability
  before claiming new hosted delivery. Do not purchase an upgrade. Tests can
  preserve local evidence while delivery is unavailable; complete hosted
  end-to-end tracing acceptance remains pending until readback succeeds.
- Forty-seven prior LangSmith costs disagree with provider receipts, although
  tokens match. Investigate using existing saved evidence first. Provider
  receipts are the spending authority; preserve both measurements and label
  any remaining difference. Do not rewrite the old traces into agreement.
- Production uses `hybrid_rerank`; the previous budgeted book tests used BM25
  because reranker pricing was not guarded. Establish a priced, bounded path
  before any paid production request that can invoke reranking. If pricing
  cannot be bounded, that production cost comparison is blocked; switching
  the old control to BM25 would not be an unchanged production baseline.
- Check published course evidence and compatible vectors before related
  trials. An unavailable lecture is a source-readiness problem. Do not spend
  on a retrieval experiment expecting it to repair absent source content.

## Before/after report in the real account

The report has two comparisons, because they answer different questions:

| Comparison | Evidence and limits |
| --- | --- |
| Before eval work began → final selection | Use repository history for the pre-eval implementation, and saved first-round outputs as the earliest measured quality baseline, explicitly labelled as such. Historical code anchor `f705751` precedes the Luna-default switch `6c5e5bb` and eval-plan commit `590c70b`; label this anchor explicitly. Four subsequently added cases cannot be counted as old cases. An isolated old-code replay, if affordable, is labelled replay rather than historical production traffic. |
| Current production → final deployed production | Same verified account, canonical sources, queries and conversation histories. Record old live API/worker/model configuration before deploying. Then repeat the same requests after deploying the verified finalist. Existing prior production acceptance is supporting evidence, not a substitute for matched pairs. |

Compare old quality outputs only on shared criteria with the same external
reviewer. Reuse existing matching reviews; otherwise rejudge the preserved
outputs within the round budget or mark that comparison incomplete. The first
measured evaluation is not proof of the exact pre-chat production behavior.

Prepare production inputs before screening so the old live measurement can be
captured within its reservation before rollout. Use one representative case
per flow, hard-case checks where affordable, and two/three interleaved repeats
for short chat/course requests if budget permits. Include one unseen
paraphrase per relevant flow to check transfer beyond the fixed eval questions.
Long summaries, sheets and complete interviews may have only one pair: mark
that sample size and do not present its timing as a reliable percentile.

Report quality/omissions, actual full-request cost, tokens, total completion
time, time to first visible text where streaming exists, queue wait, rendering
time, local worker/browser CPU and peak memory when measured. Server CPU is
not the remote model's CPU. Identify where each chosen setting helps, fails
or leaves an uncertain result. Give exact source sizes, session lengths,
model/provider versions, cache state and configuration for every pair.

Use LangSmith for connected AI timing/token traces when hosted delivery is
available, Grafana for API/worker/queue timings and resources, and provider
receipts for billing. CPU/memory observations under live production traffic
are descriptive unless isolated; a shared service's aggregate CPU does not
prove one request used that CPU. Link an example complete tree, API trace,
log record and saved artifact per flow, with verified user identity and no
credentials in public artifacts.

Count cache misses and cache hits separately. Opening an already-generated
ideal interview is a cache-hit demonstration, not a fresh-generation speed
test. Preserve original input/configuration/output hashes; skip or explicitly
rebind a pair if its canonical source changed. Avoid background experiment
overlap during timing. Do not disable ownership or deploy old buggy code into
production for the historical comparison.

Actual pre-chat production latency and billing were not comprehensively saved.
Where those observations are absent, say **unavailable**. Show historical
recorded receipts, replay measurements and current-price forecasts in separate
columns. No fabricated past production speed/cost percentages. Existing
render-only speed gains must not become whole-flow speed claims.

## Resume and approval boundary

Next action is plan review. After go-ahead: preflight/ledger → independent
screens → repeat/select → combined suite → bounded old-production measurement
→ deploy → matched production recheck → final report. Runtime implementation,
paid trials and deployment have not started for this round.

Each checkpoint records commit, candidate ID, changed stage, source/manifest/
prompt/model/provider/reviewer versions, private bundle location, actual spend,
unsettled reservations, next action and test results. Private sources, prompts,
images and provider captures stay in ignored `evaluation/runs/`; publish only
sanitized hashes, measurements and findings. Resume matching bundles instead
of regenerating paid outputs. The new public catalog snapshot is informational
only; revalidate current endpoint prices before execution.
