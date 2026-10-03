# Evaluation

Results are project-specific diagnostics, not general benchmarks. Evaluation
separates deterministic contracts, retrieved-source coverage, and generated
quality. Synthetic/model-reviewed data is not treated as human-verified gold.
Committed datasets and result artifacts are the evidence.

The shared runner evaluates chat, complete summaries, dedicated video/course
study, revision sheets and interviews. LLM review is the default; human
calibration is optional and has not been claimed. Quality, deterministic
contracts, hosted delivery and browser acceptance are separate evidence layers.
[Runner](evaluation-runner.md), [coverage criteria](evaluation-metrics-plan.md),
[verification checklist](verification-checklist.md).

## Book retrieval

[Gold set](../evaluation/retrieval_gold_seed.json): 15 cases—5 exact-term,
4 paraphrase, 3 multi-section, 3 unanswerable. Recall/MRR score the 12 answerable
cases; expected nodes were checked against canonical pages.

| Strategy | Recall@3 | Recall@5 | MRR@5 |
|---|---:|---:|---:|
| BM25 | 81.9% | 88.9% | 91.7% |
| Vector | 90.3% | 93.1% | 79.2% |
| Hybrid RRF | 84.7% | 93.1% | 84.7% |
| Hybrid + reranker | 88.9% | 100% | 95.8% |

The reranker had 100% candidate Recall@20; multi-section Recall@5 rose from
72.2% to 100%. This justified the default on this source, not every corpus.
[Comparison artifact](../evaluation/retrieval_comparison_artifact.json).

## Five-flow evaluation

[The manifest](../evaluation/five_flow_manifest.json) contains 54 cases: 20 chat,
seven summaries, 13 video/course, six sheets and eight interview cases. Native
book tests use BM25; the paired production checks use `hybrid_rerank`. Freeze
canonical sources, prompts/settings, output hashes and review version before
comparing variants. A completed request is not a quality pass.

Source-backed review checks correctness, coverage, support and usefulness;
sheets also require readable rendered pages. Original figure pixels are source
evidence, while generated previews are derived output. The reviewer sees all
PDF pages. Failures, absent sources and uncertain judgments stay visible.
Luna is the default judge; nine alternate-model blinded reviews preserve
disagreement without claiming human calibration.

An output is usable only with supported grounding, correctness/coverage/usefulness
each at least 3/4, no failed required contract check, and acceptable sheet layout.
Exact standalone rewrite equality is diagnostic. Missing support/scores or unknown
sheet layout stay uncertain; they are not converted into successful cases.

Code: [native flow adapters](../evals/adapters.py),
[resumable suite](../evals/suite.py), [source-evidence judge](../evals/suite_judge.py),
[saved-output review](../evals/automated_reviews.py) and
[review viewer](../evals/review_server.py).

| Flow | Earliest comparable saved run | Current, including separate retry |
| --- | ---: | ---: |
| Chat | 10/17 | 12/17 |
| Summaries | 3/6 | 6/6 |
| Video/course | 6/9 | 8/9 |
| Revision sheets | 0/5 | 3/5 |
| Interviews | 5/8 | 5/8 |
| **Clearing every quality/contract check** | **24/45** | **34/45** |

These fifty shared cases have identical input/source/expected fields. Five
policy/source/capture exceptions are excluded explicitly; four later-added
cases are not counted as historical cases. The final full manifest completed
53/54 first attempts and cleared 35/49 eligible quality cases; one separate
provider retry raises those to 54/54 and 36/49. Preserve the failed first attempt.
This is the earliest saved evaluation, not a reconstruction of old production.

| Retained change | Measurement and limit |
| --- | --- |
| Bounded neighboring section context | Definition coverage 2/4 → 4/4 and checklist 1/4 → 4/4 including repeats; broad audits remain unstable |
| Full-source summary repair and large-sheet context/recovery | Combined shared summary/sheet results improve as above; individual causal contributions are not isolated |
| Citation metadata and source-bound images | Regressions reject stale/foreign bindings; a valid locator does not prove claim support |
| Browser reuse in layout search | Controlled rendering median 4.468 → 1.110 seconds, lower browser CPU, unchanged content/geometry; no whole-sheet production speedup |
| Larger citations | Six paired identical-content renders, approximately 9 → 10 points, unchanged pages/figures, no additional inference |

