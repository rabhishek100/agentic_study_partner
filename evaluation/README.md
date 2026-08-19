# Retrieval evaluation

This directory contains evaluation data derived from the canonical book
content now stored in Postgres. The frozen judgments were originally created
against the pre-cutover SQLite snapshot; audited migration preserves the same
node IDs, pages, hierarchy, and content. The local snapshot was retired after
that audit; references to it in frozen provenance describe how the judgments
were produced, not a runtime dependency. Each dataset records its own review
provenance; do not assume every artifact is human-verified.

## Source-first slice

`source_first_gold.json` is an 8-case seed for source-first study — reading a
book with questions anchored to the page in front of you. It measures four
things, and they are not the same kind of thing:

| Measure | What it says |
|---|---|
| Anchor recall | Did the passage the reader was looking at reach the model? |
| Anchor lead | Did it reach it *first*, carrying the lowest marker? |
| Rung accuracy | Did the recorded rung agree with what the case expected? |
| Selection resolution | Did a selection made on a rendered page match canonical text? |

and asserts one thing rather than scoring it: **no answer that left the
reader's sources may carry citation markers**. That is a defect, not a lower
number, so the runner exits non-zero when it happens.

The cases are written across the ladder on purpose — three answerable from the
anchored page, two needing the rest of the book, two answerable from neither,
and one whose selection cannot resolve at all. A set containing only questions
the book answers would measure nothing about escalation, which is the
behaviour most likely to be wrong.

```bash
# The deterministic half: no model, no cost.
uv run python -m scripts.evaluate_source_first --resolution-only --all

# The whole slice, which runs real turns and therefore spends real calls.
uv run python -m scripts.evaluate_source_first --all --output evaluation/runs/source-first.json
```

**Not human-reviewed.** The page anchors come from `retrieval_gold_seed.json`,
which is audited; the expected *rungs* are the author's judgement. In
particular each "answerable from the library" case assumes the open book does
not cover the question and something else in the library does, and nobody has
confirmed that against the library as it actually stands. The file says so in
its own `review_provenance`, and `test_source_first_evaluation.py` asserts it
keeps saying so.

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

## Video lecture conversation set

`video_gold.json` is the video half's gold set: 12 conversations and 43 turns
over the published Stanford CME 295 Lecture 1. It covers the question types a
reader actually asks of a lecture that has a slide deck attached to it:

| Kind | Where |
|---|---|
| Whole-lecture summary and topic inventory | vc-001 |
| Answer transformation, with no new facts | vc-001 |
| Exact-term and paraphrased retrieval from the transcript | vc-002, vc-003, vc-004 |
| Follow-ups whose referent is only in the history | throughout; 16 rewrite probes |
| Questions about what was on screen | vc-005, vc-012 |
| Requests the lecture does not answer | vc-006 |
| Course logistics, and a four-word follow-up | vc-007 |
| Detail from the last quarter of the lecture | vc-008 |
| Content from the linked document, by page | vc-009, vc-012 |
| Sections listed, and one explained | vc-010 |
| Stretches named by the clock | vc-011 |
| One thread across transcript, screen and deck | vc-012 |

Until it existed, every video feature was verified as working and none as
better. That is the gap `AGENTS.md` calls non-negotiable, and it is why this
set was built before anything further was added to the video path.

### What a judgment is

The unit is **a stretch of lecture** — a millisecond span plus the modalities
that can satisfy it — not an evidence-unit row. Evidence unit ids are derived
data and change on every rebuild, exactly as chunk ids do for books; the book
set judges TOC nodes for that reason, and the video equivalent of a node is
the moment a claim comes from. An anchor is satisfied when a retrieved item of
a listed modality overlaps the span at all, because retrieval windows and
caption cues are cut on different boundaries.

The linked document is the exception, and names **pages** instead. A slide deck
has no timestamps, and ingestion deliberately refuses to invent an alignment
between its pages and the lecture, so an anchor names whichever locator its
modality actually has. That content is also on screen — these were the slides —
so what a page anchor tests is not that the fact is exclusive to the file, but
that a reader who names the deck is answered from the deck, with a page they
can open.

