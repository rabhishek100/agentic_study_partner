# Agentic Study Partner

The project parses a PDF into a table-of-contents-aligned `ParsedBook`, then
persists that parser output in lossless primary SQLite storage.

The primary database intentionally contains no summaries, chunks, embeddings,
keyword indexes, or other derived data. It stores only source metadata, the TOC
hierarchy, ordered content blocks, tables, and images.

## Architecture

```text
PDF
 └─ parsing.parser.parse_book()
     └─ ParsedBook
         └─ storage.sqlite.ingest_book()
             └─ SQLite
                 ├─ books
                 └─ nodes
                     └─ content_blocks
                         ├─ table_blocks
                         └─ image_blocks
```

The implementation has four core files:

- `parsing/models.py`: parser output models.
- `parsing/parser.py`: PDF parsing and JSON caching.
- `storage/schema.sql`: the canonical five-table schema.
- `storage/sqlite.py`: database setup, validation, ingestion, and restoration.

The two scripts are intentionally thin:

- `scripts/parse_book.py`: parse the configured PDF.
- `scripts/import_book.py`: rebuild SQLite from cached parser output.

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

## Tests

```bash
uv run python -m unittest discover -s tests -v
```
