# Conversational RAG Implementation Handoff

Last updated: 2026-07-20

## Purpose of this document

This is the continuation document for the conversational, hierarchy-aware RAG
feature. A new LLM session should be able to read this file, inspect the
referenced code, and continue without reconstructing the project history from
chat.

This document records:

- What the project is trying to demonstrate.
- What is already implemented and verified.
- The current limitations of the running UI.
- The frozen architectural decisions for conversational RAG.
- The implementation scope of each batch.
- The evaluation protocol and commands.
- The next concrete task.

`AGENTS.md` remains authoritative for project principles and scope. `README.md`
remains authoritative for setup and current commands. This document is the
authoritative feature-level roadmap and handoff.

## Repository status at this handoff

Batch 1 and Batch 2 currently exist as uncommitted working-tree changes. A new
session must inspect and preserve them rather than resetting or overwriting
them. This note should be updated after the changes are intentionally
committed.

## Start here in a new session

Before changing code:

1. Read `AGENTS.md`.
2. Read this document completely.
3. Run `git status --short`; the working tree may contain intentional
   uncommitted work.
4. Run the offline tests:

   ```bash
   uv run python -m unittest discover -s tests -p 'test_*.py'
   ```

5. Validate the multi-turn gold set:

   ```bash
   uv run python -m scripts.validate_multiturn_gold
   ```

6. Inspect the latest baseline report referenced below.
7. Implement only the current batch. Do not introduce later-batch
   orchestration prematurely.

## Product and engineering objective

The project is an evaluation-driven study companion for technical books. It
must:

- Ingest a technical book into a lossless hierarchical source model.
- Summarize complete chapters or sections with canonical citations.
- Answer questions from retrieved evidence.
- Resolve conversational references such as “that section” or “the first
  approach.”
- Clarify ambiguous questions and abstain when the book lacks evidence.
- Expose an inspectable LangGraph workflow and end-to-end LangSmith traces.
- Demonstrate why each retrieval or agentic technique was added.

The desired conversational flow is:

```text
current message + explicit conversation state
    -> analyze route, history dependency, scope, and standalone meaning
    -> retrieve a complete hierarchy scope or ranked chunks
    -> check evidence sufficiency
    -> broaden once when justified
    -> generate a grounded response
    -> validate citations and grounding
    -> update conversation state
    -> render the answer and diagnostics in the UI
```

The agent coordinates deterministic hierarchy access and retrieval. It does
not replace BM25/vector search and never executes unrestricted model-generated
SQL.

## Current data and retrieval architecture

```text
PDF
  -> parsing/parser.py
  -> ParsedBook
  -> storage/sqlite.py
  -> data/books.sqlite3                     canonical and lossless
      -> retrieval/chunking.py
      -> data/retrieval.sqlite3             derived chunks + FTS5
      -> retrieval/vector.py
      -> data/chroma/                       derived local vectors
```

### Canonical source data

`data/books.sqlite3` is the source of truth. It stores:

- Books.
- Hierarchical TOC nodes.
- Ordered text blocks.
- Structured table blocks.
- Image blocks and provenance.
- Canonical physical PDF page numbers.

Parsed content is source data. Chunks, FTS indexes, vectors, summaries, and
evaluation reports are derived and must remain rebuildable.

The currently indexed book is *Designing Machine Learning Systems* by Chip
Huyen, using database book ID `1`.

### Derived chunks and lexical retrieval

`retrieval/chunking.py` constructs citation-aware chunks from canonical
SQLite. Each chunk retains:

- Source book ID.
- Source node ID.
- Chunk index and ID.
- Chapter/section hierarchy.
- Start and end PDF pages.
- Content-type provenance.

`data/retrieval.sqlite3` contains the rebuildable chunks and an FTS5 index.
BM25 searches weighted title, hierarchy, and body fields.

### Vector and hybrid retrieval

Local semantic retrieval uses:

- Embedded Chroma at `data/chroma/`.
- Embedding model `Alibaba-NLP/gte-modernbert-base`.
- Local CPU execution by default.

Hybrid retrieval uses unweighted reciprocal-rank fusion over BM25 and vector
results. Hybrid plus reranking uses the pinned local
`Alibaba-NLP/gte-reranker-modernbert-base` cross-encoder over a bounded
candidate set.

The current vector milestone deliberately indexes text representations only.
Tables can contribute flattened text. Image pixels are not embedded and there
is no multimodal similarity search in the current feature scope.

## What was built before the conversational batches

### PDF ingestion and canonical storage — complete

Relevant files:

- `parsing/models.py`
- `parsing/parser.py`
- `storage/schema.sql`
- `storage/sqlite.py`
- `scripts/parse_book.py`
- `scripts/import_book.py`

Properties:

- Hierarchical and lossless.
- Idempotent import behavior with explicit replacement.
- Canonical content can be restored to parser models.
- Tables and images remain source records rather than being hidden inside
  retrieval-only blobs.

