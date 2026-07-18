# Agentic Study Partner

The project parses a PDF into a table-of-contents-aligned `ParsedBook`,
persists that parser output in lossless primary SQLite storage, and builds a
separate, disposable SQLite BM25 index from citation-aware chunks.

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
```

The canonical implementation has four core files:

- `parsing/models.py`: parser output models.
- `parsing/parser.py`: PDF parsing and JSON caching.
- `storage/schema.sql`: the canonical five-table schema.
- `storage/sqlite.py`: database setup, validation, ingestion, and restoration.

The derived retrieval layer has four corresponding files:

- `retrieval/models.py`: chunk configuration and provenance contracts.
- `retrieval/chunking.py`: deterministic ordered-block chunk construction.
- `retrieval/schema.sql`: disposable chunk and FTS5 schema.
- `retrieval/sqlite.py`: atomic rebuild and BM25 search.

The scripts are intentionally thin:

- `scripts/parse_book.py`: parse the configured PDF.
- `scripts/import_book.py`: rebuild SQLite from cached parser output.
- `scripts/build_chunks.py`: rebuild chunks and the FTS5 index.
- `scripts/evaluate_retrieval.py`: evaluate BM25 against the seed gold set.

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

## Evaluate BM25

The retrieval gold set and judgment policy live under `evaluation/`. Run:

```bash
uv run python -m scripts.evaluate_retrieval
```

For the current 15-question seed set, the initial node-level BM25 baseline on
the 12 answerable questions is:

| Metric | Result |
|---|---:|
| Recall@3 | 0.819 |
| Recall@5 | 0.889 |
| MRR@5 | 0.917 |

The remaining misses are concentrated in questions whose required evidence
spans multiple sections. Unanswerable questions are shown as retrieval probes
but are not scored until a downstream sufficiency/abstention step exists.

### Detailed HTML report

Open [`evaluation/bm25_report.html`](evaluation/bm25_report.html) for a
self-contained report containing:

- Overall and category-level retrieval metrics.
- Every gold-set question and its expected source nodes and PDF pages.
- The top five BM25 results, ranks, scores, citations, and chunk excerpts.
- Explicit missing-node and unanswerable-probe diagnostics.

The report's rebuildable source artifact is generated directly from the gold
set and retrieval database:

```bash
uv run python -m scripts.build_retrieval_report
```

## Tests

```bash
uv run python -m unittest discover -s tests -v
```
