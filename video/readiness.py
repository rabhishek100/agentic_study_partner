"""What "ready, with reservations" actually means, in the reader's words.

A published version records ten quality gates and the measurements behind
them. The interface was shown only the verdict — "Ready (partial)" — which is
the one thing a reader cannot act on: it says something is missing without
saying what, how much, or whether it matters to the question they were about
to ask. A lecture whose transcript stops a percentage point short of the
threshold and one whose visual analysis half failed both said exactly that.

The sentences live here rather than in the interface because the interface
consumes the grounded API and generates nothing. Each one names the gate and
the number that failed it, so a reader can decide for themselves.
"""

from __future__ import annotations

from typing import Any


def _ratio(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _count(metrics: dict[str, Any], key: str) -> int | None:
    value = metrics.get(key)
    return int(value) if isinstance(value, (int, float)) else None


def _minutes(milliseconds: float) -> str:
    minutes = round(milliseconds / 60_000)
    if minutes < 1:
        return f"{round(milliseconds / 1000)} seconds"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _transcript_note(metrics: dict[str, Any]) -> str:
    ratio = _ratio(metrics, "transcript_completeness_ratio")
    if ratio is None:
        return "The transcript does not cover the whole lecture."
    duration = _ratio(metrics, "duration_ms") or 0
    missing = duration * (1 - ratio)
    covered = f"The transcript covers {ratio:.0%} of the lecture"
    if missing >= 30_000:
        return f"{covered} — about {_minutes(missing)} is not transcribed."
    return f"{covered}."


def _visual_note(metrics: dict[str, Any]) -> str:
    analysed = _count(metrics, "successful_visual_observation_count")
    total = _count(metrics, "frame_count")
    if analysed is None or not total:
        return "Some captured frames could not be interpreted."
    return f"{analysed} of {total} captured frames could be interpreted."


def _chapter_note(metrics: dict[str, Any]) -> str:
    covered = _count(metrics, "chapters_with_visual_evidence")
    total = _count(metrics, "chapter_count")
    if covered is None or not total:
        return "A chapter has no frame captured inside it."
    return f"{covered} of {total} chapters have a frame captured inside them."


def _gap_note(metrics: dict[str, Any]) -> str:
    gap = _ratio(metrics, "maximum_visual_gap_ms")
    if gap is None:
        return "A long stretch of the lecture has no visual evidence."
    return (
        f"One stretch of {_minutes(gap)} has no interpreted frame, so questions "
        "about what was on screen then may find nothing."
    )


def _semantic_note(metrics: dict[str, Any]) -> str:
    embedded = _count(metrics, "text_embedding_count")
    total = _count(metrics, "evidence_count")
    if embedded is None or not total:
        return "Semantic search is incomplete; keyword search still works."
    return (
        f"{embedded} of {total} passages are in the semantic index. The rest "
        "are found by keyword only, so a paraphrased question may miss them."
    )


# Ordered by how much each one narrows what the lecture can answer, so a
# reader shown several reads the one that matters most first.
_NOTES = (
    ("canonical_source", lambda _: "The original video file is no longer available."),
    (
        "transcript_evidence_complete",
        lambda _: "Part of the transcript is missing from the search index.",
    ),
    ("visual_evidence_present", lambda _: "No frames were captured from this lecture."),
    ("transcript_complete", _transcript_note),
    ("visual_analysis_success", _visual_note),
    ("no_visual_gap_over_five_minutes", _gap_note),
    ("every_chapter_visual", _chapter_note),
    (
        "timeline_frames",
        lambda _: "The opening or closing minutes have no frame captured.",
    ),
    ("semantic_index_complete", _semantic_note),
    (
        "required_resources_ready",
        lambda _: "A document marked required could not be read.",
    ),
)


def readiness_notes(quality_gates: dict[str, Any] | None) -> list[str]:
    """One measured sentence per gate this version did not pass.

    Empty for a version that passed everything, and for one recorded before
    gates were measured — an unexplained reservation is worse than none.
    """

    metrics = quality_gates or {}
    gates = metrics.get("gates")
    if not isinstance(gates, dict):
        return []
    return [
        describe(metrics)
        for key, describe in _NOTES
        if gates.get(key) is False
    ]


__all__ = ["readiness_notes"]
