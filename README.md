# Agentic Study Partner

The project parses a PDF into a table-of-contents-aligned `ParsedBook`,
persists that parser output in lossless primary SQLite storage, and builds
disposable BM25 and Chroma vector indexes from citation-aware chunks.

The primary database intentionally contains no summaries, chunks, embeddings,
keyword indexes, or other derived data. It stores only source metadata, the TOC
hierarchy, ordered content blocks, tables, and images.

## Architecture

```text
PDF
 └─ parsing.parser.parse_book()
     └─ ParsedBook
         └─ storage.sqlite.ingest_book()
             └─ data/books.sqlite3 (canonical)
                 ├─ books
                 └─ nodes
                     └─ content_blocks
                         ├─ table_blocks
                         └─ image_blocks
                             │
                             └─ retrieval.chunking.build_book_chunks()
                                 └─ data/retrieval.sqlite3 (derived)
                                     ├─ chunk_builds
                                     ├─ chunks
                                     ├─ chunk_sources
                                     └─ chunks_fts
                                         │
                                         └─ data/chroma/ (derived)
                                             └─ book_text_chunks
```

The canonical implementation has four core files:

- `parsing/models.py`: parser output models.
- `parsing/parser.py`: PDF parsing and JSON caching.
- `storage/schema.sql`: the canonical five-table schema.
- `storage/sqlite.py`: database setup, validation, ingestion, and restoration.

The derived retrieval layer is split into rebuildable indexing and explicit
ranking components:

- `retrieval/models.py`: chunk configuration and provenance contracts.
- `retrieval/chunking.py`: deterministic ordered-block chunk construction.
- `retrieval/schema.sql`: disposable chunk and FTS5 schema.
- `retrieval/sqlite.py`: atomic rebuild and BM25 search.
- `retrieval/vector.py`: idempotent local Chroma ingestion and vector search.
- `retrieval/search.py`: explicit BM25, vector, RRF hybrid, and reranked
  strategies.
- `retrieval/reranker.py`: pinned local cross-encoder scoring over a bounded
  hybrid shortlist.
- `retrieval/langchain.py`: thin LangChain adapter over those strategies.

The scripts are intentionally thin:

- `scripts/parse_book.py`: parse the configured PDF.
- `scripts/import_book.py`: rebuild SQLite from cached parser output.
- `scripts/inspect_scope.py`: resolve and inspect canonical book hierarchy.
- `scripts/study.py`: map explicit study queries and summarize complete scopes.
- `scripts/build_chunks.py`: rebuild chunks and the FTS5 index.
- `scripts/build_vector_index.py`: synchronize local Chroma from the chunks.
- `scripts/evaluate_retrieval.py`: compare all retrieval modes.

## Parse the source PDF

```bash
uv run python -m scripts.parse_book
```

The command uses the cached parser output when available. Delete the cache or
call `parse_book(..., force=True)` from Python when you intentionally want to
rebuild `parsed_book.json`.

## Import the existing parsed book

The importer uses `cache/parsed_book.json`, hashes its source PDF with SHA-256,
reads title/author/page count from PDF metadata, initializes the database, and
imports everything in one transaction:

```bash
uv run python -m scripts.import_book
```

The default database is `data/books.sqlite3`. Paths can be overridden when
needed:

```bash
uv run python -m scripts.import_book \
  --cache cache/parsed_book.json \
  --source sources/books/book.pdf \
  --database data/books.sqlite3
```

Importing the same PDF twice raises an error. Replacement must be explicit:

```bash
uv run python -m scripts.import_book --replace
```

`--replace` deletes the matching book and relies on cascading foreign keys to
remove its nodes and payloads before the fresh import. The delete and re-import
are part of the same transaction, so a failure restores the original book.

## Programmatic use

```python
from parsing.parser import PARSER_VERSION, load_parsed_book
from storage.sqlite import connect, ingest_book, initialize

book = load_parsed_book("cache/parsed_book.json")
connection = connect("data/books.sqlite3")
initialize(connection)

book_id = ingest_book(
    connection,
    book,
    title="Designing Machine Learning Systems",
    author="Chip Huyen",
    file_hash="<sha256-of-source-pdf>",
    page_count=389,
    parser_version=PARSER_VERSION,
)
```