### Citation-aware chunk ingestion and BM25 — complete

Relevant files:

- `retrieval/models.py`
- `retrieval/chunking.py`
- `retrieval/schema.sql`
- `retrieval/sqlite.py`
- `scripts/build_chunks.py`

Properties:

- Deterministic chunk construction.
- Rebuildable derived database.
- FTS5/BM25 baseline.
- Hierarchy and page metadata carried into every result.

### Vector, hybrid, and reranked retrieval — complete

Relevant files:

- `retrieval/vector.py`
- `retrieval/search.py`
- `retrieval/reranker.py`
- `retrieval/langchain.py`
- `scripts/build_vector_index.py`
- `scripts/evaluate_retrieval.py`

Available retrieval modes:

- `bm25`
- `vector`
- `hybrid`
- `hybrid_rerank`

The current ordinary QA default remains `hybrid`; the gold-query oracle in the
multi-turn evaluator uses `hybrid_rerank`.

### Retrieval evaluation — complete

The retrieval seed set is `evaluation/retrieval_gold_seed.json`. It contains
exact-term, paraphrase, multi-section, and unanswerable probes.

The comparison report is:

- `evaluation/retrieval_comparison.html`

This work justified vector retrieval and reranking with measured retrieval
behavior rather than adding them speculatively.

### Complete-scope hierarchy operations — complete

Explicit chapter and section operations resolve from canonical SQLite rather
than top-k retrieval.

Implemented behavior:

- “Summarize Chapter 3” loads the complete Chapter 3 subtree.
- “Summarize section X in Chapter 3” loads the complete section subtree.
- “What sections are present in Chapter 1?” reads the hierarchy
  deterministically.
- Named summaries fall back to ordinary retrieval only when a unique hierarchy
  scope cannot be resolved.

Complete-scope summaries:

- Fail instead of silently truncating when the prompt exceeds its configured
  context budget.
- Validate required-node citation coverage.
- Regenerate at most once when deterministic citation/coverage validation
  fails, passing the exact errors back to the same model.
- Never silently reassign an invalid citation to another node or page.
- Normalize grouped markers such as `[N81:P153; N81:P154]` into two unchanged
  canonical markers before validation.
- Use canonical node/page citation markers.
- Append reader-facing book, chapter, section, and PDF-page references.

Relevant files:

- `study/request.py`
- `study/scope.py`
- `study/content.py`
- `study/context.py`
- `study/summarize.py`
- `study/render.py`
- `study/query.py`
- `scripts/study.py`
- `scripts/ask_book.py`

### One-turn QA compatibility layer — complete

`study.query.answer_query()` remains the stable one-turn compatibility path.
It can:

- Run explicit chapter/section summaries.
- List hierarchy scopes.
- Run BM25, vector, hybrid, or hybrid-rerank QA.
- Display cited reader-facing answers.

`study.query.execute_query()` intentionally remains stateless. The current
Gradio UI calls the conversational coordinator described in Batch 3 instead,
so existing CLI callers do not regress.

## Conversational evaluation data — complete

The frozen multi-turn seed set is:

- `evaluation/multiturn_gold.json`
- `evaluation/multiturn_gold.html`

It contains 11 conversations and 44 turns covering:

- Complete chapter/section summaries.
- Hierarchy listings.
- Scoped follow-up questions.
- Pronoun and ordinal references.
- Independent topic switches.
- Cross-section synthesis.
- Prior-answer transformations.
- Clarification.
- Grounded abstention.
- Time-sensitive questions unsupported by a static book.

The set is model-adjudicated synthetic data, not human-verified. Canonical
nodes, pages, scopes, dependencies, routes, and outline expectations are
validated against SQLite.

Validate and rebuild the offline review artifact with:

```bash
uv run python -m scripts.validate_multiturn_gold
uv run python -m scripts.build_multiturn_report
```

## Batch 1 — evaluation foundation — complete

Batch 1 deliberately measured the frozen one-turn implementation before
adding conversational behavior.

### Implemented contracts

`study/contracts.py` now defines strict Pydantic boundaries for:

- `ConversationMessage`
- `ConversationState`
- `ScopeRef`
- `EvidenceRef`
- `CitationRef`
- `StateUpdate`
- `SufficiencyDecision`
- `TurnResult`

`ConversationState` currently contains:

- Conversation ID.
- Book ID.
- Recent messages.
- Active scope.
- Pending clarification.
- Previous answer.
- Previous evidence.
- Previous route.

`TurnResult` is the standard result shape for hierarchy operations, QA,
clarification, abstention, errors, evidence, citations, scope, and state
updates.

### Structured compatibility layer

`study.query.execute_query()` exposes the existing behavior as `TurnResult`.
`answer_query()` remains a compatibility wrapper returning reader-facing
Markdown, so the CLI and UI did not regress.

