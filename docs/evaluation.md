# Evaluation

Results are project-specific diagnostics, not general benchmarks. Evaluation
separates deterministic contracts, retrieved-source coverage, and generated
quality. Synthetic/model-reviewed data is not treated as human-verified gold.
Committed datasets and result artifacts are the evidence.

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
the intended source for model latency, token use, and cost analysis.

## Reproduce

Commands require the canonical sources/database and relevant provider keys.
Live generation/judging makes paid calls. Each runner exposes options with
`--help`.

| Evaluation | Command |
|---|---|
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