Anchors were read off the canonical transcript rather than recalled. A pooling
pass over the first run's results then added visual modalities to eight anchors
whose slide is on screen inside their own span and genuinely carries the answer
— the Tokenization summary table lists the cons of character-level tokenization
in as many words. Anchors were left transcript-only wherever the slide did not
carry the claim. No span was widened to reach a slide and no anchor was changed
because a run had failed it.

### The two behaviours that were failing silently

**Summary coverage meant cited, not covered.** `video.lecture.evaluate_coverage`
counts a stretch satisfied when any marker points at it, so "the lecture then
moves on [S12]" satisfies window 12 and the runtime reports "24 of 24 required
stretches cited". `evals/video_coverage.py` measures the second thing: whether
the claim carrying the marker shares the distinctive vocabulary of that stretch,
where distinctive is TF-IDF computed across the lecture's own windows so a term
earns its place by being concentrated rather than frequent.

**Follow-up rewriting had no replay.** Every dependent follow-up is now
retrieved three ways against the same version and the same anchors — as the
reader typed it, as the router rewrote it, and as the gold rewrite — so the
only thing that differs between arms is the query.

### Measured results

43 turns, hosted database, hybrid retrieval, no answer judge. "First" is the
baseline the set was built against; "current" is after the retrieval and prompt
changes described below, on the expanded set.

| Measurement | First (29 turns) | Current (43 turns) |
|---|---:|---:|
| Route accuracy | 1.000 | 1.000 |
| History-dependency accuracy | 0.862 | 0.953 |
| Outcome accuracy | 0.931 | 1.000 |
| Required-evidence recall | 0.750 | 0.900 |
| Cited-evidence recall | 0.385 | 0.775 |
| Citation validity | 1.000 | 1.000 |
| Visual evidence present, where required | 1.000 | 1.000 |
| Execution errors | 0 | 0 |

The two figures are not strictly comparable — the current set is larger and
harder, containing document, section and time-range questions the first did
not. Both directions of that matter: the recall gain is understated because
the questions got harder, and no single number should be quoted without the
set version beside it.

Summary coverage on the whole-lecture summary: **24 of 24 stretches cited, 23
of 24 substantive, 1 vacuous citation.** The one flagged is a window straddling
the end of the historical timeline and the start of tokenization, where the
summary reported the timeline and said nothing about the tokenization half. A
borderline case, and the right call: the stretch was cited and only partly
covered. Earlier runs scored 24 of 24, so this varies run to run.

The rewriting replay, over 16 follow-ups the router rewrote in all 16 cases:

| Query sent to retrieval | Mean anchor recall |
|---|---:|
| As the reader typed it | 0.563 |
| As the router rewrote it | 0.875 |
| The gold rewrite | 0.938 |

Rewriting helped 7 follow-ups, hurt 2, and changed nothing for 7, capturing
**83% of the recall the gold rewrite shows was available**. That is the
evidence that the rewriting call earns its cost rather than merely resolving a
pronoun — and the two it hurts are stable across three runs, so the tail is
real rather than noise.

Retrieval alone, scored from the gold rewrites with nothing generated:
**anchor recall 0.909, 30 of 33 turns fully covered.**

### What the set found, and what it cost to fix

Retrieval changes are iterated in `--retrieval-only`, which scores anchor
recall from each turn's gold rewrite with nothing generated — one query
embedding per turn. Routing, answering and conversation state are held still,
so a change in the number is a change in retrieval and nothing else. Every row
below is one run of it.

The first diagnosis came from the modality mix rather than from the recall.
Across the set, **133 of 184 evidence slots went to frames** and almost exactly
one per answer went to the transcript — and that one held six words. The cause
is mechanical, not editorial: a frame's description runs to about 1,600
characters, a caption cue to about 30, so the frame wins on lexical and vector
scores nearly regardless of the question.

| Change | Anchor recall |
|---|---:|
| Baseline, 8 conversations | 0.761 |
| A share of the evidence per modality | 0.804 |
| Retrieved cues widened into passages | 0.848 |
| *Expanded to 12 conversations, harder questions* | *0.736* |
| Shortlist cut per modality instead of globally | **0.879** |

Three changes were tried and reverted because the measurement disagreed with
the theory. Cutting the **fused** ranking per modality as well — the
shortlists feeding it already are — cost 3 points: the fused order is what
tells the budget which frames are the *right* frames. A longer shortlist at
`limit * 8` cost 6 points, by letting weakly-matching frames into the fusion
for the budget to then spend its visual share on. And topping each modality up
to a floor after the global cut changed which turns failed without changing
how many. All three are recorded next to the constants they concern, so nobody
re-runs them.

