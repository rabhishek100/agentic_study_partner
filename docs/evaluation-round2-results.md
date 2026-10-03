# Round-two evaluation results

Selection: retain the tested core changes from `754681b` and restore the original `adaptive-interview-v13` grading prompt. The added grading instruction did not show a reliable benefit, so it is rejected. Keep the corrected external LLM judge (`artifact-review-v3`). This is a per-component selection, not a universal quality win or a production rollout.

Five versions each attempted the same 54 cases, followed by nine fresh repeat cases and six native grading checks after restoring the original prompt. Total reported provider spend: **$3.904616935**, including original generation, repairs, judging, corrected baseline review and repeats. Each full experiment stayed under its $2 ceiling; the repeat, corrected review and selected grading validation each stayed under $1. No unsettled receipts remain.

Human-review replacement is complete. Luna judges the generated outputs against the same expected points and original evidence; PDFs include rendered pages. These are automated judgments, without human calibration. Baseline outputs were preserved and 53 captured outputs were rescored with the same corrected judge used for the candidates. The deterministic chapter listing has no generation capture and remains source-support-unverified.

## Results across the five flows

A case clears the combined threshold only if generation completed, every non-diagnostic check passed, the judge found source support, every quality score was at least 3/4, and sheets were readable. A low score, unknown source support, failed generation or failed metadata check remains visible. This is a small fixed test set, not an estimate of accuracy for all users.

| Flow | Baseline cleared | Selected components cleared | What the comparison supports |
| --- | ---: | ---: | --- |
| Chat | 13/18 | 17/18 | Fuller section context helps definitions/checklists; query-rewrite and follow-up variability remain. |
| Summaries | 6/7 | 6/7 | Already strong; retain the completeness instruction without claiming a reliable broad gain. |
| Video/course study | 7/10 | 7/10 | Incomplete selected passages still limit answers; removing preview truncation did not solve all omissions. |
| Revision sheets | 3/6 | 5/6 | More outputs clear review, with bounded image validation/recovery and unchanged rendering checks. Semantic completeness remains variable. |
| Interviews | 6/8 | 6/8 | Reject the added grading instruction. Original prompt restored; fresh validation still has one routing failure and one uncertain source judgment. No overall quality gain claimed. |

Selected counts use the recovery run for unchanged flows and ideal generation, and the six fresh checks for the restored grading prompt. This is explicitly a component selection; a new full 54-case run of the combined code was not performed. File-hash reconstruction verifies that only `interviews/prompts.py` differs from the frozen recovery code and exactly matches the baseline. All 54 original results are retained. The quality comparison uses the same 49 eligible cases in every version: three course cases need unavailable lecture sources; one case expects abstention despite the authorized labelled general-knowledge fallback; one deterministic hierarchy response lacks a generation capture. Those five are reported separately rather than turned into improvements. Exact rewrite-text equality is a diagnostic; history/scope/citation checks remain part of the combined threshold.

## Changes retained and repeat evidence

| Change | Evidence | Remaining limit |
| --- | --- | --- |
| Bounded neighboring section chunks | Quantization coverage 2/4 → 4/4 in both final versions and the fresh repeat. Model-card coverage 1/4 → 4/4 in both final versions and the repeat. | The broad audit reaches 4/4 in the two full trials, then drops to 1/4 in the repeat because a neighboring section ranks first. Expansion is bounded to the strongest section, not every possible section. |
| Citation metadata after shortening | Metadata now retains only the original source bindings for markers actually printed. Both citation styles have output-based regression checks; the stale-metadata failure in the first candidate is preserved. | A valid locator still does not prove that a claim is supported; the separate judge checks source support. |
| Safer sheet composition and image handling | Recompose when missing essential concepts cannot fit an edit-only repair. Draft/patch schemas constrain selectable figures to inspected IDs. Incomplete image inventory gets one repair using the identical original images; persistent omissions fail. | The first two candidate runs each had one generation failure. They remain failures, not overwritten retries. Bounded recovery is covered by semantic regressions; this sample does not establish a general failure-rate reduction. |
| Reuse Chromium within one layout search | Three controlled alternating repetitions of ten identical layouts: median 4.468s → 1.110s, **75.2% faster rendering**. Text, page geometry and selection scores match exactly. Median browser-child CPU 5.335s → 1.479s. | Rendering only; no 75% whole-workflow latency claim. Every attempt still uses a fresh context with network and JavaScript disabled. |
| Completion prompts and corrected external review | All 54 unchanged cases evaluated per version; external review distinguishes candidate input from grading output. | Summary/course gains are not established. Native grading addition rejected; measurement correction is not a generation improvement. |

Section recall alone hid these omissions: the expected section was found (recall 1.0) even when its definition/checklist continuation was absent. The evidence pack must contain the required passage, not just one hit in the right section.

