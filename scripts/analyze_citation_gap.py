"""Decompose a video run's cited-evidence recall shortfall.

Reads a saved ``results.json`` rather than replaying the lecture, so the
diagnosis can be re-derived from any frozen run — including the ones already
in `evaluation/runs/video/` — without paying for retrieval again.

    uv run python -m scripts.analyze_citation_gap \
        evaluation/runs/video/final-v3/results.json

`evals.video_citations` explains what the three kinds mean and which of them a
prompt change could actually move.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.video_citations import (
    CITED_ADJACENT,
    CITED_ELSEWHERE,
    UNCITED,
    classify_citation_gaps,
)


EXPLANATIONS = {
    UNCITED: "the answer cited nothing, so no marker could land",
    CITED_ADJACENT: "a marker landed just outside the anchor — usually the same slide, one frame later",
    CITED_ELSEWHERE: "every marker is far away — another valid stretch, or the wrong part of the lecture",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="a run's results.json")
    parser.add_argument(
        "--json", action="store_true", help="emit the breakdown as JSON"
    )
    arguments = parser.parse_args()

    payload = json.loads(arguments.results.read_text())
    turns = payload.get("turns") or []
    breakdown = classify_citation_gaps(turns)

    if arguments.json:
        print(json.dumps(breakdown, indent=2))
        return 0

    summary = payload.get("summary") or {}
    retrieved = summary.get("required_evidence_recall")
    cited = summary.get("cited_evidence_recall")
    print(f"{arguments.results}")
    if retrieved is not None and cited is not None:
        print(
            f"  retrieval recall {retrieved:.3f} -> cited recall {cited:.3f} "
            f"over {summary.get('turns')} turns"
        )
    print(
        f"  {breakdown['gaps']} required anchors reached and not cited, "
        f"across {breakdown['turns_affected']} turns"
    )
    for kind, count in breakdown["by_kind"].items():
        print(f"    {count:>3}  {kind:<16} {EXPLANATIONS[kind]}")
    print("  by route:", ", ".join(
        f"{route}={count}" for route, count in breakdown["by_route"].items()
    ) or "none")
    print()
    for gap in breakdown["detail"]:
        nearest = gap["nearest_citation_ms"]
        distance = "no comparable citation" if nearest is None else f"{nearest / 1000:.0f}s away"
        print(
            f"  {gap['turn_id']:<12} {gap['kind']:<16} {distance:<24}"
            f" reached {gap['reached_ranks']} cited {gap['cited_ranks']}"
        )
    if breakdown["by_kind"][CITED_ELSEWHERE]:
        print(
            "\n  `cited_elsewhere` is the bucket that needs a reader: a matcher "
            "cannot tell\n  an answer grounded in an unlisted stretch from an "
            "answer grounded in the\n  wrong one."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