To prove that canonical storage can reproduce the parser output:

```python
from storage.sqlite import restore_book

restored = restore_book(connection, book_id)
assert restored == book
```

## Inspect canonical study scopes

Chapter and section operations start from canonical SQLite rather than
retrieval results. The scope resolver accepts chapter numbers or titles,
returns the complete ordered subtree, and reports ambiguous or missing scopes
instead of guessing.

Inspect Chapter 1 and list its complete section hierarchy:

```bash
uv run python -m scripts.inspect_scope chapter 1 --book-id 1
```

Resolve a section within that chapter:

```bash
uv run python -m scripts.inspect_scope section \
  "Machine Learning Use Cases" \
  --chapter 1 \
  --book-id 1
```

Add `--show-content` to print a bounded preview of every canonical content
block. Tables use their flattened text; images show MIME/provenance metadata
without loading their base64 payloads. Complete-book inspection is also
available:

```bash
uv run python -m scripts.inspect_scope book --book-id 1
```

## Natural-language chapter and section operations

Explicit study requests are parsed without an LLM and mapped to the canonical
hierarchy. Listing sections is fully deterministic:

```bash
uv run python -m scripts.study \
  "What sections are present in Chapter 1?" \
  --book-id 1
```

Before paying for a summary, inspect the resolved scope and complete prompt
budget:

```bash
uv run python -m scripts.study \
  "Summarize Chapter 1" \
  --book-id 1 \
  --dry-run
```

To inspect every non-overlapping canonical block that will be sent:

```bash
uv run python -m scripts.study \
  "Summarize Chapter 1" \
  --book-id 1 \
  --dry-run \
  --dump-context outputs/chapter_1_context.txt
```

Run the complete-scope OpenRouter summary and optionally save validated
Markdown:

```bash
uv run python -m scripts.study \
  "Summarize Chapter 1" \
  --book-id 1 \
  --output outputs/chapter_1_summary.md
```

Section requests can be constrained by chapter:

```bash
uv run python -m scripts.study \
  "Summarize section Understanding Machine Learning Systems in Chapter 1" \
  --book-id 1
```

Summarization reads all meaningful canonical blocks in the resolved subtree;
it does not use top-k retrieval. Tables are flattened, image captions remain
text, image payloads are omitted, and known header/footer/page-break blocks are
skipped. The first version makes one model call only when the complete prompt
fits the configured context window. It never truncates or stores summaries.
Returned node/page citations and content-bearing node coverage are validated
before an output file is written. After validation, the application appends a
deduplicated `References` section that maps every citation used in the summary
to the book title, full chapter/section hierarchy, and exact PDF page. Missing
coverage for recap nodes titled `Summary` or `Conclusion` is reported as a
warning and does not block output; missing substantive nodes and invalid or
out-of-scope citations remain hard validation failures.

The prompt budget defaults to a 64,000-token context window with 4,000 output
tokens and a 1,000-token safety reserve. Override these explicitly for the
chosen OpenRouter model with `SUMMARY_CONTEXT_WINDOW_TOKENS`,
`SUMMARY_MAX_OUTPUT_TOKENS`, and `SUMMARY_SAFETY_MARGIN_TOKENS`.

## Build the BM25 index

The chunk builder reads canonical storage in read-only mode and writes
rebuildable output to `data/retrieval.sqlite3`:

```bash
uv run python -m scripts.build_chunks
```

The default policy targets 600 tokens, caps chunks at 800 tokens, uses up to
80 tokens of block-aligned overlap, and never crosses a TOC node. Table
placeholders are replaced with flattened table text. Image payloads are not
indexed; nearby captions and text remain searchable while image block
provenance is retained.

Navigation-only nodes such as the cover, table of contents, and index are
excluded from retrieval but remain unchanged in canonical storage. Rebuilding
the same source and configuration atomically replaces the prior derived build
and produces the same chunk IDs.

The parameters and database paths can be changed explicitly:

```bash
uv run python -m scripts.build_chunks \
  --source-database data/books.sqlite3 \
  --retrieval-database data/retrieval.sqlite3 \
  --book-id 1 \
  --target-tokens 600 \
  --max-tokens 800 \
  --overlap-tokens 80
```