The Batch 1 executor intentionally ignores supplied conversation state. That
behavior is the baseline being measured, not an accidental omission.

### Multi-turn replay and judgments

`evals/multiturn.py` provides:

- `CurrentBaselineExecutor`.
- Explicit state replay between turns.
- Setup-turn replay when evaluating one dependent turn.
- Component judgments for route, history dependency, scope, scope behavior,
  state update, outcome, outline, evidence recall, and citation coverage.
- Deterministic safety checks.
- `GoldQueryRetriever` to isolate retrieval quality using the gold standalone
  query.
- Optional semantic answer judging.

Deterministic safety checks currently detect:

- Citations to nonexistent canonical nodes/pages.
- Citations outside supplied evidence.
- Evidence escaping a hard hierarchy scope.
- Silent omission of required summary nodes.
- Answering after an explicit insufficient-evidence decision.
- A pure prior-answer transformation introducing new evidence.

Zero deterministic violations does not mean every answer is semantically
correct. Semantic unsupported claims are judged separately.

### Live evaluator and reports

`scripts/evaluate_multiturn.py` provides:

- Targeted conversation selection.
- Targeted turn selection with prerequisite replay.
- Explicit `--all` for the full set.
- Live configuration validation.
- Preflight model and estimated-call reporting.
- Turn-level progress output.
- LangSmith tracing.
- Machine-readable JSON output.
- A self-contained HTML report.

`evals/report.py` renders:

- Gold versus predicted decisions.
- Reference versus generated responses.
- Expected versus observed evidence.
- Oracle-query retrieval.
- Safety checks.
- Semantic answer judgments.
- State snapshots.
- LangSmith trace links.
- Search and route/status filters.

Markdown is rendered with HTML disabled so model output is readable without
trusting embedded HTML.

Routine runs are written below `evaluation/runs/` and are gitignored. Promote
only deliberately selected baselines into a versioned baseline directory.

### Batch 1 live smoke result

Run:

- `evaluation/runs/baseline-20260720T145900Z/`

Smoke conversations:

- `mt-001`
- `mt-009`
- `mt-010`

Models:

- Generation: `openai/gpt-5.6-luna`
- Control/judge: `x-ai/grok-4.5`, high reasoning

Results:

| Metric | Result |
|---|---:|
| Route accuracy | 66.7% |
| History-dependency accuracy | 41.7% |
| State-update accuracy | 33.3% |
| Required-evidence recall | 62.5% |
| Gold-query oracle retrieval recall | 95.2% |
| Deterministic safety violations | 0 |

Interpretation:

- The baseline only recognizes explicit hierarchy operations
  deterministically.
- Every other message becomes independent global retrieval QA.
- It cannot resolve “it,” “the first one,” or “that section.”
- It does not set or retain active scope.
- It has no explicit clarification or abstention route for ordinary QA.
- When supplied a clear standalone gold query, the existing retriever usually
  finds the correct evidence.

The main measured failure is therefore conversation interpretation and
orchestration, not a need for another retrieval backend.

### Batch 1 validation state

At the end of Batch 1:

- 46 offline tests passed.
- The 44-turn gold set passed canonical validation.
- The live smoke completed with 12 LangSmith-linked turn traces.
- The HTML report was checked in a real browser.
- No full 44-turn live baseline has been run yet.

## Frozen decisions for the conversational implementation

Do not reopen these decisions without new evidence or a user request.

### Model responsibilities

- Generation model: `openai/gpt-5.6-luna`.
- Control model: `x-ai/grok-4.5` with high reasoning.
- Obvious hierarchy operations remain deterministic.
- Non-obvious route, dependency, rewrite, and scope decisions use one
  structured control-model call.
- Provider/schema failures retry the same model up to two times.
- There is no silent fallback model.

### Conversation context

- Keep explicit `ConversationState`.
- Give analysis the last three conversation turns plus explicit state.
- Bound long assistant-answer content; retain scope/evidence metadata.
- Version one is in memory, scoped to a UI/API session.
- Do not add persistence, users, or multi-tenancy for this feature.

### Scope resolution

- SQLite is canonical.
- Produce a small canonical chapter/section candidate list before the LLM
  selects a scope.
- The LLM may select only supplied candidates or the current canonical active
  scope.
- Never execute LLM-generated SQL.
- Use explicit ambiguity signals and clarification, not a numeric confidence
  threshold.

### Retrieval query construction

- Produce one faithful standalone query.
- Do not add query expansion, multiple rewrites, HyDE, or sample-answer search
  until evaluation demonstrates a need.
- Future conversational retrieval QA uses hybrid retrieval plus the local
  reranker.
- Return five distinct reranked nodes, then add bounded neighboring context
  during the later execution batch.
- Target a bounded evidence budget of approximately 12,000 tokens.

### Complete-scope operations

