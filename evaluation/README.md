# Retrieval evaluation

This directory contains evaluation data derived from the canonical book
content now stored in Postgres. The frozen judgments were originally created
against the pre-cutover SQLite snapshot; audited migration preserves the same
node IDs, pages, hierarchy, and content. The local snapshot was retired after
that audit; references to it in frozen provenance describe how the judgments
were produced, not a runtime dependency. Each dataset records its own review
provenance; do not assume every artifact is human-verified.

## Interview-answer seed

`interview_answer_seed.json` is a 30-case diagnostic set for the
interview-preparation prompt profile. It covers:

| Book | Cases |
|---|---:|
| An Introduction to Statistical Learning with Applications in Python | 10 |
| System Design Interview | 10 |
| AI Engineering | 7 |
| Designing Machine Learning Systems | 3 |

The cases include concept explanations, comparisons, scenarios, seven
system-design walkthroughs, three chapter reviews, five contextual follow-ups,
nine explicit depth overrides, and three deliberately unanswerable requests.
Each answerable case defines semantic must-cover points, common failure guards,
likely interviewer probes, scoring dimensions, and candidate page-level
evidence anchors.

Validate the schema and inspect its coverage:

```bash
uv run python -m scripts.validate_interview_dataset
```

Pass `--database-url "$MIGRATION_DATABASE_URL"` to also check every source
hash, node ID, hierarchy path, and page range against the hosted canonical
database. This structural check still does not replace semantic human review.

This set is **knowledge-authored and pending human evidence review**. The
questions and rubrics were created from model knowledge, then matched to the
production books' canonical hierarchy. Three Designing Machine Learning
Systems cases reuse topics already reviewed in the existing gold sets; the
remaining page mappings are candidate anchors. Do not report aggregate answer
quality from this seed as a human-verified result until each mapping and
must-cover criterion has been checked against the cited pages.

The next promotion step is to add compact reference answers or evidence
summaries, record reviewer decisions, and change `review.status` only when
those checks are complete. Prompt experiments should freeze this version
rather than editing questions to favor a candidate prompt.

The seed is a development regression set, not a source of production rules.
Runtime routing and retrieval must not contain its question text, book titles,
case IDs, node IDs, or topic-specific exceptions. Changes should operate on
general signals such as requested depth, answer intent, hierarchy structure,
and retrieval scores, then be checked on held-out uploaded content before a
release claim about generalization.

### Interview-answer runs

Run the diagnostic smoke set (`int-001`, `int-011`, and `int-030`) before a
paid complete baseline:

```bash
uv run python -m scripts.evaluate_interview_answers \
  --smoke \
  --database-url "$MIGRATION_DATABASE_URL" \
  --judge-answers
```

The runner executes each case through the real stateful coordinator. Follow-up
cases first replay their setup turn, and every case receives the UI depth
specified by the dataset. It records:

- route, outcome, answer archetype, and effective-depth accuracy;
- required candidate-node recall and citation validity;
- avoidance of the unwanted generic “based on the evidence” answer preface;
- prompt-profile provenance and end-to-end case latency;
- optional 0–4 rubric judgments for grounded correctness, interview
  readiness, coverage, depth adherence, clarity, follow-up quality, and
  citation quality.

The reader-facing API stores compact evidence previews, but the evaluation
runner reloads the complete retrieved chunks for the judge. Otherwise a claim
supported later in an 800-token chunk can be falsely labeled unsupported from
its 400-character preview. Candidate evidence recall is hierarchy-aware: a
content-bearing descendant covers a required heading-only parent anchor.

Each run writes `results.json` and a searchable, filterable `report.html`
under `evaluation/runs/interview/<timestamp>/`. It also rewrites
`checkpoint.json` after every completed case. Runs are gitignored. The
optional judge is diagnostic rather than a safety gate; a judge failure is
recorded separately and does not discard the generated answer.

Run selected cases with repeated `--case int-NNN`, or run the complete frozen
set only after reviewing the smoke output:

```bash
uv run python -m scripts.evaluate_interview_answers \
  --all \
  --database-url "$MIGRATION_DATABASE_URL" \
  --judge-answers
```

`--prompt-profile path/to/profile.json` accepts either a raw `PromptProfile`
object or the API's prompt-settings response, enabling paired comparisons
without changing the frozen questions.

Hosted generation and judge requests are bounded to 90 seconds with retries
disabled for evaluation. A separate 240-second case deadline bounds setup
turns, summary repairs, answer generation, and judging together, so a provider
that continues sending a slow response cannot hold the baseline indefinitely.
Override these with `--request-timeout-seconds` and
`--case-timeout-seconds`. Resume after an interruption while retaining
successful cases:

```bash
uv run python -m scripts.evaluate_interview_answers \
  --all \
  --database-url "$MIGRATION_DATABASE_URL" \
  --judge-answers \
  --resume evaluation/runs/interview/<timestamp>/checkpoint.json
```

If the judge rubric changes, rejudge saved generations without repeating the
more expensive answer calls:

```bash
uv run python -m scripts.rejudge_interview_results \
  evaluation/runs/interview/<timestamp>/results.json
```

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
complete-summary subtree against canonical Postgres:

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

Render the current retrieval comparison artifact without rerunning paid
provider evaluation:

```bash
uv run python -m scripts.render_retrieval_report
```

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

The judgment unit is a TOC-aligned `nodes` row rather than a version-specific
chunk. A node is relevant when its content would be used as evidence
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
originally inspected directly in SQLite. The migration audit confirms that
Postgres restores the same parsed book and canonical counts. Evidence
summaries in the JSON are paraphrases used to explain the relevance decision;
they are not reference answers.

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

- **BM25:** Postgres-normalized weighted title, hierarchy, and body lexemes,
  with a GIN-backed match predicate and deterministic BM25 scoring.
- **Vector:** hierarchy-aware text embedded with
  `openai/text-embedding-3-large` through OpenRouter and searched exactly in
  Postgres `vector(3072)` rows.
- **Hybrid:** unweighted reciprocal rank fusion with rank constant 60 over the
  top 20 chunks from BM25 and vector retrieval.
- **Hybrid + reranker:** the configured hosted reranker scores the 20 RRF
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

The first audited Postgres run produced:

| Method | Recall@3 | Recall@5 | MRR@5 |
|---|---:|---:|---:|
| BM25 | 0.819 | 0.889 | 0.917 |
| Vector | 0.903 | 0.931 | 0.792 |
| Hybrid | 0.847 | 0.931 | 0.847 |
| Hybrid + reranker | 0.889 | 1.000 | 0.958 |

The reranker candidate Recall@20 is 1.000. The current
`retrieval_comparison_artifact.json` contains per-question evidence for this
run.

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
