"""Why cited-evidence recall sits below retrieval recall.

Retrieval recall asks whether an anchor's stretch was reached. Cited-evidence
recall asks whether the answer's markers point *into* that stretch. The second
number has been the lower of the two since the set was built, and the gap was
reported as one thing — "the answer cites fewer of the stretches it was given
than it reached" — which turns out to describe only part of it.

A shortfall has three shapes, and they call for opposite responses:

`uncited`
    The turn cited nothing at all, so no marker could land anywhere. A
    citation-discipline failure, and the only one the original reading of the
    number describes.

`cited_adjacent`
    A marker of a listed modality landed just outside the anchor's window. On
    a lecture this is usually the same slide one frame later — the deck does
    not change, the frame id does, and an anchor cut on the clock cannot tell
    them apart. The answer is grounded in the content the anchor is about;
    the judgment boundary is what it missed.

`cited_elsewhere`
    Every marker is far from the anchor. This is where the two genuinely
    different cases live, and no rule separates them: the answer either found
    the same fact in a stretch the gold set did not list, or it answered from
    the wrong part of the lecture entirely. Both look identical to a matcher,
    so this bucket is the one that earns a human read, and it is deliberately
    left as a question rather than resolved by a threshold.

The point of the split is that only `uncited` is fixed by asking the model to
cite more. `cited_adjacent` is fixed in the gold set or not at all, and
`cited_elsewhere` may not be a citation problem in the first place.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


# How far outside an anchor's window a citation may land and still be read as
# the same content. A slide is on screen for tens of seconds and is sampled
# repeatedly, so the frame an answer cites is routinely a neighbour of the
# frame the anchor's window contains. Wider than this and "adjacent" would
# start absorbing genuinely different moments: the shortest anchors in the set
# are around 60 seconds, so half a minute of slack cannot reach past one.
ADJACENT_TOLERANCE_MS = 30_000

UNCITED = "uncited"
CITED_ADJACENT = "cited_adjacent"
CITED_ELSEWHERE = "cited_elsewhere"


@dataclass(frozen=True)
class CitationGap:
    """One required anchor that retrieval reached and no marker pointed at."""

    turn_id: str
    route: str | None
    kind: str
    modalities: tuple[str, ...]
    reached_ranks: tuple[int, ...]
    cited_ranks: tuple[int, ...]
    nearest_citation_ms: int | None

    def summary(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "route": self.route,
            "kind": self.kind,
            "modalities": list(self.modalities),
            "reached_ranks": list(self.reached_ranks),
            "cited_ranks": list(self.cited_ranks),
            "nearest_citation_ms": self.nearest_citation_ms,
        }


def _overlaps(item: dict[str, Any], anchor: dict[str, Any]) -> bool:
    """The same judgment `evals.video` scores with, over serialized rows.

    Kept here rather than imported so this module reads a saved `results.json`
    without constructing the runtime's evidence objects — the diagnosis has to
    be runnable against a frozen run, not only against a fresh one.
    """

    if item.get("modality") not in set(anchor["modalities"]):
        return False
    pages = anchor.get("resource_pages")
    if pages is not None:
        return item.get("page_number") in set(pages)
    if item.get("start_ms") is None:
        return False
    start, end = int(anchor["start_ms"]), int(anchor["end_ms"])
    item_end = item.get("end_ms")
    item_end = item["start_ms"] if item_end is None else item_end
    return item["start_ms"] < end and item_end > start


def _distance_ms(item: dict[str, Any], anchor: dict[str, Any]) -> int | None:
    """How far outside the anchor's window this item sits, or None if it has
    no place on the clock to compare."""

    if anchor.get("resource_pages") is not None or item.get("start_ms") is None:
        return None
    start, end = int(anchor["start_ms"]), int(anchor["end_ms"])
    item_end = item.get("end_ms")
    item_end = item["start_ms"] if item_end is None else item_end
    if item["start_ms"] < end and item_end > start:
        return 0
    return min(abs(item["start_ms"] - end), abs(start - item_end))


def classify_turn(turn: dict[str, Any]) -> list[CitationGap]:
    """Every required anchor this turn reached without citing."""

    prediction = turn.get("prediction")
    if not prediction:
        return []
    anchors = [
        anchor
        for anchor in (turn.get("gold", {}).get("expected_evidence") or [])
        if anchor.get("role") == "required"
    ]
    if not anchors:
        return []
    evidence = prediction.get("evidence") or []
    cited_ranks = {
        citation["evidence_rank"] for citation in (prediction.get("citations") or [])
    }
    cited = [item for item in evidence if item.get("rank") in cited_ranks]

    gaps: list[CitationGap] = []
    for anchor in anchors:
        reached = [item for item in evidence if _overlaps(item, anchor)]
        if not reached or any(item["rank"] in cited_ranks for item in reached):
            continue
        listed = set(anchor["modalities"])
        distances = [
            distance
            for item in cited
            if item.get("modality") in listed
            for distance in [_distance_ms(item, anchor)]
            if distance is not None
        ]
        nearest = min(distances) if distances else None
        if not cited:
            kind = UNCITED
        elif nearest is not None and nearest <= ADJACENT_TOLERANCE_MS:
            kind = CITED_ADJACENT
        else:
            kind = CITED_ELSEWHERE
        gaps.append(
            CitationGap(
                turn_id=turn.get("turn_id", ""),
                route=prediction.get("route"),
                kind=kind,
                modalities=tuple(anchor["modalities"]),
                reached_ranks=tuple(item["rank"] for item in reached),
                cited_ranks=tuple(sorted(cited_ranks)),
                nearest_citation_ms=nearest,
            )
        )
    return gaps


def classify_citation_gaps(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Decompose a run's cited-recall shortfall into its three shapes."""

    gaps = [gap for turn in turns for gap in classify_turn(turn)]
    kinds = Counter(gap.kind for gap in gaps)
    return {
        "gaps": len(gaps),
        "turns_affected": len({gap.turn_id for gap in gaps}),
        "by_kind": {
            UNCITED: kinds[UNCITED],
            CITED_ADJACENT: kinds[CITED_ADJACENT],
            CITED_ELSEWHERE: kinds[CITED_ELSEWHERE],
        },
        "by_route": dict(Counter(gap.route for gap in gaps)),
        # Named individually. Three of these are a handful of anchors on one
        # lecture, and a total cannot be re-read later to check whether the
        # split still holds.
        "detail": [gap.summary() for gap in gaps],
    }


__all__ = [
    "ADJACENT_TOLERANCE_MS",
    "CITED_ADJACENT",
    "CITED_ELSEWHERE",
    "CitationGap",
    "UNCITED",
    "classify_citation_gaps",
    "classify_turn",
]