- Chapter/section summaries use the complete canonical subtree.
- They do not use top-k retrieval.
- They fail explicitly rather than silently truncate.
- Pure transformations of a previous answer reuse its evidence.
- A request that asks to enrich or add facts performs new retrieval.

### Sufficiency and grounding

- Evidence sufficiency is a structured control-model decision.
- One scope-broadening retry is allowed.
- Continued insufficiency produces abstention.
- Python validates citations against canonical SQLite and supplied evidence.
- The control model checks semantic grounding.
- One regeneration is allowed after a grounding failure.

### Evaluation and observability

- Unit tests remain offline with fake models/retrievers.
- Live model-backed evaluation requires LangSmith configuration.
- Targeted runs are the default workflow.
- The full set requires explicit `--all`.
- Grok semantic scores are diagnostic, never safety gates.
- Routine runs remain gitignored.
- Quality thresholds are set after observing the relevant component baseline;
  safety invariants apply immediately.

### Orchestration

- Build and test pure Python components first.
- Add a thin LangGraph only after the component behavior is measurable.
- LangGraph must expose explicit state, route, retrieve, sufficiency, retry,
  generation, validation, and state-update nodes.
- Do not build an opaque open-ended agent loop.

## Batch roadmap

```text
Batch 0  ingestion, summaries, BM25, vectors, hybrid, reranking      complete
Batch 1  contracts, frozen baseline replay, tracing, reports         complete
Batch 2  turn analysis, scope candidates, component diagnostics      complete enough
Batch 3  minimal stateful execution + Gradio conversation            complete
Batch 4  sufficiency, retry, grounding, citation regeneration        pending
Batch 5  LangGraph orchestration and end-to-end tracing              pending
Batch 6  FastAPI/React and full conversational evaluation             pending
Batch 7  conversational hardening and promoted baseline              pending
```

## Simplification decision — 2026-07-20

The isolated Batch 2C run showed that optimizing exact scope and state-update
labels before shipping a usable conversation loop was creating complexity
without improving the initial product objective.

The authoritative initial runtime is now:

```text
message + recent state
    -> deterministic explicit hierarchy operation, or one structured rewrite
    -> complete SQLite scope OR existing book-wide retrieval
    -> cited response
    -> deterministic Python state update
    -> Gradio session
```

For this milestone:

- The model chooses the high-level route and one standalone query.
- Analyzer-proposed state changes never directly mutate runtime state.
- Explicit summaries and hierarchy listings establish persistent scope.
- Ordinary QA searches the selected book globally and retains any saved scope.
- Chapter/section context is carried in the rewritten query rather than a
  complex hard-filter/prefer-scope/fallback policy.
- Pre-retrieval `abstain` is treated as QA; the existing grounded prompt must
  report insufficiency after seeing evidence.
- Detailed semantic judges remain offline diagnostics, not runtime steps.
- LangGraph, broadening retries, HyDE, query expansion, and complex scope
  policies remain postponed.

## Batch 2 — turn analysis — complete enough for the minimal runtime

### Goal

Convert:

```text
current user message
+ last three turns
+ explicit ConversationState
+ canonical SQLite scope candidates
```

into one validated structured decision:

```text
route
+ history dependency
+ standalone query
+ resolved scope
+ scope behavior
+ proposed state update
+ clarification question when needed
```

Batch 2 produces diagnostic decisions. Batch 3 executes only the high-level
route, rewrite, and clarification fields; Python owns runtime state.

### Current Batch 2 status

Batch 2A is complete.

Implemented:

- Added strict `study.contracts.ScopeCandidate`.
- Added strict `study.contracts.TurnAnalysis`.
- Added route-specific contract validation for hierarchy, retrieval,
  clarification, and hard-scope decisions.
- Added `study.scope_candidates.find_scope_candidates()`.
- Ranked explicit chapter references, exact/partial title matches, active
  scope, recent scope mentions, and previous evidence.
- Added parent chapters for matched sections and subsections.
- Computed candidate page ranges from the complete canonical subtree.
- Kept candidate construction deterministic, read-only, and model-free.
- Added `tests/test_scope_candidates.py`.

Naming note: `study.scope.ScopeCandidate` is an older internal dataclass used
only to render deterministic ambiguity errors. The new conversational contract
is `study.contracts.ScopeCandidate`; Batch 2 code must import it explicitly.

Verification:

- 12 focused Batch 2A tests passed.
- 58 total offline tests passed.
- Real-book probes correctly selected Chapter 3, Modes of Dataflow, ETL,
  Reservoir Sampling, and Low-Rank Factorization.
- An unrelated question with no active state returned no arbitrary scope.
- At the end of Batch 2A, production paths remained unchanged.

Batch 2B is also complete.

Implemented:

- Added `study.analyze.analyze_turn()`.
- Reused the existing explicit hierarchy parser as the only deterministic
  fast path.
