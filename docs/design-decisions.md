# Design decisions

Deterministic code handles repeatable work; models handle language and
judgment. Decisions prioritize grounding, inspectability, and measured value.

## Architecture tradeoffs

| Choice | Reason | Tradeoff |
|---|---|---|
| Postgres for content, search, and queues | Transactions and owner-scoped state in one system | Search/job load shares the database |
| Hierarchical, page-aware source data | Evidence retains chapter/section/page identity | Outline and extraction quality need explicit gates |
| BM25 → hybrid → reranking | Measured recall/order failures justified each layer | Additional model calls and provider dependency |
| Exact vector search | Current scale does not justify approximate indexes | Search cost grows with corpus size |
| Complete-scope explanations | Relevant top-k matches cannot establish coverage | Explicit context ceilings and potentially higher cost |
| LangGraph for decision loops | State, branches, retries, and stopping are inspectable | More state contracts than a single prompt |
| Durable worker queues | Long work survives reloads and process failure | Leases, checkpoints, and idempotency are required |
| Supabase Auth | Managed sessions, refresh, and token verification | Identity is separate from database/storage deployment |
| Optional LiveKit | Streaming speech without replacing interview logic | Separate media workers and configuration |
| LangSmith for AI traces, shared runner for quality evals | Inspect prompts/evidence and compare frozen outputs without another eval platform | Quotas and model-review bias remain explicit; no additional Opik service |
| Direct OpenTelemetry export to Grafana Cloud | Logs, operational traces and process metrics without monitoring servers | Best-effort bounded queues can drop telemetry; no SQL/browser trace instrumentation |
| PostHog with safe explicit UI events | Usage and event journeys without capturing study content | Replay disabled; generic actions do not prove feature completion |
| Verified auth UUID across telemetry | Join logs, traces and product events without email | Anonymous/system work has no user; identity is not a metric/stream label |

## Model defaults

Environment variables override repository defaults; artifact provenance records
actual usage. STT is speech-to-text; TTS is text-to-speech.
Configuration: [.env.example](../.env.example).

| Role | Default | Selection basis / fallback |
|---|---|---|
| Answers, routing, cards, interviews, revision composition/review | `openai/gpt-6-luna` | Shared structured/vision default; cost control |
| Text embeddings | `openai/text-embedding-3-large` | Shared text model; evaluated as part of hybrid retrieval |
| Reranking | `cohere/rerank-4-pro` | Measured Recall@5 gain; hybrid fallback on failure |
| Scanned-page OCR | `qwen/qwen3-vl-32b-instruct` | Measured prose quality/cost |
| OCR evaluation fallback / general evaluation judge | `google/gemini-3-flash-preview` | Difficult-page fallback in the transcription evaluation script; separate quality judging |
| Figure captions / spoken figure descriptions | `google/gemini-2.5-flash-lite` | Bounded vision descriptions |
| Video frame analysis | `openai/gpt-6-luna` | Shared vision default; timed observations |
| Video region embeddings | `google/gemini-embedding-2` | Question/diagram similarity in a separate space |
| Lecture audio / composer dictation | `openai/whisper-1` | Timed speech; captions preferred for lectures |
| HTTP interview transcription | `openai/whisper-large-v3-turbo` | Short ephemeral clips |
| HTTP interview / reading speech | `mistralai/voxtral-mini-tts-2603` | Shared speech default; reading overrides supported |
| LiveKit interview / narration | STT `deepgram/nova-3`; TTS `cartesia/sonic-3` | Streaming; narration inherits defaults |
| LiveKit ideal interview | TTS `cartesia/sonic-3.6` | Two-voice saved-flow playback |

Revision review is a separate pass using the composition model unless
overridden. The twenty-candidate round compares answer, routing, grading,
sheet and figure-reading roles; no alternative showed a reliable improvement
worth promoting. Keep Luna, with larger citations and the earlier tested core
fixes. Speech defaults were not compared in that round.
[Selection evidence and limits](evaluation-round3-results.md).