## Execution and spend

| Version | Completed / attempted | Preserved initial failures | Reported USD including review |
| --- | ---: | --- | ---: |
| baseline (`d452eb7`) | 54/54 | None | $0.836918250 |
| quality (`f1e33f4`) | 53/54 | sheet-chapter-10 | $0.619649580 |
| refined (`6149df3`) | 53/54 | sheet-chapter-8 | $0.539410670 |
| final (`cda092f`) | 54/54 | None | $0.739770440 |
| recovery (`754681b`) | 54/54 | None | $0.610470125 |

Additional corrected baseline review: $0.523331350; nine-case repeat: $0.026569420; six-case selected grading validation: $0.008497100. Original baseline judgments and output hashes remain preserved. Failed-case LangSmith timing/token/cost were recovered from trace sidecars without changing the failed outputs; their process CPU/RSS remains unknown.

All five full trials have trace-backed timing/token/cost observations, including recovered failed-case sidecars. Token counts reconcile with captured receipts for all 270 initial cases. **47 case costs do not reconcile**: Observed LangSmith generation costs are lower than the corresponding provider receipts. Their cause is not yet established; the JSON preserves both amounts and exact differences. Use the provider-receipt ledger for total spend, and treat trace costs as unreconciled observations rather than exact billing. The final six grading checks lack hosted trace metrics: LangSmith rejected new ingestion with `Monthly unique traces usage limit exceeded`. Provider captures and receipt spend are preserved; unavailable trace metrics remain unknown. Resolve the account quota before claiming new production trace delivery; no plan upgrade or purchase was made.

LangSmith is authoritative for generation latency, physical model token usage and generation cost. Receipt ledgers include generation and review spend. Runs overlapped, so their timing is descriptive and cannot isolate prompt or rendering effects. Python CPU/RSS excludes child browsers and remote inference. Streaming first-content latency was not measured.

## Verification and production status

The selected code passed **2,118 backend tests and 972 subtests**, with 38 skips. The combined selected code was rechecked after the grading rollback. Relevant regressions reproduce missing preceding definitions, stale citation metadata, truncated course evidence, sheet recomposition, source-bound image schemas, bounded inventory recovery and browser cleanup. Graph/API reference checks passed. Run fingerprints, unchanged gold/source bindings/settings, saved-output hashes, exact budget sums and report aggregates were checked before publication.

Production was not changed in this round. These native evaluations do not establish API/browser, ingestion, microphone/playback or load-test acceptance. Books used the priced BM25 baseline; video/course retrieval retained its existing text/multimodal embeddings. The production reranker was not exercised by this budgeted comparison. Generation, native review and external review receipts use Luna; embeddings are separately accounted.

## Remaining improvements

1. Preserve the question's retrieval terms and source intent during rewriting, then test ranked-section stability against the broad-audit and data-leakage repeat failures.
2. Retrieve a bounded continuation around selected course passages so throughput and write-path details reach the answer; first ensure required lectures have published evidence.
3. Address sheet-specific omissions and qualifications, uneven page density and small citations without weakening the independent review or page checks.
4. Stabilize interview grading/routes and ideal-answer reasoning signals against the recorded weak/mixed-answer cases.

Also resolve the LangSmith account quota and investigate cost metadata/aggregation against the 47 preserved receipt discrepancies. Do not silently overwrite old trace measurements. These are remaining experiments, not completed fixes. Avoid adding another retrieval/model layer until a measured failure justifies it.

## Evidence and reproduction

Full sanitized measurements, all per-case scores/checks/hashes, special cases, repeat observations, costs and controlled benchmark: [round2_comparison_results.json](../evaluation/round2_comparison_results.json). The original baseline audit remains in [round2_baseline_results.json](../evaluation/round2_baseline_results.json). Private originals, prompts, source images and provider captures remain under ignored `evaluation/runs/`.

Run names and commits above identify the immutable local bundles and corresponding `study-partner-evals-<run-name>` LangSmith projects. Reproduce from the recorded commit with the same canonical source/build bindings, model settings and frozen `evaluation/five_flow_manifest.json`; always use a new output directory for changed code/settings. Example:

```sh
uv run --frozen --extra voice python -m scripts.run_evaluations --live \
  --output evaluation/runs/round2-new-comparison --retrieval-mode bm25 --max-usd 2

# Includes both four-turn dependency chains and the independent course case.
uv run --frozen --extra voice python -m scripts.run_evaluations --live \
  --output evaluation/runs/round2-new-key-repeat --retrieval-mode bm25 --max-usd 1 \
  --case mt-003-t4 --case mt-008-t4 --case course-spanner-indexed
```

To rejudge saved outputs, use `scripts.judge_saved_evaluations` with an explicit selection and new output directory. It verifies captures/output hashes and never regenerates the artifact. No human labels were manufactured.
