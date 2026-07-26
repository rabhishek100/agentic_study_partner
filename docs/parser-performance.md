# Parser performance

Parsing dominates ingestion. This records what was measured, what was tried,
and why the obvious optimisation was rejected.

## Where the time goes

A 269-page book took 31 minutes end to end on the deployed worker:

| Stage | Time | Share |
|---|---:|---:|
| Parsing | 24m 51s | 80% |
| Persisting | 5m 18s | 17% |
| Chunking, embedding, verifying | 48s | 3% |

Persisting was fixed by batching inserts and is now seconds. Everything else
is parsing, at roughly 5.5 s/page on Railway and 3.9 s/page on a laptop.

`hi_res` never reads the text a digital PDF already contains. For every page
it renders an image, runs YOLOX layout detection, runs table-transformer on
detected tables, and extracts text and images per region. That is what
produces the tables and images the canonical store holds, and it costs the
same on a page of plain prose as on a page of diagrams.

## Rejected: selective layout extraction

The idea: classify pages cheaply with PyMuPDF, run `hi_res` only on pages
holding an image or a table, and run the text-only parser elsewhere.

A 60-page sample looked excellent: 2.19x faster with 9/9 tables, 20/20
images, and 100% of characters. **The whole-book comparison contradicted it.**

Reference book, 389 pages, against a full `hi_res` parse:

| Classifier | Time | Speedup | Tables | Images | Characters |
|---|---:|---:|---:|---:|---:|
| Whole-document `hi_res` | 29.0 min | 1.00x | 32 | 119 | 821,672 |
| images or `find_tables` | 10.0 min | 2.90x | **23** | **117** | 817,262 |
| ...plus vector drawings | 12.5 min | 2.32x | **24** | 119 | 817,877 |

Adding vector-drawing detection recovered both missing images and exactly
one table. Eight tables stayed invisible.

The reason is structural, not a tuning problem. `hi_res` finds tables
*visually*, including borderless ones laid out purely with whitespace.
PyMuPDF's `find_tables()` looks for ruling lines and aligned text, and a
borderless table has neither reliable signal. No cheap classifier can
predict what a vision model will see; that is why the vision model is there.

Losing a quarter of a book's tables silently is not a trade worth 2.3x, so
selective extraction is **off by default**. `PARSER_SELECTIVE_LAYOUT=1`
enables it for a document whose owner accepts the risk — a book known to
contain no tables, for instance.

Reproduce with:

```bash
uv run python -m scripts.compare_extraction sources/books/<book>.pdf
```

It exits non-zero if tables, images, or text regress.

## What is left

The safe way to speed this up is to run the *same* parser concurrently rather
than to run less of it. Page-batched extraction with a process pool is
lossless by construction: every page still goes through `hi_res`.

Measured on a laptop, four processes over 24 pages gave only 1.34x, because
each process reloads the model (~20s) and the machine's four performance
cores were already saturated by ONNX. Both costs amortise better on a longer
book and on a machine with more headroom: the deployed worker averaged 1.37
of its 8 vCPU during a real parse. That needs measuring on the worker itself
before any number is promised.

Page batching is also what the ingestion design already wants for other
reasons: bounded memory, per-batch checkpoints, and real per-page progress
instead of a stage that reports nothing for twenty-five minutes.
`parsing.parser._subset` and `_restore_page_numbers` exist for this, with
tests, and are what a batched implementation would build on.

## Also measured, and not worth it

The `fast` strategy alone returns 95% of the characters in 2% of the time
and extracts **zero** tables and **zero** images. It is unusable for a store
whose contract is lossless canonical content.