## Build the local vector index

The vector index runs locally with no Docker or server:

```bash
uv run python -m scripts.build_vector_index
```

The first run downloads the pinned
`Alibaba-NLP/gte-modernbert-base` model. It embeds the book title, full
hierarchy path, and complete chunk body into a 768-dimensional vector, then
stores it in a cosine HNSW Chroma collection under `data/chroma/`.

The model supports 8,192 tokens, so the existing 800-token chunk limit is not
silently truncated. The command is idempotent: unchanged chunk IDs are
skipped, new chunks are embedded, and stale vectors are deleted. Index
provenance is written to `data/chroma/index_manifest.json`.

Embedding defaults to CPU for a reproducible no-GPU setup. On a compatible
CUDA installation, opt in with `EMBEDDING_DEVICE=cuda`.

To intentionally recreate an incompatible derived collection:

```bash
uv run python -m scripts.build_vector_index --reset
```

## Evaluate retrieval

The retrieval gold set and judgment policy live under `evaluation/`. Run:

```bash
uv run python -m scripts.evaluate_retrieval
```

This evaluates BM25, vector, hybrid, and reranked hybrid retrieval against the
same frozen node-level judgments. Hybrid uses unweighted reciprocal rank fusion
over the top 20 chunks from each retriever. The reranked mode applies the pinned
local `Alibaba-NLP/gte-reranker-modernbert-base` cross-encoder to that
20-candidate shortlist before retaining five distinct TOC nodes. To run only
the frozen BM25 baseline:

```bash
uv run python -m scripts.evaluate_retrieval --modes bm25
```

For the current 15-question seed set, the original BM25 baseline on the 12
answerable questions is:

| Metric | Result |
|---|---:|
| Recall@3 | 0.819 |
| Recall@5 | 0.889 |
| MRR@5 | 0.917 |

The remaining misses are concentrated in questions whose required evidence
spans multiple sections. Unanswerable questions are shown as retrieval probes
but are not scored until a downstream sufficiency/abstention step exists.

The current four-way comparison is:

| Method | Recall@3 | Recall@5 | MRR@5 |
|---|---:|---:|---:|
| BM25 | 0.819 | 0.889 | 0.917 |
| Vector | 0.681 | 0.847 | 0.750 |
| Hybrid | 0.778 | 0.931 | 0.896 |
| Hybrid + reranker | 0.847 | 0.931 | 0.917 |

The reranker shortlist has 1.000 candidate Recall@20. Reranking improves
early ordering over hybrid but does not improve Recall@5 on this seed set, so
plain hybrid remains the interactive default while the reranked mode stays
available for comparison. The first reranked query downloads the model; later
queries use the local Hugging Face cache. CPU is the reproducible default;
set `RERANKER_DEVICE=cuda` on a compatible CUDA setup.

### Detailed comparison report

Open
[`evaluation/retrieval_comparison.html`](evaluation/retrieval_comparison.html)
for the self-contained comparison containing:

- Overall and category-level metrics for all four methods.
- Every gold-set question and its expected source nodes and PDF pages.
- The top five results from each method with citations and excerpts.
- Explicit missing-node and unanswerable-probe diagnostics.

The report's rebuildable source artifact is generated from the frozen gold
set, derived SQLite chunks, and local Chroma collection:

```bash
uv run python -m scripts.build_retrieval_report
```

## Gradio chat

After adding `OPENROUTER_API_KEY` to `.env`, launch the local chat interface:

```bash
uv run python app.py
```

Each response shows source labels, full book hierarchy, and PDF pages.
The UI can switch between BM25, vector, hybrid, and reranked hybrid retrieval;
hybrid is intentionally the initial default. Search spans all indexed books
unless a `book_id` filter is provided programmatically. Conversation history
is visible, but each question is retrieved independently.

The CLI exposes the same retrieval choice:

```bash
uv run python -m scripts.ask_book \
  --retrieval-mode hybrid_rerank \
  "How does reservoir sampling work?"
```

## Tests

```bash
uv run python -m unittest discover -s tests -v
```
