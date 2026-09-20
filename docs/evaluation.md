# Evaluation

## How to read these results

Committed JSON is the evidence. This page summarizes it; it does not upgrade a
synthetic or model-reviewed dataset into a human-verified benchmark. Results
are small, project-specific diagnostics, not general model-quality claims.

Three layers are tested separately:

1. deterministic contracts: schemas, citations, ownership, state transitions,
   budgets, and retry bounds;
2. retrieval/coverage: whether expected source units reach the model;
3. generated quality: optional rubric judges and manual inspection.

## Measured improvements

| Area | Before | After | Change that earned the result | Evidence |
|---|---:|---:|---|---|
| Book retrieval Recall@5 | BM25 88.9% | Hybrid + reranker 100% | Added embeddings, RRF fusion, then reranked 20 candidates | 12 answerable questions; `retrieval_comparison_artifact.json` |
| Book retrieval MRR@5 | BM25 91.7% | Hybrid + reranker 95.8% | Same comparison | Same 12 questions |
| Video required-evidence recall | 75.0% | 90.0% | Mixed-modality retrieval, balance fixes, time/section routing, prompt changes | First 29-turn run vs expanded 43-turn run; not a strict paired comparison |
| Video cited-evidence recall | 38.5% | 77.5% | Coalesced timeline evidence and citation/coverage fixes | Same non-paired runs |
| Video outcome accuracy | 93.1% | 100% | Whole-scope routing and evidence fixes | Same non-paired runs |
| Interview interaction pass rate | 25% | 100% | Neutral deterministic reaction policy and bounded follow-up routing | 4 development cases |
| Interview held-out pass rate | — | 100% | Same frozen policy | 4 held-out cases |
| Interview realism pass rate | 60% | 100% rescored | Source-independent, breadth-first, role-aware question policy | 5 synthetic sessions; model judge |
| OCR character error rate | Tesseract 37.91% | Gemini 14.73%; Qwen 14.80% | Vision transcription; Qwen chosen on price/prose, Gemini as fallback | 35 independently transcribed pages |

The video before/after sets differ in size and difficulty, so the table reports
them as directional evidence, not a controlled delta. The routing experiment
named `routing-fix-20260902` was not an improvement: route accuracy fell from
84.1% to 81.8%, scope accuracy from 70.5% to 65.9%, and outcome accuracy from
81.8% to 79.5%. It remains committed because failed experiments are evidence.

## Retrieval evaluation

`evaluation/retrieval_gold_seed.json` contains 15 cases: 5 exact-term, 4
paraphrase, 3 multi-section, and 3 unanswerable. Recall/MRR use only the 12
answerable cases. Expected nodes were inspected against canonical pages; the
artifact records model/configuration provenance and per-query rankings.

| Method | Recall@3 | Recall@5 | MRR@5 |
|---|---:|---:|---:|
| BM25 | 81.9% | 88.9% | 91.7% |
| Vector | 90.3% | 93.1% | 79.2% |
| Hybrid RRF | 84.7% | 93.1% | 84.7% |
| Hybrid + reranker | 88.9% | 100% | 95.8% |

The reranker candidate set had 100% Recall@20. Multi-section Recall@5 moved
from 72.2% for BM25/hybrid to 100% after reranking. This justified the current
default; it does not prove the strategy wins on other books.

Run all four strategies against the configured canonical database:

```bash
uv run python -m scripts.evaluate_retrieval --all
uv run python -m scripts.build_retrieval_report
uv run python -m scripts.render_retrieval_report
```

## Source-first study

The 8-case set checks anchored evidence, selection resolution, and which rung
answered. The committed run measured 100% anchor recall, 100% anchor-first
ordering, 87.5% selection resolution, and 71.4% rung accuracy (5/7 scored
cases). It also exposed an important hole: an answer can remain on a grounded
rung while making claims not entailed by the cited evidence. Citation presence
is therefore not treated as claim-level correctness.

```bash
uv run python -m scripts.evaluate_source_first --resolution-only --all
uv run python -m scripts.evaluate_source_first --all \
  --output evaluation/runs/source-first.json
```

Expected rungs are author judgments, not human-reviewed library audits.

## Multi-turn book conversation

`multiturn_gold.json` has 11 synthetic conversations and 44 turns covering
scope changes, summaries, follow-ups, answer transforms, clarification, and
abstention. It is model-generated and separately model-reviewed, not
human-verified. The latest committed comparison includes the failed routing
experiment described above; neither run is a release-quality conversational
benchmark.

