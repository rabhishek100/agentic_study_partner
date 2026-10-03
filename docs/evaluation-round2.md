# Five-flow quality and speed comparison

Started 3 October 2026. Resume alongside `evaluation-progress.md`.

## Fixed comparison

Run the same 54 cases (20 chat, 7 summaries, 13 video/course, 6 sheets,
8 interviews), canonical sources, expected points and Luna artifact reviewer.
Each experiment includes generation, repairs and judging under a $2 ceiling.
Use separate immutable private run directories; publish only sanitized results.
Human review remains optional. Model judgments are not independent human evidence.

The production reranker endpoint remains unpriced by the evaluation budget
guard. All three comparison variants therefore use **BM25 for books**, current native
application workflows and real providers against the existing local source
corpus. This is a quality comparison, not an exact production retrieval rerun.
Lecture/course retrieval retains its native text/multimodal embedding calls,
with the same source snapshots and model settings in each variant.
Do not change deployed retrieval defaults or weaken the budget guard.
Record unavailable course evidence separately; do not fabricate transcripts,
publish unprocessed lectures or replace failed cases with easier questions.
The existing source-only LoRA expectation stays immutable; the user's retained
labelled-general-knowledge policy is disclosed separately from grounded quality.

## Units and acceptance

| Unit | Change | State | Acceptance |
| --- | --- | --- | --- |
| 15 | Freeze comparison and fresh current-code baseline | Complete | All 54 generated/judged; $0.83691825; 54 trace latencies/token/cost measurements; no unknown receipts. Additional corrected review: 53 captured outputs, $0.52333135; no human review required |
| 16 | Quality candidate | Complete at `f1e33f4` | 54 initial attempts: 53 completed and one preserved generation failure; $0.61964958; no unknown receipts |
| 17 | Refined quality and speed candidate | Complete at `6149df3` | 54 initial attempts: 53 completed and one preserved generation failure; $0.53941067; no unknown receipts |
| 18 | Bounded section context, citation metadata and source-bound image IDs | Complete at `cda092f` | 54/54 generated and judged; $0.73977044; no unknown receipts |
| 19 | Bounded incomplete-image-inventory repair | Complete at `754681b` | 54/54 generated and judged; $0.610470125; no unknown receipts; bounded recovery and persistent failure verified |
| 20 | Selection and final verification | Complete | Retain core changes, reject added native grading instruction; nine fresh repeat cases plus six restored-grader checks; combined selected code passed 2,118 tests / 972 subtests; sanitized results published |

Quality comes before speed/cost. Confirmed new grounding, ownership or recovery
failures block selection. A global average cannot hide regressions in a flow.
The candidate may be retained for some flows and rejected for others. No new
provider/model/retrieval technique is required unless results justify it.

## Resume

Baseline directory: `evaluation/runs/round2-current-bm25-20261003`.
Use `scripts.run_evaluations --live --retrieval-mode bm25 --max-usd 2`.
The runner fingerprints application code/configuration and sources; finish a
variant before changing Python code. Saved outputs are never regenerated on
resume. Budget stops and unknown receipts must remain explicit.

This round is complete. Results, per-component selection, receipt budgets and
remaining limitations are in [evaluation-round2-results.md](evaluation-round2-results.md).
Private resumable run/session records remain under ignored `evaluation/runs/`.
Production was not changed. New LangSmith ingestion is blocked by the account's
monthly unique-trace quota; the six final grading validations have unknown hosted
trace metrics. All five full trials have trace observations, but 47 costs differ
from provider receipts and remain explicitly unreconciled. Existing operational
telemetry and user identity code is unchanged.

### Quality candidate prepared

- Preserve the complete bounded course retrieval passage rather than truncating
  it to a 700-character preview. The regression reproduces a missing mechanism
  and verifies selected-lecture isolation.
- For explicit book checklist questions, include the following chunk from at
  most two highest-ranked sections, within the existing retrieval item limit.
  The fresh baseline's model-card checklist is split across two chunks of the
  same section. Expansion stays within owner, book, section and source build.