- Routed unresolved, ambiguous, dependent, and ordinary QA decisions through
  one structured control-model call.
- Bounded history to the last three turns, 2,000 characters per message, a
  2,000-character previous-answer excerpt, and eight previous evidence items.
- Passed canonical scope candidates, active scope, pending clarification,
  previous route, and previous evidence into the analyzer payload.
- Configured `x-ai/grok-4.5` with high reasoning through OpenRouter.
- Added one faithful standalone-query decision rather than expansion or HyDE.
- Canonicalized model-selected scope metadata from SQLite candidates.
- Rejected invented nodes, invalid preferred scopes, invalid state updates,
  clarification without pending state, and prior-answer transforms without a
  previous answer.
- Allowed two retries after the initial attempt, always using the same model
  and messages.
- Added `tests/test_turn_analysis.py`.

Verification:

- 12 focused Batch 2B tests passed.
- 70 total offline tests passed.
- Explicit summary and listing tests prove that the model is not called.
- Fake-model tests cover dependent rewriting, ambiguity, independent topic
  switches, unresolved explicit scopes, canonicalization, bounded context,
  transient retry, invented scope rejection, and transformation preconditions.
- The first live control-model component evaluation is recorded under Batch
  2C below.
- At the end of Batch 2B, production paths remained unchanged.

Batch 2C is complete. It added the isolated analysis evaluator, ran the
selected live turns with LangSmith tracing, and made the current turn-analysis
failure modes measurable without retrieval or answer-generation noise.

### New contracts

Extend `study/contracts.py` with:

#### `ScopeCandidate`

- `book_id`
- `node_id`
- `kind`
- `title`
- `display_path`
- `start_page`
- `end_page`
- `match_reason`

#### `TurnAnalysis`

- `route`
- `history_dependency`
- `standalone_query`
- `scope_behavior`
- `resolved_scope`
- `state_update`
- `clarification_question`
- `decision_reason`
- `decision_source` (`deterministic` or `llm`)

`decision_reason` must be a short inspectable explanation, not hidden
chain-of-thought.

### Canonical scope candidates

Create `study/scope_candidates.py`.

It should return roughly five to eight canonical candidates using:

- Explicit chapter numbers.
- Exact or partial node-title matches.
- Significant title words from the question.
- The active scope.
- Scopes mentioned in recent turns.
- A parent chapter when a subsection matches.

For the current book size, selecting canonical nodes and scoring them in plain
Python is acceptable and easier to inspect than adding another index.

Constraints:

- Parameterized SQLite only.
- No generated SQL.
- No vector search for hierarchy selection.
- Every candidate carries canonical node and page metadata.
- Candidate selection does not make the final semantic decision.

### Deterministic fast path

Reuse the existing parser only for explicit, uniquely resolvable hierarchy
requests such as:

- “Summarize Chapter 3.”
- “List sections in Chapter 4.”

Do not grow a large conversational rule set. Questions containing unresolved
pronouns, ordinals, implicit scope, or unclear transformations go to the
control model.

### Structured control-model analysis

Create `study/analyze.py`.

One Grok call receives:

- Current message.
- Bounded last-three-turn history.
- Active scope.
- Pending clarification.
- Previous route.
- Previous evidence paths.
- A bounded previous-answer excerpt.
- Canonical scope candidates.
- Allowed routes and state semantics.

It returns `TurnAnalysis` through structured output.

### Standalone rewriting

Dependent retrieval questions must become one faithful standalone query.

Example:

```text
active scope: Chapter 3
history: database dataflow compared with service dataflow
message: “Why is the first one weak for latency-sensitive applications?”

standalone query:
“Why is database-mediated dataflow in Chapter 3 weak for
latency-sensitive consumer applications?”
```

Do not retrieve during rewriting. Do not produce multiple queries or a
hypothetical answer.

### Clarification

Return `clarify` when:

- A pronoun has no unique antecedent.
- “First,” “second,” or similar ordinals have no known list.
- Multiple scopes remain equally plausible.
- The requested scope does not exist.
- A pending clarification has not been resolved.

Example:

```text
message: “Explain the second approach.”
history: none

route: clarify
clarification_question: “Which approaches are you referring to?”
```

### Python validation after the model

Reject decisions where:

- A selected scope is not a supplied candidate/current active scope.
- A hierarchy operation has no resolved scope.
- Retrieval QA has no standalone query.
- A prior-answer transformation has no previous answer.
- Clarification has no clarification question.
- A hard filter has no resolved scope.
- The output contains unknown fields or values.

Retry the same control model at most twice for invalid structured output or a
transient provider failure. Return an explicit analysis error after repeated
failure.

### Isolated component evaluation

Add:

- `evals/turn_analysis.py`
- `scripts/evaluate_turn_analysis.py`

The component evaluator should construct the correct expected conversation
state for each gold turn so analysis quality is measured without cascading
errors from earlier predicted turns.

Measure:

