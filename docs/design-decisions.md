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

## Model defaults

Environment variables override repository defaults; artifact provenance records
actual usage. STT is speech-to-text; TTS is text-to-speech.
Configuration: [.env.example](../.env.example).

| Role | Default | Selection basis / fallback |
|---|---|---|
| Answers, routing, cards, interviews, revision composition/review | `openai/gpt-5.6-luna` | Shared structured/vision default; cost control |
| Text embeddings | `openai/text-embedding-3-large` | Shared text model; evaluated as part of hybrid retrieval |
| Reranking | `cohere/rerank-4-pro` | Measured Recall@5 gain; hybrid fallback on failure |
| Scanned-page OCR | `qwen/qwen3-vl-32b-instruct` | Measured prose quality/cost; Gemini fallback |
| OCR fallback / general evaluation judge | `google/gemini-3-flash-preview` | Difficult-page fallback; separate quality judging |
| Figure captions / spoken figure descriptions | `google/gemini-2.5-flash-lite` | Bounded vision descriptions |
| Video frame analysis | `openai/gpt-5.6-luna` | Shared vision default; timed observations |
| Video region embeddings | `google/gemini-embedding-2` | Question/diagram similarity in a separate space |
| Lecture audio / composer dictation | `openai/whisper-1` | Timed speech; captions preferred for lectures |
| HTTP interview transcription | `openai/whisper-large-v3-turbo` | Short ephemeral clips |
| HTTP interview / reading speech | `mistralai/voxtral-mini-tts-2603` | Shared speech default; reading overrides supported |
| LiveKit interview / narration | STT `deepgram/nova-3`; TTS `cartesia/sonic-3` | Streaming; narration inherits defaults |
| LiveKit ideal interview | TTS `cartesia/sonic-3.6` | Two-voice saved-flow playback |

Revision review is a separate pass using the composition model unless
overridden. Generation/speech defaults lack comparative model benchmarks.

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
