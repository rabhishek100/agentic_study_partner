# Video-first course pilot

Decision date: 2026-08-03

## Purpose

The first video experiment tests a different product thesis from transcript
RAG: a technical lecture is an ordered visual source whose frames, visual
states, and transitions are evidence in their own right. Speech, slides,
notes, and other linked material improve the answer, but none substitutes for
what the lecture visibly shows.

The pilot is deliberately isolated from the production book schema. It must
prove that visual-first retrieval improves grounded answers before the product
is generalized from books and pages to sources and typed locators.

## Pilot source

- Course: Stanford CME295, Transformers & LLMs, Autumn 2025.
- Lecture: Lecture 1 — Transformer.
- YouTube video: `https://www.youtube.com/watch?v=Ub3GoFaUcds`.
- Official slides:
  `https://cme295.stanford.edu/slides/fall25-cme295-lecture1.pdf?v=1761094147`.
- Scope: the complete lecture, approximately 102 minutes.
- Evidence set: video, timestamped transcript, official slides, and official
  course/video metadata. Third-party summaries are excluded.

## Product decisions

- The eventual product has a video-centred course workspace, not a chat UI
  with a video bolted onto it.
- Answers combine every relevant modality and show supporting frames,
  timestamps, transcript excerpts, slides, and other linked sources inline.
- Static claims cite an exact representative frame. Claims about change cite
  a before/after visual sequence and a time interval.
- A citation seeks a few seconds before the evidence interval, highlights the
  evidence, and waits for the user to press play.
- Slides and notes remain independent sources linked to a course or lecture.
  A direct timestamp/page alignment is optional and must not be invented.
- Retrieval searches all linked materials automatically while preserving
  their identity and provenance.
- Conflicting sources are surfaced and explained rather than silently merged.
- A partially supported question receives a partial grounded answer with
  explicit gaps. General knowledge never fills missing evidence.

## Visual evidence model

The source video is canonical. Selected frames, crops, OCR, observations,
embeddings, chunks, and summaries are derived and rebuildable.

The pilot artifact model contains:

- `visual_segments`: stable states or shots with start/end timestamps;
- `video_frames`: retained frames with timestamps, hashes, dimensions, and
  the deterministic reason each became a candidate;
- `frame_regions`: meaningful crops, bounding boxes, OCR, and region type;
- `visual_observations`: model-classified content types and structured visual
  interpretations with model/prompt provenance;
- `visual_events`: transitions such as slide changes, diagram expansion,
  code edits, commands, outputs, or UI actions;
- `modality_alignments`: optional frame/transcript and frame/slide links with
  confidence and method;
- `evidence_units`: retrieval-ready representations with typed locators.

Ordinary presenter footage is deprioritized, but gestures, whiteboards,
physical explanations, and demonstrations remain eligible evidence.

## Ingestion pipeline

1. Acquire metadata, video, transcript, and official slides.
2. Decode the complete timeline and create candidate frames from scene
   changes, visual differences, OCR changes, and periodic safeguards.
3. Deduplicate candidates by content and perceptual similarity.
4. Retain original-resolution selected frames plus optimized model/report
   variants and meaningful crops.
5. Run Tesseract as the cheap, inspectable OCR baseline.
6. Send short frame sequences to one structured VLM call that classifies and
   interprets the stable state and any visual transition.
7. Use the model classification, not handwritten semantic heuristics, to
   decide whether a frame is text, code, terminal, chart, diagram, drawing,
   UI, presenter footage, or a mixture.
8. Use OCR and text embeddings for textual screens. Use OpenRouter's
   `google/gemini-embedding-2` image embeddings only for model-classified
   drawings, diagrams, charts, and mixed visual regions.
9. Align transcript ranges and slide pages only when evidence supports the
   relationship.
10. Build modality-aware lexical and semantic retrieval indexes without
    crossing lecture or visual-segment boundaries.

Local utilities such as FFmpeg, Tesseract, hashing, resizing, and scene/change
detection are allowed. Larger VLM and semantic image inference goes through
OpenRouter.

## Model and cost policy

- Narrow candidates using current public benchmarks and OpenRouter pricing,
  then test only the top two inexpensive models on a small, stratified set of
  representative frame sequences.
- The selected bulk model must be on the measured price/performance frontier,
  not merely the highest-ranked model.
- A stronger model handles only ambiguous, information-dense, or
  evaluation-critical evidence.
- Escalation uses observable triggers: low OCR quality, poor retrieval margin,
  missing required modalities, conflicting evidence, invalid structured
  output, or failed citation verification. Self-reported confidence is not a
  sufficient trigger.
- Playlist ingestion target: comfortably below USD 5.
- Playlist ingestion hard cap: USD 5, including calibration.
- Answer target: USD 0.02 average per question.
- Answer hard cap: USD 0.05 for an individual difficult question.
- Cache every stable model result by content, model, and prompt hash.
- Record actual provider-reported cost and latency for every call.

## Pilot evaluation

The first run is a diagnostic baseline, not a pass/fail release gate.

After ingestion, generate 30 balanced questions across visual-only,
transcript-led, slide-led, multimodal, temporal-change, synthesis, and
deliberately unanswerable cases. Generate a source-grounded reference answer
from the complete evidence for each question. Reference answers may not add
outside knowledge.

Compare the retrieval-bound system answer with the reference for:

- semantic and directional agreement;
- missing or contradictory points;
- expected-frame and expected-segment retrieval;
- timestamp accuracy;
- modality and citation correctness;
- unsupported claims and abstention quality;
- latency and actual cost.

The final report is an interactive local HTML application backed by JSON. It
includes the complete visual timeline, searchable OCR, frame filmstrip,
clickable timestamps, slide links, system/reference comparison, evidence per
question, metrics, cost, and failure diagnostics.

## Reliability and artifact policy

- The pipeline checkpoints every stage and resumes interrupted runs.
- Artifacts are content-addressed. A changed prompt invalidates its dependent
  model outputs, not video decoding or otherwise compatible upstream work.
- A successful ingestion requires frames across 100% of playable duration,
  transcript coverage of at least 95% of spoken duration, successful analysis
  for at least 90% of unique visual segments, visual evidence in every official
  chapter, and no unexplained analysis gap longer than five minutes.
- Failed model calls receive bounded retries and one fallback. Successful
  segments survive failures elsewhere.
- Retain the source, transcript, selected frames, crops, sequences, OCR, model
  outputs, embeddings, evaluation results, and report. Delete unselected
  decoded frames and temporary files.
- Serve the artifact bundle with a lightweight local report server. Do not
  modify the production Next.js UI or Postgres schema during this pilot.

## Implementation boundary

The experiment may live under `experiments/video_course/`, with runnable entry
points under `scripts/`. Its output belongs under the ignored
`evaluation/runs/video-course/` directory. Production integration begins only
after the report identifies measured value and concrete failure modes.