- Route accuracy.
- History-dependency accuracy.
- Scope accuracy.
- Scope-behavior accuracy.
- State-update accuracy.
- Clarification correctness.
- Standalone-query semantic preservation.
- Invented/invalid scope IDs.
- Model failures and trace links.

Exact standalone-query equality remains diagnostic only.

Start with:

- `mt-001-t2`
- `mt-001-t3`
- `mt-001-t4`
- `mt-009-t1`
- `mt-009-t2`
- `mt-009-t3`
- `mt-009-t4`
- `mt-010-t2`
- `mt-010-t3`

Then run all 44 analysis decisions and inspect the report before Batch 3.

### Batch 2C implementation and result

Implemented:

- Added `evals/turn_analysis.py`.
- Reconstructs the expected pre-turn `ConversationState` from prior gold turns,
  including active scope, pending clarification, previous answer, and previous
  evidence.
- Calls the analyzer only for selected turns; prior turns are applied silently
  from gold state, so one bad prediction cannot corrupt later component scores.
- Scores route, dependency, scope, scope behavior, state update,
  clarification, standalone-query presence/meaning, invalid scope IDs, and
  model failures.
- Added `OpenRouterQueryMeaningJudge` in `evals/judge.py`. Exact standalone
  matches skip the judge; non-exact rewrites are judged semantically.
- Added `scripts/evaluate_turn_analysis.py` with explicit targeted selection,
  `--all`, call-count preflight, progress output, LangSmith traces, JSON, and a
  self-contained HTML report.
- Added `evals/turn_analysis_report.py` with searchable expected-versus-observed
  decisions, gold state, canonical candidates, semantic judgments, component
  checks, and trace links.
- Added six offline evaluator tests.

Verification:

- 76 total offline tests pass.
- The frozen dataset still validates: 11 conversations and 44 turns.
- The HTML report was checked in a real browser; search, filters, expandable
  diagnostics, and LangSmith links work.
- The live run had no provider/schema failures, no judge failures, and no
  invented scope IDs.

Canonical targeted run:

- `evaluation/runs/turn-analysis-20260720T164605Z/`
- Selected turns: `mt-001-t2` through `t4`, `mt-009-t1` through `t4`, and
  `mt-010-t2` through `t3`.
- Control model and semantic judge: `x-ai/grok-4.5`, high reasoning.

| Metric | Result |
|---|---:|
| Analysis success | 100.0% |
| Route accuracy | 88.9% |
| History-dependency accuracy | 88.9% |
| Scope accuracy | 44.4% |
| Scope-behavior accuracy | 44.4% |
| State-update accuracy | 44.4% |
| Clarification accuracy | 100.0% |
| Standalone-query presence | 100.0% |
| Standalone-query semantic preservation | 37.5% |
| All components correct | 22.2% |
| Invalid scopes | 0 |

Measured failure patterns:

- Some “standalone” rewrites simply repeated phrases such as “the three
  dataflow modes” instead of naming the modes and Chapter 3.
- The analyzer often selected the narrow matching section and changed the
  active scope even when the frozen policy expected a global search or
  retention of the existing chapter scope.
- It treated the reply that resolved a pending clarification as dependent,
  while the gold contract labels the resolved request independent.
- It routed a likely-insufficient LoRA follow-up to retrieval rather than
  predicting the terminal `abstain` label.
- Clarification itself, explicit coreference expansion in `mt-009-t3`, and
  canonical scope safety worked.

An initial report at `turn-analysis-20260720T164032Z` exposed an overly strict
semantic-judge rubric that rejected harmless ordinal labels alongside named
referents. The rubric was corrected to distinguish harmless descriptive
redundancy from omissions that change retrieval evidence, and the canonical
run above is the post-correction result. Keep both runs as failure history.

Decision:

- Do not run the complete 44-turn live analysis yet.
- First adjudicate the scope-policy disagreements and make one small analyzer
  prompt/validation calibration from these measured failures.
- Re-run these same nine turns. Run all 44 only when the targeted behavior is
  coherent enough to make the larger call spend useful.

### Batch 2 files

| File | Status |
|---|---|
| `study/contracts.py` | Batch 2 contracts complete |
| `study/scope_candidates.py` | Complete |
| `tests/test_scope_candidates.py` | Complete |
| `study/analyze.py` | Batch 2B complete |
| `tests/test_turn_analysis.py` | Batch 2B complete |
| `evals/judge.py` | Batch 2C semantic query judge complete |
| `evals/turn_analysis.py` | Batch 2C complete |
| `evals/turn_analysis_report.py` | Batch 2C complete |
| `scripts/evaluate_turn_analysis.py` | Batch 2C complete |
| `tests/test_turn_analysis_evaluation.py` | Batch 2C complete |

### Batch 2 definition of done

