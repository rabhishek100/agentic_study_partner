# CME295 Lecture 1 video-first pilot results

Run date: 2026-08-03

## Outcome

The complete 102-minute Stanford CME295 Lecture 1 pilot passed all ingestion
coverage gates and produced a working local video-centred report. The result
supports the feasibility of treating frames and visual transitions as
retrievable evidence, but it is a diagnostic baseline rather than a product
quality claim.

The final artifact is under the ignored directory
`evaluation/runs/video-course/cme295-lecture1/`. Run it with:

```bash
uv run python -m scripts.video_course_pilot serve --port 8765
```

Then open `http://127.0.0.1:8765/report.html`.

## Ingestion measurements

- Source duration: 6,119 seconds across nine official YouTube chapters.
- Transcript: 195 segments covering 99.81% of the playable timeline.
- Official slides: 135 pages, kept as a separately linked source.
- Retained frames: 243 from change detection plus periodic safeguards.
- Visual observations: 243, grouped as 122 before/after sequences.
- Timeline and chapter visual coverage: 100%.
- Maximum analyzed visual gap: 60 seconds.
- Retrieval evidence units: 573.
- Image embeddings: 158 frames classified by the VLM as containing a
  diagram, drawing, or chart. All other visual evidence uses OCR and text
  retrieval.

The current image units are selected full frames containing spatial evidence.
The next iteration should extract diagram regions before embedding them; that
will avoid presenter/background pixels and more literally satisfy the
region-only image-embedding policy.

## Model calibration

OpenRouter provider-reported measurements on representative frame pairs:

| Model | Valid / attempts | Mean latency | Total calibration cost | Decision |
|---|---:|---:|---:|---|
| `openai/gpt-5.6-luna` | 6 / 6 | 7.17 s | $0.00270 | Selected |
| `stepfun/step-3.7-flash` | 3 / 12 | 35.43 s | $0.04098 | Rejected: invalid/truncated structured output |
| `xiaomi/mimo-v2.5` | 0 / 2 | 99.86 s | $0.00838 | Rejected: invalid/truncated structured output |

Luna's full 122-sequence visual pass had no invalid outputs and cost $0.06086.
This is the clearest price/performance finding from the pilot: headline token
price was much less predictive than structured-output validity and latency.

## Corrected evaluation

The gold-set generator is code-gated to exactly 30 questions:

- 6 visual-only;
- 8 multimodal;
- 4 transcript-led;
- 4 slide-led;
- 4 temporal-change;
- 2 synthesis;
- 2 deliberately unanswerable.

Every official chapter must occur in at least two answerable questions. This
gate was added after the first question-generation prompt truncated the
evidence inventory and accidentally omitted the final self-attention,
Transformer, and worked-example chapters. The initially higher score was
discarded; this is an important evaluation-design failure, not just a prompt
detail.

Final source-grounded results:

| Category | Questions | Mean semantic agreement |
|---|---:|---:|
| Visual-only | 6 | 100.0% |
| Multimodal | 8 | 96.25% |
| Transcript-led | 4 | 98.75% |
| Slide-led | 4 | 100.0% |
| Temporal change | 4 | 96.25% |
| Synthesis | 2 | 72.5% |
| Unanswerable | 2 | 100.0% |
| **Overall** | **30** | **96.5%** |

All 30 runs completed without an error. The citation-number validator scored
100%, the judge found no unsupported claims, and it identified 15 missing
points. The two unanswerable questions correctly stated that the exact number
of attention heads and exact BLEU score were absent instead of inventing
values.

The main failure is multi-chapter synthesis. The lowest-scoring question
(50%) asked for the progression from tokenization through Transformers; a
top-12 fused retrieval context omitted specific tokenization trade-offs,
Word2vec proxy tasks, RNN limitations, query/key/value mechanics, and
Transformer components. This is a measured reason to add decomposed retrieval
or chapter-aware coverage checks. It is not yet evidence for a different
vector database or a complex agent loop.

## Cost

- Cumulative ingestion experimentation: $0.14520.
- Cumulative evaluation experimentation: $0.47708.
- All-in cost, including rejected model probes and superseded evaluation
  attempts: $0.62228.
- Final 30-question answer/reference/judge pass: $0.15130, or $0.00504 per
  question on average; maximum $0.01077.

The clean marginal ingestion path is materially cheaper than the cumulative
number because it excludes rejected calibration attempts and superseded
artifacts. The observed cost leaves ample room under the $5 playlist cap; the
more immediate playlist constraint is sequential provider latency and local
video decoding, not model spend.

## What the pilot establishes

- Frames can be canonical evidence with typed timestamps, searchable visual
  interpretations, and actual images supplied to answer generation.
- Before/after frame pairs capture technical changes that transcripts and
  static slide decks cannot represent by themselves.
- OCR-first retrieval plus diagram-only image embeddings is inexpensive.
- Clickable answer citations and evidence cards can seek the local lecture
  three seconds before the cited timestamp.
- Supporting slides can remain independent linked material without inventing
  page-to-timestamp alignment.

## Limitations and recommended next step

The reported agreement is not a manual gold score. Questions and reference
answers are source-grounded but model-generated, and answer generation uses the
same Luna model family; the separate Gemini judge reduces but does not remove
self-evaluation bias. Timestamp precision is not manually audited, and the
current citation metric validates evidence numbering rather than whether every
locator is the best possible moment.

Before production integration, manually audit a stratified 10-question subset
with special attention to the six visual-only and four temporal-change cases.
Then add one bounded LangGraph retry for the demonstrated synthesis failure:
decompose by required chapter or stage, retrieve each part, check modality and
chapter coverage, and synthesize only when all parts have evidence. Keep the
video experiment isolated until that change improves the same fixed question
set.
