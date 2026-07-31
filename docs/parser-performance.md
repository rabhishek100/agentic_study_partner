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

Profiled warm on a laptop, one page at a time, the 3.53 s/page divides as:

| Component | s/page | Share | A GPU helps? |
|---|---:|---:|---|
| Tesseract OCR subprocess | 1.28 | 36% | no — CPU binary |
| ONNX inference (YOLOX, table-transformer) | 1.12 | 32% | **yes** |
| Waiting on the OCR thread pool | 0.35 | 10% | no |
| PIL encode of image payloads | 0.32 | 9% | no |
| PIL decode and page render | 0.23 | 7% | no |
| Everything else | 0.23 | 6% | no |

Only a third of the work is inference, which puts the ceiling on GPU
acceleration at **1.47x** by Amdahl's law even if inference became free. That
is why the parser stays on CPU: a GPU would turn a 92-minute book into a
66-minute one and add a second deployment target to do it.

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

**Those speedups are overstated and the serial row is why** — see "the serial
baseline was throttling itself" below. Against a serial parse that is not
fighting its own thread pool, four processes are worth about 2.2x. The
per-page figures in the other rows stand; only the ratios were wrong.

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

## Adopted: OCR only where the text layer is missing

`hi_res` defaults to `ocr_mode="entire_page"`: it shells out to Tesseract for
every page and re-reads text pdfminer has already taken out of the file.
Preflight only admits documents that carry a text layer, so that pass has
almost nothing to contribute. `individual_blocks` runs OCR only on regions
the text layer does not cover.

A serial profile made this look like the big win. Warm, one page at a time,
Tesseract was 1.28 s/page of a 3.53 s/page total, and switching modes on a
12-page sample took 4.05 s/page to 2.03 s/page — **2.0x**.

**On a laptop, through the production path, it is 1.19x.** Measured with
`extract_batched` at four workers:

| Book | Full-page OCR | Block OCR | Speedup | Deltas |
|---|---:|---:|---:|---|
| Reference, 389 pages, whole book | 2.45 s/page | 2.12 s/page | 1.16x | 32 tables, 119 images, 821,672 chars — all zero |
| AI Engineering, 60 pages | 2.51 s/page | 2.10 s/page | 1.19x | 577 elements, 13 tables, 31 images — all zero |

**On the deployed worker it is 1.75x.** Both modes, same container, same 24
pages, back to back:

| | Serial | Per page | Parallel(4) | Per page |
|---|---:|---:|---:|---:|
| Full-page OCR | 125.7s | 5.24s | 22.2s | 0.93s |
| Block OCR | 99.9s | 4.16s | **12.7s** | **0.53s** |
| Gain | | 1.26x | | **1.75x** |

Block OCR at other pool sizes: 0.99 s/page at two workers, 0.41 s/page at six.

Three measurements of the same change gave 2.0x, 1.19x and 1.75x. The number
that counts is the deployed one, and none of the three could have been
predicted from the others. **Measure the configuration that ships** — the same
lesson as the 1.34x laptop reading, arrived at from the opposite direction.

## Solved: the serial baseline was throttling itself

The benchmark kept reporting **superlinear** speedup — parallel(4) at 7.85x
on a pool of four, parallel(6) at 10.21x on a pool of six. A pool of four
cannot be eight times quicker, so the serial baseline had to be wrong.

It was. The container sees `os.cpu_count() == 48`; its cgroup quota is
`800000 100000`, i.e. **8 cores**. ONNX Runtime sizes its intra-op pool from
the visible count, so one parse process opened roughly 48 threads to run on
8 and spent its time being descheduled.

Capping the pool, same container, same 24 pages:

| Threads per process | Serial | Parallel(4) | Ratio |
|---|---:|---:|---:|
| 48 (library default) | 4.45 s/page | 0.50 s/page | 8.83x |
| 2 | 1.60 s/page | 0.53 s/page | 3.01x |
| **8 (the quota)** | **1.14 s/page** | 0.52 s/page | **2.21x** |

