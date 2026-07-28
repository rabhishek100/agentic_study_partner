# Figure selection: measured, 2026-07-28

`study/figures.py` selects a figure when its canonical block sits in a node the
answer drew on *and* on a page that node's evidence covers. Two parameters were
chosen from the numbers below rather than from intuition.

Reproduce with:

```bash
uv run python -m scripts.evaluate_figures --owner-id <owner>
```

## Corpus

| Book | Figures | Nodes holding them |
|---|---:|---:|
| System Design Interview, 2nd ed. | 225 | 15 |
| Designing Machine Learning Systems | 119 | 74 |
| AI Engineering | 226 | 88 |
| ISLP | 240 | 133 |
| **Total** | **810** | — |

## How much the page rule costs

Distance from each figure to the nearest page of its own node's text:

| Distance (pages) | Figures |
|---:|---:|
| 0 | 809 |
| 1 | 1 |

**809 of 810 figures (99.88%) share a page with their node's text.**

This contradicts the concern recorded when the rule was designed — that a
figure one page outside the cited range would routinely be missed. It is not a
real failure mode in this corpus, so `PAGE_TOLERANCE` stays at 0. Widening it
would buy at most one figure and would start pulling in neighbouring sections'
images.

## Why the count is capped

System Design Interview concentrates 225 figures into 15 nodes — a shallow
table of contents gives each node a very wide page span. Citing one such node
would render dozens of images beneath a short answer, so `select_figures`
returns at most `DEFAULT_FIGURE_LIMIT` (6), preferring the best-ranked
evidence and then restoring reading order.

## Selection follows citations, not retrieval

A first cut selected from every retrieved chunk, which put four figures under
an answer that cited one source — from pages the reader was never pointed at.
Citations are now authoritative when a turn has any: a figure appears because
the answer pointed at its page. A cited node's full evidence page range still
counts, since one marker names one page while the passage can span several.
Evidence remains the fallback only for turns that cite nothing at all, such as
an abstention.

## What is still unmeasured

Precision and recall against human labels. The rule can only be scored that way
against questions whose correct answers have a known associated figure, and
authoring that set means reading the books. `scripts/evaluate_figures.py`
computes precision and recall as soon as such a file is supplied via `--gold`;
until then the honest statement is that the rule's *reachability* is measured
and its *usefulness* is not.

The two failure modes labels would expose:

- a decorative image on a cited page is indistinguishable from a substantive
  diagram, and
- a figure whose node is never cited is unreachable regardless of tolerance.

Both are addressed by the same upgrade — captioning figures with a vision model
at ingest so they compete in retrieval on their own merit — which per AGENTS.md
should follow a measured failure rather than precede it.