```bash
uv run python -m scripts.validate_multiturn_gold
uv run python -m scripts.evaluate_multiturn --all
```

## Video RAG

`video_gold.json` currently contains 13 conversations and 47 turns over one
lecture plus linked material. The latest committed full-run numbers predate the
last four-turn addition and therefore cover 43 turns: route 100%, history dependency
95.3%, outcome 100%, required-evidence recall 90.0%, cited-evidence recall
77.5%, citation validity 100%, and zero execution errors. Retrieval alone
reached 90.9% of gold anchors; 30/33 retrieval turns had full anchor coverage.

Whole-lecture summary coverage was 24/24 stretches cited and 23/24
substantively covered in the recorded run. Query rewriting over 16 follow-ups
moved mean anchor recall from 56.3% as typed to 87.5%; the gold rewrite reached
93.8%. Rewriting helped 7, hurt 2, and did not change 7.

Known limitation: the linked slide deck used for the set has damaged extracted
text on many pages. The run is source-specific and has no independent answer
quality judge.

```bash
uv run python -m scripts.validate_video_gold --owner-id "$VIDEO_OWNER_ID"
uv run python -m scripts.evaluate_video --smoke --owner-id "$VIDEO_OWNER_ID"
uv run python -m scripts.evaluate_video --all --owner-id "$VIDEO_OWNER_ID"
```

## Interviews

| Dataset | Size | What it checks | Review status |
|---|---:|---|---|
| `interview_answer_seed.json` | 30 cases | Grounded study answers for interview preparation | Knowledge-authored; most evidence anchors await human review |
| `interview_realism_seed.json` | 5 sessions | Question sequence, role fit, progression, source independence | Research-derived synthetic; model judged |
| `interview_transcript_eval.json` | 8 cases | Clarify/probe/advance behavior | Derived from 8 public mock interviews; synthetic turns |
| `interview_candidate_profiles.json` | 6 profiles | Weak/mixed/strong score calibration | Human-authored answers; one live run had 2 provider failures |
| `ideal_interview_flow_seed.json` | 2 cases | Complete topic coverage and spoken coherence | Synthetic; human review still required |

Useful commands:

```bash
uv run python -m scripts.validate_interview_dataset
uv run python -m scripts.evaluate_interview_answers --smoke --judge-answers
uv run python -m scripts.evaluate_interview_realism --all --judge
uv run python -m scripts.evaluate_interview_interactions --split held_out --judge
uv run python -m scripts.evaluate_interview_candidate_calibration
uv run python -m scripts.evaluate_ideal_interview_flows --judge
```

The candidate-calibration run passed 4/6 profiles and its complete ladder was
monotonic; 2 cases were provider failures, so it is not a clean 66.7% product
quality result.

## OCR and reader evidence

The OCR set selected 39 pages from 3 scanned books; 35 pages met the scoring
floor. References were transcribed from page images independently of every
candidate. Gemini and Qwen were effectively tied on aggregate CER; Qwen did
better on prose, Gemini on pathological pages, and Tesseract could not
represent tables. The earlier circular-reference scoring method was discarded.

```bash
uv run python -m scripts.evaluate_transcription select
uv run python -m scripts.evaluate_transcription transcribe
uv run python -m scripts.evaluate_transcription score
```

Two separate reader measurements are historical snapshots:

- 809/810 stored figures shared a page with text from their hierarchy node,
  supporting zero page tolerance and a six-figure display cap. Figure
  usefulness precision/recall is still unmeasured.
- Citation text matching resolved exactly on 97/119 sampled citations (81.5%);
  misses fall back to page-level focus. Persisted PDF geometry would be needed
  for exact highlighting.

## Revision sheets and flashcards

Both features report deterministic coverage and validation metrics per
artifact. Revision sheets also store the independent inventory, judge history,
repair counts, figure inspection ledger, and unresolved findings. These are
strong audit trails, but there is no held-out population benchmark proving
revision sheets or cards improve learning outcomes.

## What is still missing

- Human review of the 30-case interview-answer evidence anchors.
- Claim-level entailment evaluation, not only valid citation markers.
- A labelled figure-relevance set.
- Multi-book and multi-lecture evaluation beyond the current narrow sources.
- Human learning-outcome or interview-outcome studies.
- Stable latency/cost percentiles collected across a representative workload.
