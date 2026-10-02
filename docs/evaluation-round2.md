# Five-flow quality and speed comparison

Started 3 October 2026. Resume alongside `evaluation-progress.md`.

## Fixed comparison

Run the same 54 cases (20 chat, 7 summaries, 13 video/course, 6 sheets,
8 interviews), canonical sources, expected points and Luna artifact reviewer.
Each experiment includes generation, repairs and judging under a $2 ceiling.
Use separate immutable private run directories; publish only sanitized results.
Human review remains optional. Model judgments are not independent human evidence.

The production reranker endpoint remains unpriced by the evaluation budget
guard. All three comparison variants therefore use **BM25**, current native
application workflows and real providers against the existing local source
corpus. This is a quality comparison, not an exact production retrieval rerun.
Do not change deployed retrieval defaults or weaken the budget guard.
Record unavailable course evidence separately; do not fabricate transcripts,
publish unprocessed lectures or replace failed cases with easier questions.
The existing source-only LoRA expectation stays immutable; the user's retained
labelled-general-knowledge policy is disclosed separately from grounded quality.

## Units and acceptance

| Unit | Change | State | Acceptance |
| --- | --- | --- | --- |
| 15 | Freeze comparison and fresh current-code baseline | In progress | All 54 attempted; explicit failures, receipts, trace metrics and per-flow findings |
| 16 | Quality candidate | Prepared; live comparison pending | Address measured missing details, exact claim citations, summary/sheet completeness and justified interview feedback; meaningful regression tests; unchanged-case full rerun |
| 17 | Speed candidate | Prepared; live comparison pending | Target measured repeated work; preserve dependencies, source isolation and bounded repairs; compare all 54 to quality winner |
| 18 | Selection and final verification | Pending | Per-flow before/after evidence; repeat material regressions or close results; full relevant contracts/journeys; commit/push retained changes |

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

Source readiness, model configuration and results will be recorded below after
the baseline. Existing operational telemetry and user identity work is complete;
this round changes evaluation/quality behavior rather than hosted setup.

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
context-cleanup regression. Full live five-flow speed comparison remains pending.
