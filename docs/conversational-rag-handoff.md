# Conversational RAG handoff

Last updated: 2026-07-20

This document describes the code that exists now. `AGENTS.md` is authoritative
for product principles and scope; `README.md` contains setup and user commands.

## Current milestone

The application supports:

- Complete chapter and section summaries from canonical SQLite, with validated
  node/page citations and one bounded repair attempt.
- Deterministic chapter/section listings.
- BM25, local vector, hybrid, and hybrid-plus-reranker retrieval.
- Multi-turn Gradio chat with explicit per-session state.
- A minimal LangGraph workflow with explicit planning, conditional route
  execution, and deterministic state update.
- One LLM control decision for non-obvious turns: choose a route, rewrite a
  follow-up as a standalone question, optionally select a supplied canonical
  scope, or ask for clarification.
- A frozen 44-turn conversation set, canonical-data validation, sequential
  end-to-end evaluation, and searchable HTML reports.

The baseline before the simplification is commit
`9a6770c` (`working multi turn + summary based + hybrid rag`). The
simplification after that commit is intentionally uncommitted until reviewed.

## Runtime architecture

```text
Gradio message + ConversationState
    -> LangGraph study_turn
       -> plan_turn
          -> deterministic hierarchy fast path or structured LLM decision
       -> execute_hierarchy | execute_retrieval | transform_answer | clarify
       -> update_state
    -> answer + diagnostics
```

There is no general agent loop or retry cycle in the current milestone. The
control model cannot invent SQL or arbitrary scope metadata. For hierarchy
routes it can select only a node ID supplied from canonical SQLite or the
current active scope.

Every submitted user message is one LangSmith `study_turn` root trace. Graph
nodes, the control-model call, `BookRetriever`, answer generation, and state
update are nested beneath it. All turns share
`thread_id=ConversationState.conversation_id`, so LangSmith groups them as one
conversation thread. Conversation memory remains explicit application state;
the graph deliberately has no checkpointer yet.

### The small conversation contract

`study/contracts.py` contains only:

- `ConversationState`: messages, active scope, pending clarification, and the
  previous grounded answer/evidence.
- `TurnDecision`: route, dependency, standalone query, optional canonical
  scope, clarification, and a short reason.
- `TurnResult`: reader-facing answer plus route, scope, evidence, citations,
  outcome, and retrieval mode.

State changes are not model output. Python applies four simple rules:

1. A successful chapter/section summary or listing becomes the active scope.
2. Retrieval questions do not silently replace that scope.
3. A clarification records the unresolved user message.
4. Any completed answer clears the pending clarification and becomes the
   previous answer.

This replaced the former `TurnAnalysis`, `StateUpdate`,
`SufficiencyDecision`, `ScopeBehavior`, and duplicated predicted-state
contracts.

## Important files

### Canonical content and hierarchy

- `parsing/parser.py`
- `storage/sqlite.py`
- `study/scope.py`
- `study/content.py`
- `study/context.py`

`data/books.sqlite3` remains the only canonical source. Chunks, FTS, Chroma,
and reports are rebuildable derived data.

### Summary and QA runtime

- `study/request.py`: deterministic explicit hierarchy-request parsing.
- `study/summarize.py`: prompt construction, citation normalization,
  validation, and one repair.
- `study/query.py`: one-turn hierarchy and retrieval execution.
- `study/scope_candidates.py`: bounded canonical candidates for the control
  model.
- `study/analyze.py`: the deterministic fast path and structured LLM decision.
- `study/graph.py`: minimal traced LangGraph and conditional route edges.
- `study/conversation.py`: graph entry point, route execution, and
  deterministic state updates.
- `app.py`: the small stateful Gradio interface.

### Retrieval

- `retrieval/chunking.py`: rebuildable citation-aware text chunks.
- `retrieval/sqlite.py`: FTS5/BM25.
- `retrieval/vector.py`: local Chroma embeddings.
- `retrieval/search.py`: BM25, vector, hybrid, and reranked strategies.
- `retrieval/reranker.py`: local cross-encoder reranking.

### Evaluation