The ablation then found a defect the recall number could not, because it only
appears when the *router's* rewrite is used rather than the gold one — and
tracing it properly took two attempts, which is worth recording.

Asked what the lecturer suggests instead of one-hot encoding, the answer
retrieved the word2vec stretch — anchor recall 1.0 — and described it as
"learning dense embedding vectors for the tokens from data" without ever
writing *word2vec*. The follow-up "what are its two variants?" was then
rewritten faithfully, by copying the only name the history contained, into a
query for a phrase the recording does not say. Retrieval missed and the turn
abstained.

The first fix went to the rewriting prompt, and one sample made it look like it
had worked. It had not: the router was already doing what it was told, and
there was simply no name in the history to copy. The real fix is in the answer
prompt, which now asks for the lecture's own names — word2vec, BLEU,
CoNLL-2003 — rather than descriptions of what they do. A description reads
perfectly well and leaves both the reader and every follow-up holding a phrase
that is not in the index.

The lesson generalises past this bug: in a multi-turn system an answer is also
an input, so a vague answer degrades every question that follows it, and the
damage shows up somewhere other than where it was caused.

Three turns still miss, and they are kept rather than tuned away:

- **vc-005-t3** and **vc-012-t1** reach the right region and stop just short of
  the anchor — the slide after the one wanted, the minute after the claim.
- **vc-010-t3** asks what "the section *after* word representation" introduces,
  which needs a section index the lecture does not publish. Its gold rewrite
  fails too, so this is a limitation rather than a rewriting fault.

### Reading these numbers honestly

- The set is **transcript-authored and pending human review**. Anchors were
  read from canonical content, not recalled, but no second reader has confirmed
  that each listed span is the narrowest or the only stretch that answers its
  question.
- **A laptop run has no frame images.** They live on the deployed volume, so
  visual evidence arrives as its OCR and description text without the image
  production attaches. Visual turns are therefore weaker here than in
  production, and the runner logs a warning when this is the case.
- ~~**The linked deck's extracted text is damaged.**~~ **Withdrawn — the deck
  is intact and the document-anchored turns were never handicapped.** This
  entry claimed that roughly half the deck's pages had lost most lowercase `s`
  characters ("Hi tory of attention", "Preci ion", "Data et") to a
  font-encoding fault, and that fixing it needed a resource re-ingest. Checked
  before doing that re-ingest, it does not reproduce anywhere:

  - Neither string, nor any of the others quoted, appears in any text or JSONB
    column of the production database — the sweep covered the whole `video`
    and `public` schemas, not just `resource_pages`.
  - Page 65 of the stored deck reads `History of attention` and page 21 reads
    `Datasets`, `Precision`, `observations` — the exact pages the quotes came
    from, with their `s` intact.
  - The stored text matches a fresh PyMuPDF parse of the source **exactly, on
    all 135 pages**, and the file's sha256 matches the resource's recorded
    `content_hash`, so the store is neither stale nor a parse of some other
    file. Poppler's `pdftotext` extracts the same pages cleanly too, so this
    is not one extractor's fallback covering for another's failure.
  - Across the document `s` runs at 5.9% of letters against an English norm of
    about 6.3%, no common letter is starved, and there are no U+FFFD
    replacement characters. The deck's fonts are Identity-H subsets, which is
    the family of font where this fault does occur — but not here.

  The pages were written once, on 2026-08-05, before the runs this section
  reports, and `persist_resource_pages` replaces rows outright, so no later
  re-ingest could have quietly repaired them. Whatever produced the quoted
  strings, it was not this index.

  `scripts/check_resource_text.py` is what re-derives all of that, so the
  correction is checkable rather than merely asserted, and so the next parse
  that does degrade is caught as a parse rather than inferred from a score.
- The two remaining history-dependency misses are turns labelled independent
  and classified dependent. One exposes a real over-trigger: the deterministic
  history-reference rule fires on "its" in "compared with its input", where the
  pronoun refers inside its own sentence rather than to an earlier turn.
- Cited-evidence recall (0.775) sits below retrieval recall (0.900). That gap
  now has a decomposition rather than a description; see below.