On October 1, 2026, active Luna defaults moved from GPT-5.6 Luna to GPT-6 Luna
after checking the [OpenRouter model catalog](https://openrouter.ai/api/v1/models)
and official OpenAI model pages for [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
and [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).
Standard rates per million tokens fell from $0.20 to $0.10 for input, $0.02
to $0.01 for cached input, and $1.20 to $0.50 for output. Both retain text/image
input, structured output, a 1,050,000-token context window, and a 128,000-token
output limit. Prompts above 272,000 input tokens have higher rates. The switch
preserves reasoning settings and output contracts; it is a cost decision,
not a measured improvement in answer quality. Saved artifacts retain their
original model provenance, and experiment/evaluation baselines remain pinned.
Live smoke checks passed for cited text generation with reasoning disabled,
structured interview output with low reasoning, and production frame analysis
with recorded model provenance. These establish API compatibility, not
comparative study quality.

### Where models are configured and called

There is no single model registry. Model clients and fallback identifiers live
in the Python module for each feature. Runtime environment variables select
overrides; [.env.example](../.env.example) documents the main settings, and
local entrypoints load `.env`. The default table above describes repository
choices, not a guarantee of the model selected by a running process.

Most study, vision, retrieval, and HTTP speech model calls go to hosted
providers through OpenRouter. Optional voice workers use LiveKit Inference.
Next.js displays the results and does not host or invoke these models directly.

| Role | Model client / selection code | Environment override |
|---|---|---|
| Book/paper answers and summaries | [study/query.py](../study/query.py), `openrouter_model` | `OPENROUTER_GENERATION_MODEL` |
| Book/paper request analysis and control | [study/analyze.py](../study/analyze.py), `_openrouter_model`; [study/query.py](../study/query.py), `control_model` | `OPENROUTER_CONTROL_MODEL` |
| Library suggested questions | [study/question_generator.py](../study/question_generator.py) | `OPENROUTER_GENERATION_MODEL` |
| Book/paper embeddings | [retrieval/vector.py](../retrieval/vector.py), `build_embedder` | `OPENROUTER_EMBEDDING_MODEL` |
| Retrieval reranking | [retrieval/reranker.py](../retrieval/reranker.py), `build_reranker` | `OPENROUTER_RERANKER_MODEL` |
| Scanned-page OCR | [ingestion/ocr.py](../ingestion/ocr.py); [ingestion/pipeline.py](../ingestion/pipeline.py) constructs the provider | `OPENROUTER_OCR_MODEL` |
| OCR evaluation fallback | [scripts/evaluate_transcription.py](../scripts/evaluate_transcription.py) constructs a second provider | Explicit `DEFAULT_OCR_FALLBACK_MODEL` from [ingestion/ocr.py](../ingestion/ocr.py) |
| Book figure captions | [ingestion/captions.py](../ingestion/captions.py) | `OPENROUTER_CAPTION_MODEL` |
| Lecture/course answers and control | [video/models.py](../video/models.py), `answer_model`, `control_model` | `OPENROUTER_VIDEO_ANSWER_MODEL` → `OPENROUTER_GENERATION_MODEL`; control uses `OPENROUTER_CONTROL_MODEL` |
| Video frame analysis | [video/vision.py](../video/vision.py); [video/pipeline.py](../video/pipeline.py) selects ingestion settings | `OPENROUTER_VIDEO_VISION_MODEL` |
| Video text and image-region embeddings | [video/embeddings.py](../video/embeddings.py) | `OPENROUTER_VIDEO_TEXT_EMBEDDING_MODEL`, `OPENROUTER_VIDEO_IMAGE_EMBEDDING_MODEL` |
| Lecture audio transcription | [video/audio.py](../video/audio.py) | `OPENROUTER_AUDIO_MODEL` |
| Composer dictation | [study/dictation.py](../study/dictation.py) | `OPENROUTER_AUDIO_MODEL` |
| Flashcard generation and extraction | [decks/generate.py](../decks/generate.py), `card_model`; [decks/extraction.py](../decks/extraction.py) | `OPENROUTER_DECK_MODEL` → `OPENROUTER_GENERATION_MODEL` |
| Revision composition and review | [revision_sheets/generate.py](../revision_sheets/generate.py), `revision_model` | `OPENROUTER_REVISION_MODEL`; review uses `OPENROUTER_REVISION_JUDGE_MODEL` → composition model |
| Adaptive and ideal interview generation | [interviews/models.py](../interviews/models.py), `structured_model` | `OPENROUTER_INTERVIEW_MODEL` → `OPENROUTER_GENERATION_MODEL` |
| HTTP interview transcription | [api/interviews.py](../api/interviews.py) selects the model; [study/dictation.py](../study/dictation.py) calls it | `OPENROUTER_INTERVIEW_STT_MODEL` |
| HTTP interview and reading speech | [narration/synthesis.py](../narration/synthesis.py), `configured_model`, `synthesize_speech` | `OPENROUTER_TTS_MODEL`; reading uses `OPENROUTER_READING_TTS_MODEL` → shared TTS model |
| Spoken figure descriptions | [narration/figures.py](../narration/figures.py) | `OPENROUTER_NARRATION_FIGURE_MODEL` → `OPENROUTER_CAPTION_MODEL` |
| LiveKit interview speech | [interviews/voice_worker.py](../interviews/voice_worker.py) | `LIVEKIT_INTERVIEW_STT_MODEL`, `LIVEKIT_INTERVIEW_TTS_MODEL` |
| LiveKit narration speech | [narration/voice_worker.py](../narration/voice_worker.py) | `LIVEKIT_NARRATION_STT_MODEL`, `LIVEKIT_NARRATION_TTS_MODEL` (inherit interview settings when absent) |
| LiveKit ideal interview playback | [interviews/ideal_voice_worker.py](../interviews/ideal_voice_worker.py) | `LIVEKIT_IDEAL_TTS_MODEL` |
| Evaluation judges | [evals/judge.py](../evals/judge.py); [evals/ideal_interview.py](../evals/ideal_interview.py) | `OPENROUTER_JUDGE_MODEL` |

An arrow in the override column means the first setting falls back to the
second before using the module's default. Revision composition is independent
of `OPENROUTER_GENERATION_MODEL`. Video text embeddings also have their own
setting rather than inheriting the book embedding setting.

`OPENROUTER_OCR_FALLBACK_MODEL` appears in `.env.example` but is not read by
the current implementation. The Gemini OCR fallback is explicitly selected
by the transcription evaluation script, not automatically by normal ingestion.

Suggested-question generation uses the configured generation model (Luna in
`.env.example`), but its code fallback is `anthropic/claude-3.5-sonnet` when
`OPENROUTER_GENERATION_MODEL` is absent. Experiment-only choices also exist in
[experiments/video_course](../experiments/video_course), including its
[evaluation](../experiments/video_course/evaluation.py) and
[retrieval](../experiments/video_course/retrieval.py) modules; those are separate
from the application defaults. Local PDF layout detection is handled by
Unstructured in [parsing/parser.py](../parsing/parser.py), separate from hosted
OCR and study models.

### Embedding size and precision

Books use 3,072 text dimensions, video text 1,024, image regions 768; database
vectors use half precision. Half precision preserved measured recall. Reducing
text to 1,024 dimensions lowered video recall 0.912 → 0.897 and book recall
0.931 → 0.889. Only video accepted that loss for storage savings. These
measurements cover retrieval encoding, not answer quality.

Evidence: [encoding evaluation](../evals/embedding_encoding.py),
[migration rationale](../supabase/migrations/20260903170000_halfvec_embeddings.sql).
Model implementations: [text vectors](../retrieval/vector.py), [video vectors](../video/embeddings.py),
[OCR](../ingestion/ocr.py), [generation](../study/query.py), [speech](../narration/synthesis.py).

## Grounding and stopping

Source answers cite pages/timestamps; external answers are labelled separately.
Insufficient evidence can abstain or widen under explicit policy. Valid
citations establish provenance, not claim entailment.

Loops are bounded: each grounding rung once; lecture/course retrieval one
broader retry; generated cards one coverage repair; book/paper summaries two
repairs; revision sheets bounded content repairs, one fit repair, and two
quality repairs. Interviews limit focused follow-ups and stop at coverage or
duration boundaries. Full behavior: [study flows](flows.md).

## Evaluation-driven changes

Hybrid reranking improved book recall; video balancing/rewriting improved
evidence recall; bounded interview policies improved interaction cases; vision
OCR beat Tesseract. A routing experiment reduced accuracy. Dataset sizes,
review status, and results: [evaluation](evaluation.md).