- Every evaluated turn returns a schema-valid `TurnAnalysis`.
- Explicit hierarchy requests retain their deterministic behavior.
- Non-obvious decisions use the structured control model.
- Scope IDs always resolve to canonical SQLite nodes.
- Dependent questions receive faithful standalone queries.
- Ambiguous references produce clarification.
- The targeted component report exists; the full 44-turn component spend was
  deliberately deferred after the simplification decision.
- Existing summary, retrieval, and Batch 1 tests still pass.
- Analyzer output remains schema-valid and safe for the simplified Batch 3
  coordinator.

## Batch 3 — minimal stateful execution and Gradio — complete

### Goal

Deliver the smallest useful multi-turn product over the existing summaries,
BM25, vectors, hybrid retrieval, and cited generation.

### Implemented

- Added `study/conversation.py` with `execute_conversation_turn()`.
- Reused `study.analyze.analyze_turn()` for route selection and one
  standalone follow-up query.
- Reused complete canonical hierarchy operations from `study/query.py`.
- Reused the selected existing retrieval mode for ordinary QA.
- Converted analyzer `abstain` predictions into evidence-seeking QA so
  insufficiency is decided after retrieval in this initial version.
- Applied active scope, pending clarification, messages, previous answer,
  evidence, and citations deterministically in Python.
- Only successful hierarchy summary/list operations set persistent scope.
- Ordinary retrieval questions ignore analyzer scope/state commands and search
  the selected book globally.
- Added a minimal prior-answer transformation path that reuses prior evidence
  and citations without retrieval.
- Added six focused offline coordinator tests.
- Replaced the stateless `ChatInterface` with a small Gradio `Blocks` UI using
  per-session `gr.State`.
- Added route/query/scope/retrieval diagnostics and a clear action that resets
  both the transcript and internal state.

### Verification

- 86 total offline tests pass.
- Existing one-turn `answer_query()` behavior remains compatible.
- A real browser smoke test completed:
  1. `What sections are present in Chapter 1?`
  2. `Which one discusses differences between research and production?`
- The second turn was rewritten as:
  `Which section in Chapter 1 Overview of Machine Learning Systems discusses
  differences between research and production?`
- Hybrid retrieval returned the correct
  `Machine Learning in Research Versus in Production` section first, and the
  answer cited it as `[S1]` with PDF pages 38–39.
- The diagnostics panel rendered the route, rewrite, active scope, and
  retrieval mode.
- Clearing the conversation removed the visible transcript and session state.

### Chapter 5 summary citation repair verification

A live `Summarize Chapter 5` attempt previously produced `[N81:P155]`. Node 81
allows pages 153–154; page 155 belongs to canonical node 82. The validator was
correct to reject this mixed marker. The summary path now makes one bounded
regeneration with the exact validator errors rather than guessing whether the
node or page should change.

The real Chapter 5 command was rerun after the fix and wrote a validated
summary successfully. The output used `[N81:P153]`, `[N81:P154]`,
`[N82:P155]`, and `[N82:P156]` consistently. Offline tests deliberately inject
an invalid first draft, verify a successful feedback-driven second draft, and
verify that valid first drafts still use one call.

### Definition of done

- Satisfied for the minimal conversational milestone.
- Scoped filtering, sufficiency retries, and full multi-turn evaluation are
  deliberately not part of this definition.

## Batch 4 — sufficiency, retry, and grounding — pending

### Goal

Prevent plausible retrieved text from being treated as adequate evidence.

### Work

- Add a structured evidence-sufficiency contract and prompt.
- Check whether evidence answers every requested part.
- Detect evidence that is related but not sufficient.
- Detect contradictory evidence.
- Broaden a preferred scope once when insufficiency justifies it.
- Abstain after the single failed retry.
- Add Python citation validation.
- Add semantic grounding judgment.
- Regenerate once after a grounding failure.
- Return explicit error/abstention reasons in `TurnResult`.

### Definition of done

- Unanswerable LoRA and current-pricing probes abstain correctly.
- Citation markers map to supplied evidence and canonical pages.
- The system cannot answer after an explicit insufficient decision.
- Retry behavior is bounded and visible in results/traces.

## Batch 5 — LangGraph orchestration — pending

### Goal

Wrap the tested pure components in a small inspectable graph.

### Proposed nodes

```text
analyze_turn
  -> resolve_scope
  -> choose_evidence_strategy
  -> retrieve_or_load_scope
  -> check_sufficiency
  -> broaden_once
  -> generate
  -> validate
  -> regenerate_once
  -> apply_state_update
  -> finish
```

### Requirements

- Explicit typed graph state.
- Conditional edges based on structured results.
- Exactly one retrieval broadening retry.
- Exactly one answer regeneration retry.
- No open-ended agent loop.
- LangSmith traces for the full graph and every model call.
- Pure component tests remain separate from graph tests.

### Definition of done

- A graph run can be diagrammed and stepped through.
- Route decisions, retrieval, retry, and state updates are visible in
  LangSmith.
