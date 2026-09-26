# Design decisions

## Overview

Agentic Study Partner is a multi-source RAG system for studying technical
books, papers, and lectures. It ingests source material into a canonical,
owner-scoped Postgres model and builds replaceable lexical and semantic
indexes. LangGraph is used only where decisions matter: routing a turn,
checking evidence sufficiency, widening scope, or adapting an interview. Every
answer cites pages or timestamps, complete summaries load the whole requested
scope, and insufficient evidence produces an abstention. Evaluation drove the
main complexity: BM25 was the baseline, hybrid retrieval improved coverage,
and reranking reached 100% Recall@5 on the small frozen retrieval set. The
project also keeps failed experiments and evaluation limitations visible.

## Walkthrough

1. A PDF or lecture is uploaded to private storage and processed by a durable,
   lease-based worker.
2. Canonical text, hierarchy, media, and provenance are stored separately from
   rebuildable search artifacts.
3. A graph classifies the request: complete-scope operation, retrieval QA,
   transformation, clarification, or external answer.
4. Retrieval combines lexical and semantic rankings; the default reranks a
   20-item fused shortlist.
5. Generation receives bounded evidence and must return resolvable citations.
6. Conversations, decisions, costs, provenance, and optional LangSmith traces
   make the result inspectable.
7. Frozen evaluation sets measure retrieval, routing, coverage, citations,
   OCR, and interview behavior separately.

## Key decisions

### Why not use vector search for everything?

Exact terms were already strong with BM25, and lexical search is cheap and
inspectable. Vector search helped some semantic cases but lowered first-hit
rank in the seed set. RRF avoids pretending raw BM25 and cosine scores are
calibrated; reranking improved final ordering. Each layer remains selectable so
the evaluation can isolate its value.

### Why are summaries not RAG?

Top-k retrieval optimizes relevance, not coverage. A chapter summary must
account for the complete subtree, so the system loads that scope directly,
chunks it only for context management, and fails if the configured budget
cannot hold it.

### What is agentic here?

The graphs own explicit choices and bounded loops: route, retrieve, check,
retry, synthesize; or evaluate, verify, adapt, continue/finish. Parsing,
persistence, and job orchestration stay deterministic. This makes a graph
trace useful instead of using an agent as decoration.

### How is hallucination controlled?

Evidence is delimited, citation markers must resolve, unsupported searches
abstain, scope widening is recorded, and complete operations use complete
source data. This controls provenance, not truth by itself. The source-first
evaluation demonstrated that valid citations can still fail entailment, so
claim-level judging remains an explicit gap.

### Why Postgres?

It holds canonical content, queues, conversations, FTS, and vectors with
transactions and owner constraints. The current scale does not justify a
separate vector database or distributed queue. Large bytes stay in object
storage.

### How do retries stop?

Every graph loop has a monotone counter or rung. Book grounding can visit each
rung once. Lecture/course retrieval broadens once. Decks get one repair pass.
Revision content gets at most two quality repairs and one fit repair. Interview
topics receive one primary question and at most one focused follow-up.

### What did evaluation change?

- Retrieval moved from BM25 to reranked hybrid after Recall@5 improved from
  88.9% to 100% on 12 answerable questions.
- Video retrieval added modality balancing, timeline coalescing, and query
  rewriting after evidence recall failures.
- Interview follow-ups became neutral and bounded after the development
  interaction set passed only 1/4 cases; the final development and held-out
  sets passed 4/4 each.
- OCR moved from Tesseract to Qwen with Gemini fallback after independently
  transcribed pages showed roughly 15% versus 38% character error.
- A proposed multi-turn routing change was rejected because measured accuracy
  decreased.

### What comes next

Human-review the interview evidence set, add claim-level entailment checks,
label figure relevance, broaden the corpus, and collect latency/cost
distributions. Only then consider approximate vector indexes or more complex
retrieval.

## Limitations

- Retrieval results come from a small source-specific seed.
- Several generative eval sets are synthetic or model judged.
- The system depends on hosted model availability and pricing.
- Live voice adds operational complexity and is optional.
- No study yet shows improved learning outcomes.

## Quick tour

To see the main ideas end to end: upload or open one book → ask an exact
question → open the cited page → summarize a complete chapter → ask an anchored
follow-up with the source lock on → inspect the LangSmith trace → compare
retrieval metrics → run or replay an adaptive interview.