- `evaluation/multiturn_gold.json`: 11 conversations, 44 turns.
- `scripts/validate_multiturn_gold.py`: essential schema and canonical
  node/page/citation checks.
- `scripts/build_multiturn_report.py`: static gold-set review page.
- `evals/multiturn.py`: sequential replay of the real coordinator.
- `evals/report.py`: searchable run report.
- `scripts/evaluate_multiturn.py`: the only conversation-evaluation CLI.

The former frozen one-turn evaluator and isolated analyzer evaluator were
removed. They scored internal fields that no longer exist and obscured the
only result that matters now: whether a real sequential conversation works.

The current evaluator reports:

- Route accuracy.
- History-dependency accuracy.
- Resolved/retained scope accuracy.
- Outcome accuracy.
- Required evidence recall.
- Citation-to-evidence validity.
- Exact standalone-query agreement as a debugging signal only.
- Optional LLM answer-quality scores when `--judge-answers` is requested.

Retrieval methods continue to use their separate frozen retrieval evaluation.
The conversation evaluator no longer repeats an oracle retrieval experiment.

## Commands to verify

Install and run with the project environment:

```bash
uv sync
uv run python -m unittest discover -s tests -v
uv run python -m scripts.validate_multiturn_gold
uv run python -m scripts.build_multiturn_report
```

Launch the chat:

```bash
uv run python app.py
```

Suggested browser check:

1. `What sections are present in Chapter 1?`
2. `Which one discusses differences between research and production?`
3. `How does reservoir sampling work?`
4. `Make that answer shorter.`

Inspect route, rewritten query, active scope, and retrieval mode in the
diagnostics accordion.

Run a small live evaluation first:

```bash
uv run python -m scripts.evaluate_multiturn \
  --conversation mt-001 \
  --conversation mt-009 \
  --conversation mt-010
```

Add `--judge-answers` only when the extra control-model calls and cost are
wanted. Run all 44 turns with `--all` only after the smoke report is healthy.

## Environment

Required for live use:

```text
OPENROUTER_API_KEY=
OPENROUTER_GENERATION_MODEL=openai/gpt-5.6-luna
OPENROUTER_CONTROL_MODEL=x-ai/grok-4.5
OPENROUTER_CONTROL_REASONING=high
```

Standard LangSmith environment variables enable automatic LangChain tracing.
Do not store credentials in source control.

## Known limits

- Retrieval QA asks the generation model to abstain when evidence is
  insufficient, but there is not yet an explicit deterministic or model-based
  sufficiency node.
- A non-obvious conversational turn uses one control-model call with up to
  three schema/provider retries.
- The current UI is Gradio, not the final FastAPI plus React portfolio shape.
- The graph does not yet include an evidence-sufficiency or retry node.
- Interview-question generation is not implemented.
- Images retain canonical metadata/captions but are not embedded as pixels.
- The multi-turn set is model-adjudicated, not human-verified.

## Next implementation order

Keep future additions measured and small:

1. Run the simplified smoke evaluation and inspect its concrete failures.
2. Add a minimal evidence-sufficiency check only if unanswerable or
   multi-section cases demonstrate the need.
3. Extend the existing graph only when evaluation justifies:
   `plan -> retrieve -> check -> optional one retry -> answer`.
4. Add interview-question generation with cited answers and verification.
5. Put the graph behind FastAPI, then replace Gradio with a minimal React UI.
6. Add Docker, CI, structured logs, and the final evaluation report.

Vector retrieval and reranking already exist. Do not add another vector
database, multi-agent architecture, saved summaries, or multimodal retrieval
unless evaluation or the remaining project time justifies it.

## Guidance for the next coding session

Before editing:

1. Read `AGENTS.md` and this document.
2. Inspect `git status --short`; preserve the simplification changes.
3. Run the offline tests and gold validator.
4. Read the latest live report under `evaluation/runs/`, if one exists.
5. Work on the first unresolved item in “Next implementation order.”

The central design constraint is now easy to state in an interview: explicit
hierarchy requests are deterministic, ambiguous language gets one bounded LLM
decision over canonical choices, retrieval remains a separate measurable
component, and Python owns state and safety.