The latest twenty candidates covered alternative models, routing/grading,
retrieval, summary allocation, figure reading, layouts/caching and interview
instructions. Only larger citations were newly adopted. Other initial gains
did not repeat or survive independent review. Keep Luna and the earlier core
fixes; no broad quality gain over the start-of-round control is established.

The new round reports $3.867140599 plus $0.099636114 reserved for unknown receipts,
within $5. Failed calls, external review, repeats and production checks count.
The previous round's $3.904616935 is separate. Same-account production timings
are mixed; cached ideal reuse is not faster fresh generation. The forecast uses
100 chats, five summaries, three sheets, two interviews and twenty video/course
questions: $0.93/month with one search per chat, $1.25 with two, including 20%
headroom. Voice and arbitrary larger chapters are separate; this is not a hard cap.

Evidence: [initial report](evaluation-results.md),
[round-two selection](evaluation-round2-results.md),
[final decisions and production pairs](evaluation-round3-results.md),
[comparable historical results](../evaluation/round3_historical_comparison.json),
[monthly assumptions](../evaluation/round3_monthly_forecast.json).

## Other measured behavior

| Area | Recorded result | Evidence and qualification |
|---|---|---|
| Video QA | Required-evidence recall 75.0% → 90.0%; cited-evidence recall 38.5% → 77.5% | [Video runs](../evaluation/runs/video); 29 versus 43 turns, not a paired comparison |
| Video final run | Route/outcome/citation validity 100%; no execution errors | [Final run](../evaluation/runs/video/final-full/results.json); 43 turns, one lecture with linked material |
| Lecture summary | 24/24 stretches cited; 23/24 substantively covered | Same recorded video evaluation |
| Follow-up rewriting | Mean anchor recall 56.3% → 87.5% | 16 follow-ups; helped 7, hurt 2, unchanged 7 |
| Source-first chat | Anchor recall/order 100%; selection resolution 87.5%; rung accuracy 5/7 | [8-case run](../evaluation/runs/source-first.json); author-labelled expectations |
| Interview interaction | Development 1/4 → 4/4; held-out 4/4 | [Interaction runs](../evaluation/runs/interview-interactions); synthetic turns derived from public mock interviews |
| Interview realism | 60% → 100% rescored | [Realism runs](../evaluation/runs/interview-realism); 5 synthetic sessions, model judge |
| Candidate calibration | 4/6 profiles passed; completed score ladder monotonic | [Live run](../evaluation/runs/interview-candidate-calibration/live-human-profiles.json); 2 provider failures |
| OCR character error | Tesseract 37.91%; Gemini 14.73%; Qwen 14.80% | [OCR set](../evaluation/ocr_gold.json); 35 independently transcribed pages scored from 39 selected across 3 books |
| Reader geometry | 809/810 figures aligned with node text pages; 97/119 citations matched text exactly | Historical samples; highlighting misses use page focus, figure relevance unmeasured |

Video improvements involved modality balancing, timeline coalescing, and query
rewriting. The current [video set](../evaluation/video_gold.json) has 47 turns;
the quoted full run predates its last four-turn addition. Damaged text in the
linked slide deck and the absence of an independent answer judge limit the result.

