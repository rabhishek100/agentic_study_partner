# Agentic Study Partner — Agent Instructions

This file is the standing ideology for anyone (human or agent) working on this
project. Read it before making scope or architecture decisions. Status and
setup instructions live in `README.md`; this file is about *why* and *what
counts as right*, not *what's currently built*.

## Purpose

An evaluation-driven study companion that ingests technical books, retrieves
grounded evidence, summarizes chapters, and generates interview-style
questions — using hierarchical RAG and an inspectable agentic workflow.

I'm building this to prepare for ML/AI engineering interviews while
demonstrating the exact skills those interviews test: retrieval system
design, evaluation methodology, and agentic orchestration I can defend by
explaining tradeoffs, not just describe as working. Every tool choice must be
justifiable in that conversation — technology is never the goal by itself.

Budget: roughly 100–120 focused hours over four weeks (about half a working
day per day).

## Skills this project must demonstrate

- **Retrieval**: BM25/FTS5 baseline first; add semantic retrieval or
  reranking only where evaluation shows the baseline misses.
- **Agentic RAG with LangGraph**: explicit state, routing, retries,
  decomposition — a workflow you can diagram and step through, not a
  black-box agent loop.
- **LangChain**: models, prompts, tools, retrievers, structured output.
- **FastAPI**: async execution, streaming.
- **A small React interface** — functional, not polished.
- **Evaluation**: a gold set with measured retrieval, citation, and
  hallucination metrics, plus a report on what improved and why.
- **Observability with LangSmith**: every LangGraph run and LLM call traced
  end to end, so retrieval steps, prompts, and routing decisions are
  inspectable after the fact — this is what makes "inspectable agentic
  workflow" a demonstrated property, not just a claim.
- **Production shape**: tests, Docker, CI, structured logs, idempotent
  ingestion — without engineering for imaginary scale.

## Non-negotiable principles

1. **Ground every answer.** Important claims carry section and page
   citations. No citation, no claim.
2. **Deterministic first.** Prefer plain Python. Reach for an LLM or agent
   call only where a decision, retry, or decomposition genuinely needs one.
3. **Source vs. derived data.** Parsed book content in SQLite is canonical
   and lossless. Chunks, indexes, embeddings, and summaries are derived —
   always rebuildable from source, never hand-edited.
4. **Evaluate before adding complexity.** Vector retrieval, reranking, and
   agent retries are justified by a measured failure mode, not by "it might
   help."
5. **Admit insufficient evidence** rather than inventing an answer when
   retrieval comes up empty or contradictory.
6. **Production-shaped, not production-scaled.** Validation, tests,
   idempotency, structured logs, and clear module boundaries: yes. Auth,
   multi-tenancy, Kubernetes, distributed infra, custom vector databases,
   complex multi-agent systems: no, unless explicitly requested.

## Scope

### Must have

- Book PDF ingestion into hierarchical SQLite (books, sections, text,
  tables, images).
- Chapter/section selection and complete chapter summarization with
  citations.
- Grounded question answering.
- BM25/FTS5 retrieval baseline.
- Interview-question generation with model answers verified against
  evidence.
- A small LangGraph workflow: plan → retrieve → check sufficiency → retry.
- LangSmith tracing wired into every LangGraph run and LLM call.
- A gold-set evaluation suite (roughly 30–50 questions).
- FastAPI, a minimal interface, tests, Docker, CI.

### Only if evaluation or remaining time justifies it

- Semantic/vector retrieval, reranking, hybrid retrieval.
- Saved hierarchical summaries with prompt and model provenance.
- PowerPoint ingestion using the same canonical content model.
- Streaming progress for long chapter/book operations.

### Explicit stretch goal — do not let this eat the month

Video transcript ingestion, episode notes/blog generation, timestamp-aligned
screenshot extraction, whole-book topic inventories, hosted deployment.
Transcript alignment and multimodal QC could consume the whole month without
improving the core RAG demonstration.

## Architecture

```text
PDF / PPT
    -> Canonical parsing and SQLite storage
    -> Rebuildable chunks, BM25 index, vectors, and summaries
    -> Hierarchy-aware retrieval
    -> LangGraph planning, validation, and retry   (traced end to end in LangSmith)
    -> Grounded answer or summary with citations
    -> FastAPI + minimal React interface
```

The agent plans and coordinates retrieval. It does not replace BM25 or
vector search, and it never executes unrestricted model-generated SQL.

## Retrieval strategy by request

| Request | Approach |
|---|---|
| Summarize a chapter | Retrieve the complete chapter subtree, summarize hierarchically |
| Explain a named term | BM25 over chunks, then hierarchy expansion |
| Explain a paraphrased concept | Add semantic retrieval if BM25 misses it |
| Construct a multi-part process | Decompose, retrieve each part, synthesize |
| Generate interview questions | Retrieve a selected scope, generate questions, verify answers against evidence |
| Find themes across a book | Reduce section summaries into chapter/book-level themes |

## Evaluation

Build a gold set of ~30–50 questions covering exact terms, paraphrases,
chapter summaries, multi-section synthesis, interview questions, and
deliberately unanswerable requests.

Track:

- Whether expected sections appear in retrieved context.
- Retrieval recall at a small `k`.
- Citation correctness.
- Chapter-summary coverage.
- Unsupported claims and hallucinations.
- Interview-preparation usefulness.
- Latency, token usage, approximate cost — pulled from LangSmith traces
  rather than hand-rolled timing/counting code.

Every new technique must improve at least one measured failure.

## Definition of done

1. Ingest at least one complete technical book reliably.
2. Rebuild all derived retrieval data from canonical storage.
3. Produce a useful, cited summary of any selected chapter.
4. Answer grounded questions and admit when evidence is insufficient.
5. Generate cited interview questions and model answers.
6. Include an evaluation report showing failures and measured improvements.
7. Run locally through a documented Docker setup.
8. Expose a small API and usable interface.
9. Be explainable end to end in a short architecture walkthrough.

## Portfolio output

Clear README and architecture diagram, one-command local setup, a short demo
video, an example chapter summary and interview session, retrieval/answer
evaluation results, and a short engineering log of decisions, failed
experiments, tradeoffs, and next steps.

The project must demonstrate not just that it works, but that I can explain
why it's designed this way, how its quality is measured, and where I chose
simplicity over unnecessary complexity.
