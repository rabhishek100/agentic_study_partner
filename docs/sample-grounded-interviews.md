# Grounded sample interviews

Generated from canonical uploaded-book content on 2026-08-26. These were
ephemeral, read-only runs through the production question generator and adaptive
answer graph. The candidate replies were simulated. In realistic mode, the
candidate hears only the neutral reaction; scores and citations are available in
the post-interview report.

## System design: proximity service

- Source: `sdi_v2_slice`
- Scope: `Chapter 1 Proximity Service`, PDF pages 10–12
- Format: system design override
- Level: senior
- Planned duration: 30 minutes

### Turn 1 — requirements

**Interviewer:** Which requirement for Proximity Service would most change your
design, and why?

**Candidate:** The requirement that would most change my design is whether
business updates must appear in real time. Given the stated next-day
effectiveness, I would optimize the system for read-heavy, low-latency queries:
precompute or index businesses by geographic cells, replicate that relatively
stable dataset, and process owner changes asynchronously in a batch or scheduled
pipeline. That makes caching and horizontal scaling straightforward, especially
with high traffic in dense areas. If updates had to be reflected immediately, I
would need a substantially different consistency model: a write path that updates
the geospatial index quickly, cache invalidation or versioning, replication-lag
monitoring, and more careful handling of concurrent updates and deletes. That adds
operational complexity and can hurt query latency. I would still protect precise
user locations and minimize retention, but freshness is the requirement that most
directly changes the core storage, indexing, and caching architecture.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 5.0/5, source aligned, grounded at `[N29293:P10]`.

### Turn 2 — API and request flow

**Interviewer:** Draw the request flow for a nearby-business search, showing the
client-facing API boundary, service boundary, and data-source boundary.

**Candidate:** I would draw it left to right with three boundaries:

```text
[Client: mobile/web]
        |
        | GET /v1/search/nearby?latitude=...&longitude=...&radius=5000
        v
[API boundary: REST API / gateway]
        |
        | validated search request
        v
[Service boundary: Nearby Search Service]
        |
        | query: latitude, longitude, radius
        v
[Data-source boundary: Business Search Store]
        |
        | matching business records
        v
[Nearby Search Service] -> { total, businesses: [...] } -> [Client]
```

The API layer owns authentication, request validation, defaults the radius to
5,000 meters, and formats the response. The search service owns nearby-search
logic and translates the request into a datastore query. The data source could
be a geospatially indexed business store; I would keep that implementation behind
an interface so it can evolve independently. I would also show a separate flow
for `GET /v1/businesses/:id` when the user opens a detail page instead of
overloading the search response. Keeping search and detail separate adds a round
trip, but keeps search payloads and latency predictable.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 4.85/5, source aligned, grounded at `[N29293:P11]` and
`[N29293:P12]`.

### Turn 3 — data consistency and recovery

**Interviewer:** A nearby search returns IDs for businesses whose detail records
are missing. How would you diagnose the mismatch and restore correct results?

**Candidate:** I would first treat the geo index and business table as two
separate sources and quantify the mismatch. For a sample of search results, I
would look up each business ID in the business table and measure missing IDs by
region, index version, and age. I would check whether the IDs were deleted or
never committed, whether search and detail reads use different replicas, and
whether a cache is serving stale results. Then I would compare recent write,
delete, and indexing logs to find where propagation failed. For recovery, I would
remove orphaned IDs from the geo index and reindex active businesses in a
controlled batch with verification before switching traffic. Going forward, I
would enforce the business table as the source of truth, make updates and deletes
reliably publish indexing events, and run a periodic orphan-detection repair job.
A transactional write plus asynchronous indexing improves consistency, but makes
search briefly stale, so I would define and monitor that bound.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 5.0/5, source aligned, grounded at `[N29293:P12]`.

### Adaptive weak-answer check

**Interviewer:** Which requirement for Proximity Service would most change your
design, and why?

**Candidate:** Low latency.

**Interviewer:** Thanks. I want to clarify one part of that. You chose low
latency. What single design decision would you change to ensure users see nearby
businesses quickly?

Post-interview: partially correct, 2.95/5. The clarification targets the
candidate's exact omission and remains within page 10 evidence.

## ML/AI system design: RAG

