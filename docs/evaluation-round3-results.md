# Round-three evaluation checkpoints

Execution is in progress under the approved [twenty-candidate plan](evaluation-round3-plan.md).
The new $5 includes production comparisons. No production deployment has been
made in this round. Automated model review is used; no human calibration is
claimed. This file records completed checkpoints, not a final selection.

## Free rendering screens

Six identical saved sheets were rendered with their original source/scope
titles, original figure pixels and unchanged generated content. The controls
use the current renderer. Every candidate still passes its existing clipping,
page ceiling, image loading and text-preservation checks. Provider spend: $0.

| Candidate | Observation | Decision |
| --- | --- | --- |
| 16: expanded first-page note choices | Additional 10/12-note choices select exactly the same layouts and scores on all six sheets. | Reject this variant: more layout attempts without a measured benefit. Page balance remains an open problem. |
| 17: citation font floor | Citation text measures 9 points in controls and approximately 10 points in candidates. All six keep identical extracted text and the same 3/4-page counts. | Carry forward for combined PDF review; no content or page-count cost in this sample. |
| 18: additional preparation cache | `revision_sheets.store.enqueue` already returns the same owner's complete saved sheet when scope, canonical source fingerprint and configuration match. This screen is code-path analysis, not a paid cache benchmark. | Do not add another cache for normal repeated opens; no additional production benefit established. Explicit regeneration remains separately scoped. |

The single render-search times are descriptive and may overlap independent
model calls; no CPU or whole-flow speed improvement is claimed. Public
measurements/hashes: [round3_free_render_results.json](../evaluation/round3_free_render_results.json).
Original PDFs/HTML, image captures and the reproduction script remain under
ignored `evaluation/runs/round3/free-render/`. An earlier exploratory render
with substituted source labels remains separate and is not this evidence.

## Model screens and remaining work

Eight initial model-role screens completed at `c2d0ee9`. Provider receipts total
**$0.135548114**; unsuccessful requests retain **$0.039133014** in unresolved
reservations. Conservative round commitment for these eight screens is
**$0.174681128**. These amounts exclude the separate fresh control now running.
Unresolved reservations are not zero charges or confirmed actual bills.

| Candidate | Initial observation | Next decision |
| --- | --- | --- |
| 1: DeepSeek writer | Four outputs complete. Audit and course coverage remain weak; attention answer has a source-support failure; paper summary clears the score floor. | No default switch; cheap token prices do not compensate for these gaps. |
| 2: Qwen writer | Four outputs complete. Audit/course answers lack enough evidence, paper coverage is incomplete and the attention answer has a support failure. | No default switch on this evidence. |
| 3: Luna Pro writer | Two hard outputs complete; audit scores 4/3 and paper summary 3/3 for correctness/coverage. | Compare with matched Luna control and actual generation costs; no isolated gain established yet. |
| 4: Gemini writer | Four outputs complete. Audit and attention clear the score floor; full paper and course coverage remain weak. | Only consider a narrowly scoped use after cost/repeat checks. |
| 5: DeepSeek router | Three dependency-chain outputs complete; quantization fails schema validation, ambiguous-reference turn fails with upstream overload and its follow-up cannot run. | Reject this router trial; keep schema validation and original failures. |
| 6: Qwen grader | All six initial structured requests fail; no quality score is inferred. | Test a necessary provider-format correction before judging model quality. |
| 7: Qwen sheet author | Figure/source preparation runs; author request fails because upstream JSON mode requires the word JSON in messages. | Correct the format hint, then retest independently with original attempt preserved. |
| 8: Gemini figure reader | Paper sheet completes and is supported, with correctness/coverage 4/4. | Compare full-flow cost and quality with the fresh Luna control; one passing sample is not a gain. |

Public scalar evidence: [round3_model_screen_results.json](../evaluation/round3_model_screen_results.json).
Actual prompts, outputs and provider error details remain private. A provider
compatibility correction now adds a format-only JSON hint for Qwen structured
clients while retaining the exact schema and native validators. It adds no
model call; other models retain their existing client. A real SDK/mock transport
test reproduces the missing-hint rejection and verifies successful parsing
with the hint. Original failed paid attempts will not be overwritten.

The fresh 54-case Luna control uses the frozen `c2d0ee9` attached checkout so
later candidate code changes cannot alter its generation. All paid children
share the same round ledger and run serially. Control timing cannot be read
back from LangSmith while its quota is exhausted; do not substitute inferred
hosted timings. If a conservative image reservation blocks the individual
control ceiling, keep its completed outputs and record the incomplete state.

Next: publish settled model findings; run independent retrieval/completeness/
grading candidates; repeat promising settings; run a fresh control/finalist on
all 54 cases; measure current production before rollout and the final deployed
version afterward. Model compatibility is a selection criterion, not a reason
to quietly change deadlines or remove schema validation.

