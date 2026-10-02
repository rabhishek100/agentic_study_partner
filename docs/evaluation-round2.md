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
| 16 | Quality candidate | Pending | Address measured missing details, exact claim citations, summary/sheet completeness and justified interview feedback; meaningful regression tests; unchanged-case full rerun |
| 17 | Speed candidate | Pending | Target measured repeated work; preserve dependencies, source isolation and bounded repairs; compare all 54 to quality winner |
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
