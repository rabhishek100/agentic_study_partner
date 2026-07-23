# OpenRouter model selection

Decision date: 2026-07-21

The application uses separate models because its model-backed tasks have
different requirements. Prices below are OpenRouter list prices per one
million input/output tokens at the decision date. Artificial Analysis (AA)
Intelligence Index scores are general capability signals, not substitutes for
the project's routing, retrieval, citation, and summary evaluations.

| Role | Previous default | New default | OpenRouter input/output | AA score |
|---|---|---|---:|---:|
| Grounded answers and summaries | GPT-5.6 Luna | DeepSeek V4 Flash, reasoning off | $0.098 / $0.196 | 29 non-reasoning |
| Conversation routing | Grok 4.5 high | Gemini 3.1 Flash Lite high | $0.25 / $1.50 | 25 |
| Optional semantic judge | Grok 4.5 high | Gemini 3 Flash Preview high | $0.50 / $3.00 | 38 |

For comparison, GPT-5.6 Luna costs $1/$6 and scores 33 at low reasoning
(46 at high). Grok 4.5 costs $2/$6 and scores 54 at high reasoning. The new
defaults trade unnecessary general reasoning strength for models matched to
the narrower application tasks.

## Why these defaults

DeepSeek V4 Flash has a one-million-token context window, and AA reports strong
non-reasoning price/performance. More importantly, it passed the application's
deterministic validator on complete Chapter 1 and Chapter 5 summaries, covering
6 and 15 content-bearing nodes respectively, and produced valid citations.
Reasoning is disabled because summarization is evidence transformation, and
reasoning tokens add cost without improving the deterministic grounding
contract.

Chapter summaries deliberately use a complete non-streaming provider call and
are exposed only after deterministic citation validation. A live Chapter 1
comparison found that the streaming provider path ended without a finish
reason after 1,852 characters and omitted later required nodes, while the same
prompt through the normal invocation returned 6,157 characters, covered all
six required nodes, and passed on the first attempt. Ordinary retrieval QA
continues to stream token by token.

Gemini 3.1 Flash Lite is the control model because it supports strict
structured output and configurable reasoning. In a seven-case live check, it
correctly handled an independent term question, scoped comparison, ordinal
coreference, prior-answer transformation, implicit chapter summary, ambiguous
reference, and explicit scope switch. Calls took roughly 2–4 seconds. Cheaper
models tested during selection either violated the schema, misclassified
history dependence, or had much higher latency.

The optional answer judge uses Gemini 3 Flash Preview rather than the cheaper
control model because evaluation benefits from extra semantic capability. It
is not a safety gate: canonical scope checks, evidence coverage, and citation
validity remain deterministic.

## Rejected lower-cost options

- Qwen 3.5 Flash repeatedly omitted required schema fields and invented field
  names in the structured routing response.
- MiniMax M2.5 completed the schema but took about 80 seconds for seven small
  routing cases and mishandled clear conversational dependencies.
- DeepSeek V3.2 and Qwen 3.5 397B were too slow through the available
  OpenRouter providers for an interactive control step.
- DeepSeek V4 Flash high reasoning was inexpensive and capable, but it also
  misclassified several history-dependent routing cases. It remains a good
  generation candidate with reasoning disabled, where deterministic citation
  validation provides the relevant quality gate.

At a representative 14,000-input/2,500-output-token chapter summary, list-price
cost falls from about $0.029 with Luna to about $0.0019 with DeepSeek V4 Flash,
roughly a 94% reduction. A short routing decision is roughly 85% cheaper with
Gemini 3.1 Flash Lite than Grok 4.5, depending on payload and reasoning-token
length.

## Sources

- [OpenRouter: DeepSeek V4 Flash](https://openrouter.ai/deepseek/deepseek-v4-flash)
- [OpenRouter: Gemini 3.1 Flash Lite](https://openrouter.ai/google/gemini-3.1-flash-lite)
- [OpenRouter: Gemini 3 Flash Preview](https://openrouter.ai/google/gemini-3-flash-preview)
- [Artificial Analysis: DeepSeek V4 Flash non-reasoning](https://artificialanalysis.ai/models/deepseek-v4-flash-non-reasoning)
- [Artificial Analysis: Gemini 3.1 Flash Lite](https://artificialanalysis.ai/models/gemini-3-1-flash-lite-preview)
- [Artificial Analysis: Gemini 3 Flash reasoning](https://artificialanalysis.ai/models/gemini-3-flash-reasoning)
- [Artificial Analysis: GPT-5.6 Luna low](https://artificialanalysis.ai/models/gpt-5-6-luna-low)
- [Artificial Analysis: Grok 4.5 high](https://artificialanalysis.ai/models/grok-4-5)
