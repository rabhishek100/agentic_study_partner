# Retrieval evaluation

This directory contains evaluation data derived from the canonical book
content in `data/books.sqlite3`. Each dataset records its own review
provenance; do not assume every artifact is human-verified.

## Multi-turn conversation set

`multiturn_gold.json` is the frozen synthetic seed set for implementing
conversation state and routing. It contains 11 four-turn conversations that
exercise chapter and section summaries, hierarchy listings, follow-up
questions, explicit scope switches, cross-chapter synthesis, answer
transformations, clarification, and grounded abstention.

The set is **model-adjudicated, not human-verified**. Independent subagent
roles reviewed its evidence/citations, conversation semantics, and
methodology. The runtime did not expose a selectable or independently
verifiable reviewer model identity, so the JSON makes no model-name claim.

Validate every node, page, route contract, dependency, hierarchy outline, and
complete-summary subtree against canonical SQLite:

```bash
uv run python -m scripts.validate_multiturn_gold
```

Build the self-contained review page:

```bash
uv run python -m scripts.build_multiturn_report
```

Open `evaluation/multiturn_gold.html` to filter the 44 turns by route,
history dependency, outcome, or free-text search and inspect the expected
scope, standalone meaning, state transition, evidence, near misses,
citations, and reviewer decisions. The HTML is derived and rebuildable; edit
the JSON only through a versioned correction, never by changing the report.

## Multi-turn runs

The evaluator replays each selected conversation through the real stateful
coordinator. Predicted state from one turn is passed into the next turn.

Copy the non-secret defaults from `.env.example`, add OpenRouter and LangSmith
credentials, and run the agreed three-conversation smoke set:

```bash
uv run python -m scripts.evaluate_multiturn \
  --conversation mt-001 \
  --conversation mt-009 \
  --conversation mt-010
```

Run the complete 44-turn baseline only after inspecting the smoke report:

```bash
uv run python -m scripts.evaluate_multiturn --all
```

Every run writes `results.json` and `report.html` beneath a unique directory
in `evaluation/runs/`. Routine runs are gitignored.

The report separates:

- Route, history dependency, retained/resolved scope, and outcome.
- Required-evidence recall and citation-to-evidence validity.
- Expected and predicted standalone queries for debugging.
- Optional answer-quality judgments when `--judge-answers` is supplied.

Gold standalone queries are not exact-string scored. The report retains the
exact comparison only as a debugging aid; retrieval coverage and the optional
answer judge are the semantic signals. Python-enforced schemas and canonical
scope selection remain the safety boundary.

## Seed set

`retrieval_gold_seed.json` is the first retrieval-only gold set. It is
deliberately small enough to review before retrieval implementation begins.
It contains 15 questions:

| Category | Count | Purpose |
|---|---:|---|
| Exact term | 5 | Establish the lexical BM25 baseline |
| Paraphrase | 4 | Expose vocabulary-mismatch failures |
| Multi-section | 3 | Test whether all required evidence is retrieved |
| Unanswerable | 3 | Test whether plausible near-matches cause false confidence |

The questions are frozen inputs for BM25, vector, hybrid, and reranking
experiments. Do not rewrite them to make a particular retriever look better.
If a question is found to be ambiguous or incorrectly judged, record the
reason and increment the dataset version.

## Judgment policy

The current judgment unit is a TOC-aligned `nodes` row because derived chunks
do not exist yet. A node is relevant when its content would be used as evidence
in a grounded answer to the question. This follows the practical relevance
definition used by NIST's Text REtrieval Conference (TREC).

Each answerable question identifies required evidence nodes and the narrowest
pages inspected during judgment. `required_coverage: "all"` means a retrieval
run must recover every listed node to cover the whole information need. For a
single-node question, this reduces to a normal hit.

For an unanswerable question:

- `expected_evidence` is empty.
- `near_miss_evidence` records nodes a lexical retriever might reasonably
  return even though they do not answer the question.
- The correct downstream behavior is to report insufficient evidence, not to
  answer from general model knowledge.

Page numbers are the physical PDF page numbers stored in
`content_blocks.page_number`, not the page labels printed in the book.

## How the set was produced

The book hierarchy, ordered text blocks, and flattened table blocks were
inspected directly in SQLite. Evidence summaries in the JSON are paraphrases
used to explain the relevance decision; they are not reference answers.

External research informed the evaluation method, not the book-specific
answers:

- [NIST TREC relevance judgments](https://trec.nist.gov/data/reljudge_eng.html)
  defines a document as relevant when its information would be used in a
  report on the topic and describes pooled human judgments.
- [BEIR](https://arxiv.org/abs/2104.08663) evaluates heterogeneous retrieval
  tasks and finds BM25 to be a robust baseline worth measuring before adding
  more complex retrieval.
- [UAEval4RAG](https://arxiv.org/abs/2412.12300) shows why RAG evaluation
  should explicitly include unanswerable requests and measure rejection
  behavior.

Every run reports node-level Recall@3, Recall@5, and mean reciprocal rank. For
multi-section questions, it also reports required-node coverage so a partial
retrieval is not mistaken for success. Unanswerable questions remain separate;
retrieval scores alone cannot determine abstention.

## Current comparison protocol

All methods rank the same rebuildable chunks:

- **BM25:** SQLite FTS5 over weighted section title, hierarchy, and body fields.
- **Vector:** hierarchy-aware text embedded locally with the pinned
  `Alibaba-NLP/gte-modernbert-base` model and searched in a cosine HNSW Chroma
  collection.
- **Hybrid:** unweighted reciprocal rank fusion with rank constant 60 over the
  top 20 chunks from BM25 and vector retrieval.
- **Hybrid + reranker:** the pinned local
  `Alibaba-NLP/gte-reranker-modernbert-base` cross-encoder scores the 20 RRF
  candidates using each chunk's hierarchy and complete body, then returns the
  five highest-ranked distinct TOC nodes.

Results are collapsed to distinct TOC nodes before scoring. Raw scores are not
compared across methods because BM25, cosine similarity, RRF, and cross-encoder
scores use different scales. Candidate Recall@20 is also reported for the
reranked mode: it shows whether the expected nodes reached the reranker at all.

Build and evaluate with:

```bash
uv run python -m scripts.build_vector_index
uv run python -m scripts.evaluate_retrieval
uv run python -m scripts.build_retrieval_report
```

After BM25 and one materially different retrieval method have produced ranked
results, review the union of their top results and add any genuinely relevant
nodes that this initial manual pass missed. This is a small-project adaptation
of TREC-style pooling and helps keep later comparisons fair.

## Known limits

This seed set is designed to expose retrieval behaviors, not to represent the
whole book statistically. Its answerable cases span Chapters 2 and 4–9 plus
Chapter 11. When the set grows toward the planned 30–50 questions, add direct
coverage for Chapters 1, 3, and 10, along with chapter-summary, interview-
question, and answer-citation judgments. Keep those later task types separate
from the retrieval-only baseline reported from this file.
