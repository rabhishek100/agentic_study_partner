"""Requests about the whole lecture rather than a moment inside it.

"Summarize this video" and "what topics are covered" are not retrieval
questions. Top-k retrieval answers them by finding eight passages and writing
confidently about a hundred minutes, which reads like a summary and is not
one — the failure is invisible precisely because the output looks right.

These routes therefore load the complete published transcript instead of
searching it, in windows that keep their own timestamps so every claim still
cites the moment it came from. That is the same move the book workflow makes
for a chapter summary: retrieve the complete subtree, not the best matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from study.streaming import TokenCallback, invoke_with_streaming
from video.answers import AnswerDraft, VideoAnswerDependencies, extract_citations
from video.contracts import VideoEvidenceRef
from video.models import answer_model, reported_cost_usd
from video.prompts import (
    build_inventory_messages,
    build_reduce_messages,
    build_summary_messages,
    format_timestamp,
)
from video.retrieval import VideoNotReadyError


# How many timestamped windows a lecture is divided into. Enough that a
# citation lands the reader within a couple of minutes of the claim, few
# enough that the marker list stays readable in the reference panel.
TARGET_WINDOWS = 24
# No window shorter than this, however brief the lecture: a citation to a
# fifteen-second window is a false promise of precision.
MINIMUM_WINDOW_MS = 60_000
# Characters of transcript per model call. A hundred-minute lecture runs to
# roughly 85,000, so it summarizes in one pass; a longer one is mapped in
# batches and reduced rather than silently truncated.
BATCH_CHARACTERS = 120_000


class NoTranscriptError(LookupError):
    """The lecture has no transcript to summarize."""


@dataclass(frozen=True)
class LectureScope:
    """The complete published transcript, in citable windows."""

    version_id: UUID
    windows: list[VideoEvidenceRef]
    duration_ms: int

    @property
    def characters(self) -> int:
        return sum(len(window.excerpt) for window in self.windows)


def _published_version(
    connection: Connection, *, owner: UUID, video: UUID
) -> UUID:
    row = connection.execute(
        """
        select version.id
        from video.videos as video
        join video.ingestion_versions as version
          on version.id = video.current_ingestion_version_id
         and version.video_id = video.id and version.owner_id = video.owner_id
        where video.id = %s and video.owner_id = %s
          and video.readiness_status in ('ready', 'degraded')
          and version.status in ('ready', 'degraded')
        """,
        (video, owner),
    ).fetchone()
    if row is None:
        raise VideoNotReadyError("video has no published evidence")
    return row["id"]


def load_lecture_scope(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    target_windows: int = TARGET_WINDOWS,
) -> LectureScope:
    """Load every transcript unit of the published version, in time order.

    Read from the evidence units rather than the raw transcript segments, so
    what a summary can say is exactly what a citation can point at: one
    published version, one set of text, no drift between the two.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    version_id = _published_version(connection, owner=owner, video=video)
    rows = connection.execute(
        """
        select id, retrieval_text, start_ms, end_ms, transcript_segment_id
        from video.evidence_units
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
          and modality = 'transcript' and start_ms is not null
        order by start_ms, id
        """,
        (owner, video, version_id),
    ).fetchall()
    if not rows:
        raise NoTranscriptError(
            "this lecture has no transcript, so it cannot be summarized"
        )

    duration_ms = int(rows[-1]["end_ms"] or rows[-1]["start_ms"])
    span = max(1, duration_ms - int(rows[0]["start_ms"]))
    window_ms = max(MINIMUM_WINDOW_MS, span // max(1, target_windows))

    windows: list[VideoEvidenceRef] = []
    bucket: list[dict[str, Any]] = []

    def flush() -> None:
        if not bucket:
            return
        start = int(bucket[0]["start_ms"])
        end = int(bucket[-1]["end_ms"] or bucket[-1]["start_ms"])
        text = " ".join(" ".join(row["retrieval_text"].split()) for row in bucket)
        identity = sha256(
            "|".join(str(row["id"]) for row in bucket).encode()
        ).hexdigest()
        windows.append(
            VideoEvidenceRef(
                rank=len(windows) + 1,
                evidence_id=identity,
                modality="transcript",
                excerpt=text,
                retrieval_method="complete_transcript",
                score=1.0,
                start_ms=start,
                end_ms=end,
                transcript_segment_id=(
                    bucket[0]["transcript_segment_id"] if len(bucket) == 1 else None
                ),
            )
        )
        bucket.clear()

    boundary = int(rows[0]["start_ms"]) + window_ms
    for row in rows:
        if bucket and int(row["start_ms"]) >= boundary:
            flush()
            boundary = int(row["start_ms"]) + window_ms
        bucket.append(row)
    flush()

    return LectureScope(
        version_id=version_id, windows=windows, duration_ms=duration_ms
    )


def load_chapters(
    connection: Connection, *, owner_id: str | UUID, video_id: str | UUID
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select chapter_index, title, start_ms, end_ms
        from video.chapters
        where owner_id = %s and video_id = %s
        order by chapter_index
        """,
        (parse_owner_id(owner_id), UUID(str(video_id))),
    ).fetchall()


def _batches(windows: list[VideoEvidenceRef]) -> list[list[VideoEvidenceRef]]:
    """Split into groups small enough for one call, keeping global ranks.

    Ranks are assigned across the whole lecture before batching, so a marker
    written while summarizing the third batch still points at the window it
    names once the partial summaries are combined.
    """

    batches: list[list[VideoEvidenceRef]] = [[]]
    size = 0
    for window in windows:
        if batches[-1] and size + len(window.excerpt) > BATCH_CHARACTERS:
            batches.append([])
            size = 0
        batches[-1].append(window)
        size += len(window.excerpt)
    return batches


def _generate(
    messages,
    *,
    dependencies: VideoAnswerDependencies,
    token_callback: TokenCallback | None,
) -> tuple[str, float]:
    model = dependencies.model or answer_model()
    response = invoke_with_streaming(
        model, messages, token_callback=token_callback
    )
    return str(response.content).strip(), reported_cost_usd(response)


def summarize_lecture(
    *,
    question: str,
    scope: LectureScope,
    video_title: str,
    chapters: list[dict[str, Any]],
    dependencies: VideoAnswerDependencies,
    token_callback: TokenCallback | None = None,
) -> AnswerDraft:
    """Summarize the complete transcript, citing the moments it draws on."""

    batches = _batches(scope.windows)
    cost = 0.0
    # Only the last call streams: a reader watching tokens arrive should see
    # the summary being written, not the intermediate passes over batches.
    if len(batches) == 1:
        text, spent = _generate(
            build_summary_messages(
                question=question,
                windows=batches[0],
                video_title=video_title,
                chapters=chapters,
                duration_ms=scope.duration_ms,
            ),
            dependencies=dependencies,
            token_callback=token_callback,
        )
        cost += spent
    else:
        partials: list[str] = []
        for index, batch in enumerate(batches, start=1):
            part, spent = _generate(
                build_summary_messages(
                    question=question,
                    windows=batch,
                    video_title=video_title,
                    chapters=chapters,
                    duration_ms=scope.duration_ms,
                    part=(index, len(batches)),
                ),
                dependencies=dependencies,
                token_callback=None,
            )
            partials.append(part)
            cost += spent
        text, spent = _generate(
            build_reduce_messages(
                question=question,
                partials=partials,
                video_title=video_title,
            ),
            dependencies=dependencies,
            token_callback=token_callback,
        )
        cost += spent

    citations = extract_citations(text, scope.windows)
    return AnswerDraft(
        answer=text,
        outcome="answer",
        citations=citations,
        visual_cards=[],
        cost_usd=round(cost, 6),
        image_count=0,
    )


def inventory_topics(
    *,
    question: str,
    scope: LectureScope,
    video_title: str,
    chapters: list[dict[str, Any]],
    dependencies: VideoAnswerDependencies,
    token_callback: TokenCallback | None = None,
) -> AnswerDraft:
    """List what the lecture covers, in the order it covers it.

    When the source published chapters, that list is the lecturer's own
    segmentation and is simply rendered — no model call, no cost, and no
    opportunity to invent a topic. Each row still cites the transcript window
    the chapter opens in, so its timestamp is as clickable as any other
    citation and the claim rests on evidence rather than on metadata alone.
    """

    if chapters:
        lines: list[str] = []
        for chapter in chapters:
            start = int(chapter["start_ms"])
            window = _window_at(scope.windows, start)
            marker = f" [S{window.rank}]" if window else ""
            lines.append(
                f"- **{format_timestamp(start)}** — {chapter['title']}{marker}"
            )
        text = (
            f"{video_title} covers {len(chapters)} "
            f"{'topic' if len(chapters) == 1 else 'topics'}, "
            "in the order the source published them:\n\n" + "\n".join(lines)
        )
        return AnswerDraft(
            answer=text,
            outcome="answer",
            citations=extract_citations(text, scope.windows),
            visual_cards=[],
            cost_usd=0.0,
            image_count=0,
        )

    text, cost = _generate(
        build_inventory_messages(
            question=question,
            windows=scope.windows,
            video_title=video_title,
            duration_ms=scope.duration_ms,
        ),
        dependencies=dependencies,
        token_callback=token_callback,
    )
    return AnswerDraft(
        answer=text,
        outcome="answer",
        citations=extract_citations(text, scope.windows),
        visual_cards=[],
        cost_usd=round(cost, 6),
        image_count=0,
    )


def _window_at(
    windows: list[VideoEvidenceRef], timestamp_ms: int
) -> VideoEvidenceRef | None:
    """The window a timestamp falls in, or the nearest one that follows it."""

    for window in windows:
        if window.start_ms is None:
            continue
        if window.start_ms <= timestamp_ms <= (window.end_ms or window.start_ms):
            return window
    following = [
        window
        for window in windows
        if window.start_ms is not None and window.start_ms >= timestamp_ms
    ]
    return following[0] if following else (windows[-1] if windows else None)


__all__ = [
    "LectureScope",
    "NoTranscriptError",
    "inventory_topics",
    "load_chapters",
    "load_lecture_scope",
    "summarize_lecture",
]