- Version prompts to cover requested details and exact claim support, required
  summary sections, source limitations and justified interview feedback.
- Let sheet length follow essential source concepts within the existing page
  limit. Missing essential concepts trigger recomposition because the existing
  note patch cannot add a note or its coverage ledger. Retain bounded repairs.
- Correct artifact review to distinguish a weak candidate answer (input) from
  the grader feedback being evaluated (output). Rejudge frozen baseline outputs
  with `artifact-review-v3` before comparing; preserve their original v2 reviews.

Verification: 96 relevant application tests plus 26 subtests; 69 evaluation,
saved-review integrity, adapter and budget tests. Three new behavior regressions
were verified to fail on the original code and pass on the candidate. Candidate
is experimental until the complete live comparison and regression checks finish;
no production deployment or quality improvement is claimed yet.

The complete combined candidate passed 2,114 tests and 962 subtests (38 skipped).
Baseline generation and judging remained frozen until all 54 cases completed;
subsequent hosted trace refresh was read-only. Sanitized baseline evidence lives
in `evaluation/round2_baseline_results.json`; original private artifacts remain
immutable. Corrected review uses `round2-current-review-v3-20261003` for 53 outputs.
`mt-002-t1` is a deterministic hierarchy-list response with no generation-request
capture: preserve its deterministic checks, leave automated source support
unverified and exclude it from model-grounding improvement claims.

### Refined candidate hypotheses

The first quality trial restored the complete model-card checklist (coverage
1/4 to 4/4 on its initial comparison), but the broader audit remained incomplete.
Handle explicit "what should ... cover" requests and use at most two strongest
hits that actually have following chunks, within the unchanged item budget.
Repeat important checklist comparisons before claiming a robust improvement.

The paper sheet's production reviewer passed an artifact that the separate
gold-based judge still found incomplete: training data and optimizer schedule
were absent. Clarify the native inventory's distinction between optional
implementation minutiae and essential experimental conditions needed to
interpret/reproduce the paper's main results. Native rubric becomes
`revision-review-v4`; external `artifact-review-v3` criteria/gold stay unchanged.
This trial combines those quality refinements with the controlled browser reuse;
do not attribute end-to-end differences solely to browser reuse.

Refinement verification: 52 focused tests and 9 subtests; full suite 2,114 tests
and 964 subtests (38 skipped). The new "cover" regression fails against the first
quality candidate and passes with this refinement. Sheet recomposition now has
an output-based regression checking an added note/coverage ledger and the complete
new condition in the rendered PDF, while preserving existing notes.
Refined live directory: `evaluation/runs/round2-refined-bm25-20261003`.

### Final candidate from measured residual failures

The first quality candidate completed 53/54; Chapter 10 failed with an uninspected
figure ID. Its initial failure remains recorded, not replaced by a favorable retry.
The refined candidate restored the model card but the broader audit remained
incomplete. Canonical chunk inspection explains why: the ranked middle chunk
omits preceding bias categories; quantization's preceding chunk contains its
precision definitions and post-training/aware approaches.

- Replace forward-only checklist matching with one scoped query for the strongest
  section's nearest chunks. Complete sections of at most five chunks; otherwise
  keep a bounded neighborhood. Leave at least two retrieval slots for other ranked
  sections and keep the overall depth-specific evidence limit unchanged. Owner,
  book, node and build stay fixed. Pinned side chats and multi-chunk system-design
  retrieval retain their existing behavior. Trace this expansion as a named child.
- A shortened answer may remove an optional example and its citation. Filter
  returned citation metadata to markers actually present, retaining their original
  source/rank bindings; never invent a new locator. The first candidate's text kept
  three markers but returned four prior citation records, causing the check failure.
- Bind draft and patch image-selection schemas to IDs actually inspected for the
  selected canonical source. Empty scopes require an empty image list. Keep local
  validation; a provider ignoring the schema must still fail. Version sheet prompt
  provenance as `revision-prompt-v11`.

