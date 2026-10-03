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

Eight model-role screens are running through the shared round coordinator.
Wait for each child's final ledger before classifying unsettled reservations:
a request in progress is not a concluded billing failure. Preserve every
failed provider/schema/timeout attempt and dependent case that could not run.

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
