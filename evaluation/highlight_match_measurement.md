# Citation highlighting: measured, 2026-07-28

The parser keeps no geometry, so highlighting a cited passage means finding
the stored excerpt again in the text pdf.js renders. `frontend/lib/pdf-match.ts`
normalises both sides and slides a window over the page's text runs.

## Method

119 citations sampled at random from the chunks of all four production books,
each paired with the real text spans PyMuPDF extracts from that page of the
actual source PDF, then run through the shipped matcher.

## Result

| Book | Matched | Rate |
|---|---|---:|
| System Design Interview | 24/29 | 83% |
| Designing Machine Learning Systems | 24/30 | 80% |
| AI Engineering | 25/30 | 83% |
| ISLP | 24/30 | 80% |
| **Overall** | **97/119** | **81.5%** |

About four citations in five highlight the exact passage. The rest fall back
to a page-level highlight, and the viewer says so rather than highlighting
something close-but-wrong.

The rate is strikingly consistent across four books of very different
typesetting, which suggests the residual is inherent to comparing a stored
excerpt against a reflowed text layer rather than specific to any one PDF.

## What would move it

The stored excerpt is truncated to 400 characters and its whitespace already
collapsed, so some cases are matching against a fragment of the passage the
reader is looking for. Widening the excerpt would help the matcher and cost
nothing at query time.

Persisting block geometry at parse time would make this exact rather than
approximate, but it changes the canonical contract, bumps `parser_version`,
and requires re-parsing every book — a large cost against a 4-in-5 baseline
that degrades safely.