- **Nothing here is deployed.** Quality gates and evidence are derived data, so
  the published lecture keeps whatever it was last built with until it is
  re-ingested, and deploys are `railway up` per service.

### What the citation gap turns out to be

Cited-evidence recall was read as one failure: the answer citing fewer of the
stretches it reached than it should. Classifying every shortfall says that is
not what the number is mostly reporting. Six required anchors, across five of
43 turns, were reached and never cited — and **none of them were missed for
want of a citation**:

| Shape | Anchors | What it means |
|---|---:|---|
| `uncited` | 0 | The answer cited nothing, so no marker could land. |
| `cited_adjacent` | 1 | A marker landed just outside the anchor's window. |
| `cited_elsewhere` | 5 | Every marker is far away. |

Every one is on the `evidence_qa` route. The summarising routes score 1.000
because they cite structurally — the whole-lecture summary cites 24 of 24
stretches — so the gap lives entirely in short factual answers, which cite 1.2
of 8 supplied items on average against 4.5 for the turns with no gap.

The single `cited_adjacent` case is the judgment boundary, not the answer:
vc-003-t3 answers "what are its two variants?" with CBOW and skip-gram and
cites the Word2vec slide at 40:22, while the anchor's window closes at 40:01.
The frames are the same unchanged slide 21 seconds apart. Nothing is wrong
with the answer; an anchor cut on the clock cannot express that.

`cited_elsewhere` is where the two genuinely different cases hide, and no rule
separates them, so they were read:

- **Two are answers grounded in a stretch the anchor did not list.** vc-002-t2
  names all three tokenization levels and cites the summary table — the frame
  of it and page 33 of the deck — which states all three in as many words. The
  anchors name the transcript stretches where he says them instead. The answer
  is correct, grounded, and cited; the set asked for a different source of the
  same fact. vc-008-t2 is the same shape.
- **Two are wrong answers, and this is the real finding.** vc-005-t2 asks what
  changes on that slide next and describes a slide ten minutes later;
  vc-012-t2 asks what was on screen and describes one seventeen minutes away.
  In both, retrieval had the right frames — at ranks 4 and 8, and at rank 2 —
  and the answer used a different one.

So the number is doing something other than what it was named for. Retrieval
recall structurally cannot see the last two: the evidence was reached, so it
scores 1.000, and only where the marker landed reveals that the answer was
built from the wrong moment. That is worth keeping — but it means "cited
recall trails retrieval recall" should not be read as a citation-discipline
problem, and asking the prompt for more markers would move exactly zero of
these six.

The breakdown now ships in every run's `results.json` under `citation_gap`,
and re-derives from any frozen run without paying for retrieval:

```bash
uv run python -m scripts.analyze_citation_gap evaluation/runs/video/final-v3/results.json
```

### Running it

Validate the set before trusting a run. The canonical pass checks that every
anchor has evidence of a listed modality actually existing inside its span, so
a dataset that asks for the unreachable fails loudly instead of scoring zero:

```bash
uv run python -m scripts.validate_video_gold --owner-id "$VIDEO_OWNER_ID"
```

Run the smoke set — one rewriting thread and the three abstentions — before a
complete baseline:

```bash
uv run python -m scripts.evaluate_video --smoke --owner-id "$VIDEO_OWNER_ID"
```

```bash
uv run python -m scripts.evaluate_video --all --owner-id "$VIDEO_OWNER_ID"
```

`--judge-answers` adds the optional semantic rubric, and
`--no-rewrite-ablation` skips the two extra retrievals per probe. Runs write
`results.json` and a filterable `report.html` under
`evaluation/runs/video/<timestamp>/` and are gitignored. The owner is supplied
explicitly because the lecture belongs to a real account rather than to the
local bootstrap owner.

A document-anchored turn scoring badly can mean the retrieval missed or the
index is degraded, and those call for opposite work. This settles which,
without re-running anything, by re-parsing the source and comparing it to what
is stored:

```bash
uv run python -m scripts.check_resource_text --resource-id "$RESOURCE_ID" --pdf path/to/deck.pdf
```

It exits non-zero on empty pages, unmappable glyphs, a common letter absent
across the whole document, or any page that differs from a fresh parse. Drop
`--pdf` when the source lives on a volume this machine cannot reach; the
stored-quality checks still run.

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
