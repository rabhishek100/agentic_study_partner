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

## Adopted: page-batched parallel extraction

Running the *same* parser concurrently is lossless by construction: every
page still goes through `hi_res`, so only the scheduling changes.

Measured on the deployed worker over 24 pages of a real book:

| | Time | Per page | Elements | Speedup |
|---|---:|---:|---:|---:|
| serial | 96.7s | 4.03s | 257 | 1.00x |
| parallel(2) | 46.1s | 1.92s | 257 | 2.10x |
| parallel(4) | 24.7s | 1.03s | 257 | 3.92x |
| parallel(6) | 16.8s | 0.70s | 257 | 5.76x |

A laptop had said 1.34x, because its four performance cores were already
saturated by ONNX. Measuring on the hardware that does the work was the
difference between rejecting this and adopting it.

The whole-book run matched the reference parse exactly: 177 sections, 4,201
blocks, 32 tables, 119 images, 821,672 characters, every delta zero.

Four workers is the default rather than six. Each process holds its own copy
of the layout model at roughly 1.2 GB, measured flat across successive
batches, so a long book peaks near 5.3 GB of the worker's 8 GB during
parsing. Assembly happens after the pool closes and costs far less: 569 MB
for a 200-page document.

## Page limit

The cap was 400 while parsing held a whole document in memory. Batches bound
per-process memory, so it is now 1,000, the original design target. A
1,000-page book projects to roughly 17 minutes on the worker at 4 workers,
and 40-100 MB of database rows depending on how many images it carries.

## Also measured, and not worth it

The `fast` strategy alone returns 95% of the characters in 2% of the time
and extracts **zero** tables and **zero** images. It is unusable for a store
whose contract is lossless canonical content.