LangSmith's zero-inference preflight still fails with the monthly unique-trace
quota error. Hosted timing/token trees remain unavailable for new runs. Saved
outputs, requests and provider receipts support quality/spend observations;
they are not a substitute for successful hosted trace readback. Old trace-cost
discrepancies remain preserved. Reranker pricing is published at
[$0.0025/search](https://openrouter.ai/cohere/rerank-4-pro), while its endpoint
metadata reports zero token prices; token metadata cannot be used as a zero
search charge.

## Isolated flow experiments

Candidates 9–11, 13–15 and 19–20 now have opt-in evaluation implementations;
12 uses the existing hybrid retrieval mode. They are not application defaults.
Each process can enable only one candidate, and its identity plus implementation
hash is recorded with the immutable run. Native ownership/build checks and the
fixed external reviewer remain in place. The screen runner now supports distinct
attempt suffixes so protocol corrections/repeats cannot overwrite prior results.

The grader experiment adds an internal account of requested points and literal
candidate quotes in the existing call. A fabricated quotation is rejected before
returning the original public grading contract. It does not credit private
expected points that were never asked. The sheet-inventory experiment requires
an explicit source-supported qualification, allowing an empty value when the
source states none. These are hypotheses awaiting paid results, not improvements.

### Budget checkpoint: full control continuation

The first control child completed 43 cases and stopped before an image request
at its original $0.75 ceiling. Settled receipts: $0.429622960, zero unresolved
request reservations. The blocked sheet has no final artifact; its partial
preparation charges remain in the ledger. The eleven unfinished cases will run
in a separate, registered continuation from the same frozen code and sources;
completed outputs will not be regenerated or overwritten.

Before continuation, the approved $0.50 contingency moves to final evaluations.
Effective allocations are screening $1.25, repeats $1.00, final $2.00 and
production $0.75. The total remains $5 and every child remains at most $2.
The parent ledger preserves the original allocations and an explicit transfer
history; transfers cannot consume active leases or unresolved charges. The
separate continuation is disclosed rather than presented as one uninterrupted run.

### Qwen grader protocol retest

The first JSON-hint retest received six billable responses, but all six failed
the unchanged grading structure. Therefore it is not a successful grader trial.
Qwen structured clients now also include the exact Pydantic/figure-constrained
JSON schema in the format instruction, so translation into upstream JSON mode
cannot leave the model without its required contract. This adds input tokens,
which must be included in affordability checks. The prior six responses were
not captured and their exact malformed structure is unknown; no quality verdict
is inferred from it. A new immutable attempt will test the complete schema hint.

Budgeted calls now preserve private response bodies and hashes without headers.
Missing usage still holds its original reservation. This permits diagnosis of
future rejected or malformed responses without paying to reconstruct them.

## Text-flow screening checkpoint

Candidates 9–12 did not repair the broad audit in their first native slices.
The exact-title boost also reduced quantization-definition coverage from 4 to
2 and the audit from 2 to 1. These are rejection signals, not reasons to add
another search layer. Candidate 13 retains the timestamp/source controls but
Spanner coverage remains 2/4; an improved score on the source-incomplete course
case is not counted as a comparable gain. Candidate 14 raises paper correctness
and usefulness to 4/4, with coverage still 3/4; the chapter control stays 4/4.
A repeat and cost check are required before retaining it.

Candidate 19 passes all three proximity cases, has uncertain source judgments
on two RAG cases and rejects a strong-answer response whose quotations are not
literal candidate text. Candidate 20 has supported, well-covered answers but
fails the existing interview-reasoning signal check in both cases. Neither is
selected on this evidence. Complete matched control grading/ideal results are
still pending in the control continuation. Preserve strict validators and both
uncertain judgments.

The isolated full backend run found two checks to resolve: the image omitted
`model_routing.py`, and the default-model scan treated the explicitly approved
experiment script as production defaults. The Docker import is included now;
only that non-shipped experiment script receives the exploratory model allowlist.
The application default allowlist remains unchanged.

## Completed initial screens and shortlist

All twenty approved hypotheses have an initial result: seventeen native paid
candidate screens, plus the three free rendering/cache checks. Separate Qwen
protocol attempts are retained under the original hypotheses, not new candidates.
Initial screens alone are not the final comparison or a production rollout.
Scalar case checks, judgments, output/source hashes and generation/review receipts
are in [round3_screen_results.json](../evaluation/round3_screen_results.json).
That file is a checkpoint and clearly identifies the incomplete control.

The strongest new candidate is **8, Gemini for original figure reading**. The
paper sheet passes its native findings check and external 4/4 review; the Luna
control has lingering native findings despite an external 4/4 review. This
single native run uses five generation requests costing $0.016925400 versus
ten costing $0.036443445 in control. Fewer downstream repairs may explain the
saving; stochastic composition also varies, so this is not an isolated causal
estimate or evidence for every sheet. Test all six sheets and repeat the paper /
large chapter before choosing it.

Carry forward **14, summary budget allocation**, for matched summary repeats,
and **17, larger citations**, for combined PDF validation. Keep **3, Luna Pro
writer**, conditional until its targeted gain is replicated and whole-flow /
monthly cost is bounded. No default model switch is selected. Qwen's sheet
draft can be structured, but its repair calls return invalid structures and
the native flow fails. Reject that author trial; do not disable repair checks.

New evaluations also save the root interval already produced by the LangSmith
SDK, separately labelled `local_langsmith_sdk_run_tree`. This permits future
matched local timing observations during the hosted quota outage. It is not
hosted trace delivery, first-token timing or billing, and it does not recover
timings for earlier runs. Hosted metrics remain unavailable until actual
readback succeeds. No speed improvement is claimed from these initial screens.

The next repeat block adds a fresh matched Luna control over all seven summaries,
the two difficult sheets and hard/passing chat controls. It then tests Gemini
figure reading on all six sheets, two independent summary-allocation repeats,
and hard-sheet / Pro-writer repeats. All remain the original hypotheses; model
roles are isolated and failed outputs remain visible. After the control-tail
lease finishes, move $0.50 of unused screening capacity into repeats if the
reconciled ledger permits it. That changes effective phases to screening $0.75,
repeat $1.50, final $2 and production $0.75, still exactly $5. Record that transfer
before any repeat call, not by editing existing child ceilings.