- Source: `AI Engineering Guidebook`
- Scope: `RAG`
- Sampled pages: 107–129
- Format: system design override
- Level: senior
- Planned duration: 30 minutes

### Turn 1 — choosing RAG

**Interviewer:** For an assistant answering questions about frequently changing
private company documents, what primary requirement should drive choosing RAG
over repeatedly retraining the model?

**Candidate:** The primary requirement is that the assistant must stay current
with frequently changing, private company information without repeatedly
retraining the model. RAG retrieves the latest relevant content from a controlled
knowledge source at query time and supplies it as context. That avoids retraining
cost and delay while grounding answers in company documents. The trade-off is
retrieval complexity: ingestion, permissions, indexing, and search quality all
matter, and poor retrieval can still produce incomplete answers. When freshness
and private-data access dominate, updating the knowledge store is generally more
practical than continually changing model weights.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 5.0/5, source aligned, grounded at `[N14279:P107]` and
`[N14279:P108]`.

### Turn 2 — knowledge-access boundary

**Interviewer:** Define the primary requirement for a RAG knowledge-access layer
serving new or private information to an LLM without exceeding its context
window.

**Candidate:** The layer must provide relevant portions of new or private
information at inference time while keeping the augmented prompt within the
model's context-window limit. I would chunk and embed source data into a vector
index, embed the query, and retrieve the most semantically similar chunks. Those
chunks and the question become the LLM input, so the model can use current or
private knowledge without retraining or receiving the entire corpus. The key
constraint is relevance under a token budget: retrieval needs enough context for
correctness, but not so much that it crowds out the question. More chunks can
improve recall, but increase latency, cost, and distraction, so I would tune
retrieval and potentially rerank or compress results.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 5.0/5, source aligned, grounded at `[N14281:P110]` through
`[N14281:P114]`.

### Turn 3 — chunking component

**Interviewer:** Draw the chunking component in a RAG ingestion pipeline,
showing its input, output, and boundary that keeps produced text within the
embedding model's input limit.

**Candidate:** I would draw `document or extracted text -> chunker -> chunks ->
embedding model`. At the chunker input, the document can be larger than the
embedding model's context window. Inside the chunker I would label a hard
boundary: maximum chunk size no greater than the embedding-model token limit,
with headroom for metadata or formatting. The output is an array of independently
embeddable chunks. I would annotate optional overlap between adjacent chunks to
avoid losing boundary context. I generally prefer recursive splitting: preserve
paragraphs or sections first, split oversized units next, and retain a final hard
token safeguard. Overlap and smaller chunks can improve boundary recall but
increase embeddings, storage, and retrieval noise.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 5.0/5, source aligned, grounded at `[N14283:P120]` through
`[N14283:P122]`.

### Turn 4 — retrieval architecture trade-off

**Interviewer:** For queries whose wording differs substantially from document
language, would you choose direct vector retrieval or hypothetical-document
retrieval, and what trade-off drives your choice?

**Candidate:** For substantial vocabulary mismatch, I would generally choose
hypothetical-document retrieval, or HyDE. Instead of embedding the raw query, I
would generate a hypothetical answer or document and use that embedding to
retrieve actual source documents. The generated text can express concepts in
language closer to the corpus, improving recall when direct query-to-document
similarity is weak. The trade-off is added latency, cost, and risk: the
hypothetical document may introduce incorrect assumptions and bias retrieval
toward a plausible but wrong interpretation. I would constrain generation and
still validate the retrieved evidence. For simple fact queries, direct retrieval
is faster, cheaper, and less exposed to generation-induced error. In practice I
would make the choice adaptive based on query complexity or retrieval confidence.

**Interviewer:** Okay, thank you. Let's move on.

Post-interview: 5.0/5, source aligned, grounded at `[N14285:P128]` and
`[N14285:P129]`.

## Assessment

The questions are source-grounded but do not require the candidate to have read
the books. They ask for requirements, boundaries, diagrams, failure diagnosis,
and trade-offs. The adaptive weak-answer case now produces a specific
clarification instead of generic feedback.

This run is not yet evidence that a full 30-minute session always feels real.
The strong simulated candidate was too polished, and the proximity-service slice
contains only three meaningful phases, so that sample completed after three
primary questions. A stronger realism evaluation should mix candidate quality,
force at least one ambiguity/failure case, and measure session pacing separately
from question quality.