Qwen/Gemini were effectively tied on aggregate OCR error. Qwen performed better
on prose; Gemini handled difficult pages better. The earlier circular-reference
OCR scoring method was discarded. Encoding/storage measurements are separate:
[embedding precision and dimensions](design-decisions.md#embedding-size-and-precision).

## Dataset review status

The book/video conversation scorers now label results `evidence-v2`. They reject
empty or fabricated citations on grounded factual answers, check the cited
page/source/rank or timestamp/frame/resource against supplied evidence, and
exclude missing required-evidence labels from recall rather than awarding 100%.
Reports include scored versus unknown/ineligible counts. Judge failures retain
generated answers and predicted state and are counted separately from application
failures. The general answer judge receives evidence, citations and prior history;
truncated excerpts or missing visual evidence must remain insufficient evidence.
These locator checks do not establish claim support, and the old recorded scores
above have not been recomputed under the new scoring version. The shared five-flow
runner, complete evidence capture and budget guard are implemented. Automated
review includes rendered PDF pages; manual review is optional. Verification and resume instructions are tracked in
[evaluation-progress.md](evaluation-progress.md).

| Set | Size | Status |
|---|---|---|
| [Multi-turn](../evaluation/multiturn_gold.json) | 11 conversations / 44 turns | Synthetic, separately model-reviewed |
| [Interview answers](../evaluation/interview_answer_seed.json) | 30 cases | Knowledge-authored; most evidence anchors await human review |
| [Interview interactions](../evaluation/interview_transcript_eval.json) | 8 cases | Synthetic turns based on public mock interviews |
| [Candidate profiles](../evaluation/interview_candidate_profiles.json) | 6 profiles | Human-authored answers |
| [Ideal interviews](../evaluation/ideal_interview_flow_seed.json) | 2 cases | Synthetic; human review outstanding |

Cards and revision sheets store per-artifact coverage/validation metrics.
Sheets also retain concept inventories, figure inspections, judge histories,
repairs, and unresolved findings. These audit trails do not establish improved
learning outcomes.

## Failed experiment and limitations

[Routing baseline](../evaluation/runs/routing-baseline-20260902/results.json)
versus [routing change](../evaluation/runs/routing-fix-20260902/results.json):
route accuracy fell 84.1% → 81.8%, scope accuracy 70.5% → 65.9%, outcome accuracy
81.8% → 79.5%. The change was not an improvement.

Valid citations do not guarantee that claims follow from evidence; the
source-first evaluation exposed that gap. Other limits include narrow
multi-source coverage, no labelled figure-relevance set, no learning-outcome
study, and no representative latency/cost percentiles. LangSmith traces are
the intended source for model latency, token use, and cost analysis. The latest
round's monthly quota rejection leaves new hosted metrics unavailable; labelled
local SDK intervals and provider receipts are retained separately. Remaining
five-flow failures include broad-answer support, unrelated figures, missing
course evidence, sheet content warnings and interview reasoning consistency.

## Reproduce

Commands require the canonical sources/database and relevant provider keys.
Live generation/judging makes paid calls. Each runner exposes options with
`--help`.

| Evaluation | Command |
|---|---|
| Five-flow coverage | `uv run --frozen --extra voice python -m scripts.run_evaluations --plan` |
| Budgeted native suite | `uv run --frozen --extra voice python -m scripts.run_evaluations --live --output evaluation/runs/NEW_EXPERIMENT --max-usd 2` |
| Saved-output automated review | `uv run --frozen --extra voice python -m scripts.judge_saved_evaluations --output evaluation/runs/NEW_REVIEW --max-usd 1` |
| Review viewer / optional manual labels | `uv run --frozen --extra voice python -m scripts.review_evaluations evaluation/runs/EXPERIMENT --port 8766` |
| Retrieval | `uv run python -m scripts.evaluate_retrieval --all` |
| Retrieval report | `uv run python -m scripts.build_retrieval_report`; `uv run python -m scripts.render_retrieval_report` |
| Anchors / source-first | `uv run python -m scripts.evaluate_source_first --resolution-only --all`; omit `--resolution-only` for generation |
| Multi-turn | `uv run python -m scripts.validate_multiturn_gold`; `uv run python -m scripts.evaluate_multiturn --all` |
| Video | `uv run python -m scripts.evaluate_video --all --owner-id "$VIDEO_OWNER_ID"` |
| Interview answers | `uv run python -m scripts.evaluate_interview_answers --smoke --judge-answers` |
| Interview realism | `uv run python -m scripts.evaluate_interview_realism --all --judge` |
| Interaction / calibration | `uv run python -m scripts.evaluate_interview_interactions --split held_out --judge`; `uv run python -m scripts.evaluate_interview_candidate_calibration` |
| Ideal interviews | `uv run python -m scripts.evaluate_ideal_interview_flows --judge` |
| OCR | `uv run python -m scripts.evaluate_transcription select`; then `transcribe`, then `score` |
