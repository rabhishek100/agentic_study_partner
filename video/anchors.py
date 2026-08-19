"""Resolve where the reader is in a lecture to canonical evidence units.

The lecture counterpart of `study.anchors`, and the rules are the same one
level up: what the reader points at is not evidence, and what it *resolves to*
is. What differs is only the identity of the thing a pointer names — a book
anchor lands on chunks, a lecture anchor lands on evidence units — which is
exactly the difference `study.side_context` was built to be neutral about.

Two things are specific to a lecture and neither is cosmetic.

**A unit belongs to an ingestion version.** A re-ingested lecture is a
different cut with different unit ids, so resolution asks the same
`published_version_id` retrieval asks. Anchoring against a stale version would
point a timestamp at a different recording.

**A moment is not an instant.** A question asked at 12:04 is almost always
about what was just said, so the ambient window looks backwards further than
it looks forwards. A symmetric window spends half its budget on content the
reader has not heard yet.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from study.contracts import LectureMomentAnchor, LectureStretchAnchor
from study.side_context import AnchoredSource
from video.retrieval import VideoNotReadyError, published_version_id

# How far a bare moment reaches, and the asymmetry is the point: a minute and a
# half of what was just said, half a minute of what follows. Chosen to cover a
# whole explanation rather than a sentence, since the thing a reader asks about
# at 12:04 usually started before 12:04.
MOMENT_LOOKBACK_MS = 90_000
MOMENT_LOOKAHEAD_MS = 30_000

# More than the book's three, deliberately. A chunk is most of a page; a
# transcript unit is a sentence. The cap is chosen to be comparable in *content*
# to three chunks rather than comparable in count, so an anchored lecture turn
# and an anchored book turn cost about the same.
MAX_UNITS_PER_ANCHOR = 8


@dataclass(frozen=True)
class ResolvedLectureAnchor:
    """One lecture anchor and the evidence units it names."""

    anchor_id: str
    kind: str
    evidence_ids: tuple[str, ...]
    label: str
    matched: bool = True
    dropped: tuple[str, ...] = ()

    def as_source(self) -> AnchoredSource:
        return AnchoredSource(
            anchor_id=self.anchor_id,
            label=self.label,
            identities=self.evidence_ids,
            # A lecture anchor carries no transcription of its own: the reader
            # marked a stretch of time, and the words are the transcript units
            # the answer will cite. There is nothing to quote separately, and
            # nothing that could fail to match.
            selected_text="",
            matched=self.matched,
        )


def timestamp(milliseconds: int) -> str:
    """`12:04`, or `1:12:40` once a lecture runs past the hour."""

    total = max(0, milliseconds) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _window(anchor: LectureMomentAnchor | LectureStretchAnchor) -> tuple[int, int]:
    if isinstance(anchor, LectureStretchAnchor):
        return anchor.start_ms, anchor.end_ms
    return (
        max(0, anchor.timestamp_ms - MOMENT_LOOKBACK_MS),
        anchor.timestamp_ms + MOMENT_LOOKAHEAD_MS,
    )


def _label(anchor: LectureMomentAnchor | LectureStretchAnchor) -> str:
    if isinstance(anchor, LectureStretchAnchor):
        return f"{timestamp(anchor.start_ms)} – {timestamp(anchor.end_ms)}"
    return timestamp(anchor.timestamp_ms)


def _units_in_window(
    connection: Connection,
    *,
    owner: UUID,
    video_id: UUID,
    version_id: UUID,
    start_ms: int,
    end_ms: int,
) -> list[Any]:
    """Every published unit overlapping the window, in lecture order.

    Overlap rather than containment: a transcript segment that starts before
    the window and runs into it carries the sentence the reader is asking
    about, and dropping it would anchor the question to its second half.

    A unit with no timing at all — a resource page, which belongs to the
    lecture rather than to a moment of it — is deliberately excluded. It is
    reachable by retrieval; it is not where the reader is.
    """

    return connection.execute(
        """
        select id, modality, start_ms, end_ms
        from video.evidence_units
        where owner_id = %s
          and video_id = %s
          and ingestion_version_id = %s
          and start_ms is not null
          and start_ms <= %s
          and coalesce(end_ms, start_ms) >= %s
        order by start_ms, id
        """,
        (owner, video_id, version_id, end_ms, start_ms),
    ).fetchall()


def _capped(
    rows: Sequence[Any],
    *,
    label: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Keep the window's units, preferring a spread of modalities.

    A dense stretch of transcript would otherwise fill the cap on its own and
    push out the frame the reader is looking at, which is the one piece of
    evidence a question about a diagram needs.
    """

    if len(rows) <= MAX_UNITS_PER_ANCHOR:
        return tuple(row["id"] for row in rows), ()

    visual = [row for row in rows if row["modality"] in {"visual_frame", "visual_event"}]
    spoken = [row for row in rows if row not in visual]
    keep_visual = visual[: max(1, MAX_UNITS_PER_ANCHOR // 4)] if visual else []
    keep = spoken[: MAX_UNITS_PER_ANCHOR - len(keep_visual)] + keep_visual
    ordered = [row["id"] for row in rows if row in keep]
    return (
        tuple(ordered),
        (
            f"{len(rows) - len(ordered)} evidence unit(s) of {label} beyond the "
            f"limit of {MAX_UNITS_PER_ANCHOR}",
        ),
    )


def resolve_lecture_anchors(
    connection: Connection,
    anchors: Iterable[LectureMomentAnchor | LectureStretchAnchor],
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
) -> tuple[ResolvedLectureAnchor, ...]:
    """Resolve every lecture anchor against the published version.

    An anchor naming a different lecture than the conversation's is skipped
    rather than resolved against this one: the two would produce plausible
    evidence for the wrong recording, which is worse than no anchor at all.
    """

    anchors = list(anchors)
    if not anchors:
        return ()
    owner = parse_owner_id(owner_id)
    video = UUID(str(video_id))
    try:
        version_id = published_version_id(
            connection,
            owner_id=owner,
            video_id=video,
        )
    except VideoNotReadyError:
        # Nothing is published, so there are no units to name. The turn still
        # runs and abstains on its own terms.
        return tuple(
            ResolvedLectureAnchor(
                anchor_id=anchor.anchor_id,
                kind=anchor.kind,
                evidence_ids=(),
                label=_label(anchor),
                matched=False,
            )
            for anchor in anchors
        )

    resolved: list[ResolvedLectureAnchor] = []
    for anchor in anchors:
        if anchor.video_id != video:
            resolved.append(
                ResolvedLectureAnchor(
                    anchor_id=anchor.anchor_id,
                    kind=anchor.kind,
                    evidence_ids=(),
                    label=_label(anchor),
                    matched=False,
                    dropped=("the anchor names a different lecture",),
                )
            )
            continue
        start_ms, end_ms = _window(anchor)
        rows = _units_in_window(
            connection,
            owner=owner,
            video_id=video,
            version_id=version_id,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        label = _label(anchor)
        evidence_ids, dropped = _capped(rows, label=label)
        resolved.append(
            ResolvedLectureAnchor(
                anchor_id=anchor.anchor_id,
                kind=anchor.kind,
                evidence_ids=evidence_ids,
                label=label,
                # A window with nothing in it is a real state — a silent
                # stretch, or a timestamp past the end of what was ingested —
                # and reporting it beats an empty success.
                matched=bool(rows),
                dropped=dropped,
            )
        )
    return tuple(resolved)