Two new behavioral regressions fail against `6149df3`: omitted earlier definition
and stale rewritten-answer citation metadata (both rank and node/page styles).
The context/metadata change passed 2,116 tests and 966 subtests; the image-selection
change passed 27 focused sheet tests and four transport-contract subtests checking
the actual SDK request schema. Combined verification: 2,117 tests and 970 subtests
passed (38 skipped), with generated graph/API reference checks. Final live run
follows. Final directory: `evaluation/runs/round2-final-bm25-20261003`.

### Incomplete image-reading recovery

The refined run completed 53/54: Chapter 8 failed before composition because the
visual reader did not return one inventory entry per original image. This is a
different stage from the first candidate's invalid selected image ID. Preserve
both initial failures in the comparison.

For an omitted/duplicated/extra image ID, retry the complete small batch once with
the same original images, expected IDs and prior inventory. Do not guess missing
descriptions or drop images to obtain a successful result. A second incomplete
inventory still fails. Ordinary valid batches add no calls; a repair call is covered
by the experiment's shared dollar ceiling and is visible in its trace/capture.
Prompt provenance becomes `revision-prompt-v12`. A transport/shape error retains
the existing error behavior; this recovery targets the measured ID-set mismatch.
Regression: omission followed by a complete result recovers, identical images stay
in both requests, and a persistent omission fails after exactly two calls. The
regression fails on `cda092f`. Combined checks and a full 54-case recovery run follow
in `evaluation/runs/round2-recovery-bm25-20261003`.

Combined recovery verification: 2,118 tests and 972 subtests passed (38 skipped).
The repair regression covers both recovery and persistent failure without dropping
an original image. The final bounded-context trial's initial quantization and audit
answers now cover 4/4, compared with 2/4 before; retain this as preliminary until
the full comparison and important repeat checks complete.

### Speed candidate prepared

Reuse Chromium startup within one sheet layout search. Each attempt still creates
and closes a fresh context with JavaScript and network access disabled. Keep every
layout candidate, overflow/text-preservation check and selection score unchanged.
Browser and context cleanup run even when an attempt fails. Rendering provenance
is `html-a4-flow-v4`; standalone rendering still owns its browser lifecycle.

Controlled synthetic fixture: three alternating repetitions of the same ten
layouts. Median layout-search time fell from 4.468 seconds to 1.110 seconds (75.2%).
Extracted text, page geometry and layout selection scores were exactly equal.
This measures rendering only, not total sheet generation or remote model speed.
Median browser child CPU time fell from 5.335 to 1.479 seconds in this local fixture;
existing suite process-CPU metrics do not include these browser children.
Verification: 26 sheet tests, including a real-browser overflow/recovery and
context-cleanup regression. Full live comparisons and selection are complete;
their overlapping generation latencies do not isolate rendering's causal effect.

### Final selection

Retain bounded section context, actual-marker citation metadata, source-bound
image selection, bounded inventory recovery and Chromium reuse. Quantization and
model-card completeness gains repeat; the broad audit still fails when a neighboring
section ranks first. Video/course completeness and sheet presentation gaps remain.

Reject the added `adaptive-interview-v14` grading instruction and restore the exact
baseline `adaptive-interview-v13` file. It showed no reliable benefit; the recovery
trial's mixed-answer grade omitted scoped gaps and its weak-RAG recommendation
included unsupported advice. Fresh selected-code checks: four of six cleared,
one routing/completion mismatch and one uncertain evidence judgment. Both ideal
generation cases retain their unchanged implementation and recovery-run observations.
The corrected external judge stays `artifact-review-v3`; no human review is required.

Total provider receipts: $3.904616935 across all five trials, corrected baseline
review, nine repeats and six selection checks. No unsettled reservation remains.
The final combined code was rechecked: 2,118 tests and 972 subtests passed, 38 skipped.
This is a component selection with six grading-specific delta checks, not a new
full 54-case run of the combined code. Original failures and all source/policy gaps
remain in the published comparison.
