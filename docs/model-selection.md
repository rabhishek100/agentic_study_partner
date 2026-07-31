# OpenRouter model selection

Decision date: 2026-07-31

The application keeps separate model roles because generation, routing,
evaluation, captioning, embedding, and reranking have different contracts.
Models are replaced only after paired application evaluations, not from
general benchmark scores alone.

| Role | Previous active model | Selected model | Reasoning |
|---|---|---|---|
| Grounded answers and summaries | DeepSeek V4 Flash | GPT-5.6 Luna | none |
| Conversation routing | GPT-4o mini | GPT-5.6 Luna | low |
| Optional semantic judge | Gemini 3 Flash Preview | unchanged | high |
| Figure captioning | Gemini 2.5 Flash Lite | unchanged | provider default |
| Embeddings | OpenAI text-embedding-3-large | unchanged | not applicable |
| Reranking | Cohere Rerank 4 Pro | unchanged | not applicable |

Luna is used through OpenRouter as `openai/gpt-5.6-luna`. Generation keeps
reasoning disabled because it transforms retrieved evidence under
deterministic citation checks. Routing uses low reasoning because it must
produce a strict structured decision and resolve conversational references.
The Gemini judge remains independent so Luna does not grade its own answers.

## Validation

The migration used the same prompts, retrieval settings, production books,
and frozen cases for both configurations. Gemini 3 Flash Preview judged both
answer sets. The first Luna run exposed one compatibility issue: Luna rendered
plain evidence markers such as `[S1]` using OpenAI's native citation token.
The response boundary now normalizes that provider syntax back to the app's
existing `[S#]` and `[N#:P#]` contracts before deterministic validation.

### Interview-answer smoke

Cases: `int-001`, `int-011`, and `int-030`.

| Metric | Previous stack | Luna generation + control |
|---|---:|---:|
| Route accuracy | 100% | 100% |
| Outcome accuracy | 66.7% | 100% |
| Depth and archetype accuracy | 100% | 100% |
| Required candidate-evidence recall | 100% | 90% |
| Citation validity and presence | 100% | 100% |
| Provider/judge errors | 0 | 0 |
| Independent judge focused mean | 4.0 / 4 | 4.0 / 4 |
| Mean end-to-end latency | 91.5 s | 36.2 s |

Luna missed one candidate evidence node in the logistic-regression case, but
the independent judge found every semantic must-cover criterion present and
gave all seven quality dimensions 4/4. Luna also correctly abstained on the
unanswerable current-pricing question, which the previous generation model
answered.

### Multi-turn smoke

Conversations: `mt-001`, `mt-009`, and `mt-010` (12 turns total).

| Metric | Previous stack | Luna generation + control |
|---|---:|---:|
| Route accuracy | 100% | 100% |
| History-dependency accuracy | 100% | 100% |
| Scope accuracy | 100% | 100% |
| Outcome accuracy | 75% | 91.7% |
| Required-evidence recall | 100% | 100% |
| Citation validity | 100% | 100% |
| Errors | 0 | 0 |

This set exercises a complete chapter summary, dependent follow-ups,
clarification, explicit scope changes, and cross-chapter retrieval. The exact
standalone-query string score was unchanged and is retained only for debugging;
the evaluation treats retrieval coverage and route semantics as the meaningful
signals.

Run artifacts are written locally under
`evaluation/runs/model-luna-validation/` and are intentionally gitignored.

## Cost posture

OpenRouter's live promotional price at the decision time is $0.10 per million
input tokens and $0.60 per million output tokens for Luna. At a representative
14,000-input/2,500-output-token chapter summary, that is about $0.0029 versus
$0.00266 for DeepSeek V4 Flash at its then-current $0.14/$0.28 pricing—roughly
a 9% increase for the validated quality and latency gains.

The promotion is not a durable budget assumption. OpenAI's standard direct
price is materially higher, and OpenRouter can change or end discounted
pricing. Recheck provider pricing before production rollout and monitor actual
cost from traces. Requests above 272K input tokens also use Luna's higher
long-context rate.

## Why the remaining roles stay separate

- The judge stays on Gemini so evaluation remains independent.
- Captioning remains on Gemini 2.5 Flash Lite until a vision-caption eval
  demonstrates a benefit from changing it.
- Luna is a generative model and cannot replace embedding or reranking
  endpoints.
- No GPT-5.6-only agent, persisted-reasoning, pro-mode, or tool-calling feature
  is enabled. The existing explicit LangGraph workflow and deterministic
  validators remain the production control plane.

## Sources

- [OpenAI: GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI: using GPT-5.6](https://developers.openai.com/api/docs/guides/model-guidance?model=gpt-5.6-luna)
- [OpenRouter: GPT-5.6 Luna](https://openrouter.ai/openai/gpt-5.6-luna)
- [OpenRouter models API](https://openrouter.ai/api/v1/models)