- Graph output remains the existing `TurnResult`.

## Batch 6 — FastAPI, React, and later UI refinements — pending

### Goal

Expose the already working conversational coordinator through the required
portfolio API and small React client.

### Work

- Add async FastAPI endpoints over the coordinator.
- Add the required small functional React interface.
- Keep Gradio as the local development/demo surface.
- Add streaming only if long summaries materially need progress feedback.
- Render later sufficiency, retry, and LangSmith diagnostics after those
  components exist.

Suggested expandable diagnostics:

- Route.
- History dependency.
- Standalone query.
- Active/resolved scope.
- Retrieval mode.
- Retrieved evidence.
- Sufficiency/retry result.
- Exact references.
- LangSmith trace link.

### Required interaction examples

```text
Summarize Chapter 3.
Compare the three dataflow modes.
Which one is better for a system with many services?
How does reservoir sampling work?
Return to Chapter 3. Which mode is best for low latency?
Turn that answer into interview notes.
```

### Definition of done

- These mixed interactions work in one browser session.
- A second session does not share state.
- UI answers and CLI/API-compatible results use the same coordinator.
- The UI does not pretend that a clarification or abstention is a normal
  answer.

## Batch 7 — full evaluation and conversational hardening — pending

### Work

- Run targeted component tests after every material change.
- Run the complete 44-turn evaluation explicitly.
- Compare against the frozen Batch 1 baseline.
- Inspect every route, scope, state, retrieval, citation, and safety failure.
- Promote one selected run into a versioned baseline directory.
- Write an evaluation report explaining:
  - What improved.
  - What remained weak.
  - Which technique caused each improvement.
  - Latency, token usage, and approximate cost from LangSmith.
- Add regression tests for corrected failures.

Do not add query expansion, more agents, a different vector database, or
additional rerankers merely to improve an aggregate score. Tie each change to
a reviewed failure.

## Remaining project work after this feature

These are required by the overall portfolio but are not reasons to expand the
current conversational batch.

### Interview-question generation

- Select a canonical scope.
- Generate interview questions and model answers.
- Verify every answer against evidence.
- Include citations and difficulty/usefulness judgments.

### FastAPI

- Expose async conversation execution.
- Preserve the same contracts.
- Add structured errors and request IDs.
- Add streaming only if time remains and it helps long operations.

### Minimal React interface

- Build a functional client over FastAPI.
- Reuse the conversational behaviors proven in Gradio.
- Keep visual polish secondary to evidence and inspectability.

### Production shape

- Structured logging.
- Docker setup.
- CI.
- Tests around ingestion idempotency and API behavior.
- Architecture diagram.
- Documented one-command local setup.

### Portfolio artifacts

- Architecture walkthrough.
- Example chapter summary.
- Example interview session.
- Evaluation report.
- Engineering decision log.
- Demo video.

## Commands and configuration

Copy `.env.example` to `.env` and add secrets locally. Never commit real
credentials.

Expected live configuration:

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_GENERATION_MODEL=openai/gpt-5.6-luna
OPENROUTER_CONTROL_MODEL=x-ai/grok-4.5
OPENROUTER_CONTROL_REASONING=high

LANGSMITH_API_KEY=
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=agentic-study-partner
```

Build derived retrieval data:

```bash
uv run python -m scripts.build_chunks
uv run python -m scripts.build_vector_index
```

Run the current UI:

```bash
uv run python app.py
```

Run the agreed Batch 1 smoke set:

```bash
uv run python -m scripts.evaluate_multiturn \
  --conversation mt-001 \
  --conversation mt-009 \
  --conversation mt-010
```

Run one turn with prerequisites:

```bash
uv run python -m scripts.evaluate_multiturn --turn mt-009-t3
```

Run all turns only after inspecting targeted output:

```bash
uv run python -m scripts.evaluate_multiturn --all
```

## Continuation rules

When completing a batch:

1. Update the batch status and last-updated date in this document.
2. Record new files and contracts.
3. Record important deviations from frozen decisions and why they changed.
4. Record targeted and full evaluation run IDs.
5. Record test counts and commands.
6. Move the “next/current batch” marker.
7. Do not erase failure history; it is part of the portfolio evidence.

## Immediate next action

Evaluate the user-visible minimal conversation loop before adding another
runtime component:

1. Add a thin evaluator adapter over `execute_conversation_turn()`.
2. Start with the same three conversations: `mt-001`, `mt-009`, and `mt-010`.
3. Prioritize high-level route, standalone meaning, expected evidence,
   citation validity, grounded answer, and clarification.
4. Keep exact internal scope/state labels diagnostic; they are no longer the
   primary product gate.
5. Inspect failures and add only the smallest measured fix.
6. Do not add LangGraph, sufficiency retries, query expansion, or scoped
   retrieval until this working loop shows why they are needed.