Two conclusions, and the second is the uncomfortable one:

**A serial parse was 3.9x slower than it needed to be** — 4.45 s/page against
1.14. Every path that parses a document whole pays this: a book shorter than
one batch, a single-worker configuration, and the fallback taken when the
process pool breaks.

**The batched path never had the problem, so every parallel speedup recorded
here has been inflated by a broken baseline.** Four processes are worth about
**2.2x**, not the 3.92x this document claimed, and six are not worth 5.76x.
The batched numbers themselves (0.50-0.53 s/page) were always correct and
have not moved through any of this; only the ratios were wrong, because they
were divided by a serial run that was throttling itself.

`PARSER_INFERENCE_THREADS` overrides the cap. It defaults to the cgroup
allowance, which costs the batched path nothing and is worth 3.9x to every
other path. A host with no quota to read is left alone, since it is not
oversubscribed in the first place.

### Still unexplained

The 535-page book parsed at 10.4 s/page across four processes, against
0.52 s/page here on the same hardware and the same pool width — a factor of
twenty. Thread oversubscription is now ruled out: it never affected the
batched path.

It is adopted anyway, because the content is identical and the speedup is
free. The whole-book run matched the reference parse on every metric. Four
strings differed, all of them OCR reading text off artwork rather than out of
the file:

```text
O'REILLY”                 <- the cover logo
01, non                   <- fragments of a chart axis
df.info() <class 'pandas.core.frame.DataFrame'> ...   <- a code screenshot
```

That is the trade: text drawn *inside* a figure stops becoming searchable
text. The figure itself is still captured as an image payload. For a book
whose text must be read off the page rather than out of the file, restore the
old behaviour with `PARSER_FULL_PAGE_OCR=1`.

Reproduce with:

```bash
uv run python -m scripts.compare_ocr_mode sources/books/<book>.pdf
```

`PARSER_VERSION` moved to `toc-hi-res-v2`, so a book half-committed under the
old mode is rebuilt rather than resumed against output it no longer matches.
It later moved to `toc-hi-res-v3` when the parser began consuming the exact
preflight-approved normalized outline, then `toc-hi-res-v4` when same-page
heading boundaries and repeated margin boilerplate became deterministic.
`toc-hi-res-v5` adds the bounded vector-page fallback below. Each
canonical-output change receives the same resume protection.

## Pathological vector pages

One reviewed source exposed a different performance failure: PDF page 134
contained 200,054 vector drawings. pdfminer traversed every path even under
its `fast` strategy, leaving the containing batch CPU-bound for more than 20
minutes. Every other inspected page contained at most 1,429 drawings, so
shrinking the batch merely moved the same stall into a smaller range.

The parser now counts vector drawings before scheduling batches. Ordinary
pages still use the same `hi_res` path. A page above 20,000 drawings is
isolated and represented by:

- native text blocks and coordinates read directly by PyMuPDF; and
- a JPEG render of the vector-content bounds.

This preserves searchable text and the non-text visual without asking
pdfminer to interpret hundreds of thousands of drawing operators. It is not
the broad selective-layout mode described below: no ordinary page changes
parser strategy. `PARSER_MAX_VECTOR_DRAWINGS` can override the conservative
ceiling if a measured corpus requires it.

On `2019BurkovTheHundred-pageMachineLearning.pdf`, the fallback selected only
PDF page 134. The reviewed 126-entry outline then completed all eight work
units and passed the extraction gate with 126/126 sections containing text,
276,115 characters, 4 tables, and 65 images.

## Page limit

The cap was 400 while parsing held a whole document in memory. Batches bound
per-process memory, so it is now 1,000, the original design target. A
1,000-page book projects to roughly 17 minutes on the worker at 4 workers,
and 40-100 MB of database rows depending on how many images it carries.

## Also measured, and not worth it

The `fast` strategy alone returns 95% of the characters in 2% of the time
and extracts **zero** tables and **zero** images. It is unusable for a store
whose contract is lossless canonical content.
