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
from video.answers import (
    AnswerDraft,
    SOURCE_CITATION,
    VideoAnswerDependencies,
    extract_citations,
)
from video.contracts import VideoEvidenceRef
from video.models import answer_model, reported_cost_usd
from video.prompts import (
    build_coverage_addendum_messages,
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
# A window carrying less than this is silence, logistics, or a handful of
# filler words. Requiring a summary to cite it would force padding — the exact
# behaviour the summary prompt forbids.
SUBSTANTIVE_WINDOW_CHARACTERS = 240
# One repair pass. A second has nothing new to say: the same evidence was
# already supplied twice, and a third call mostly spends money to reword.
COVERAGE_REPAIR_ATTEMPTS = 1


class NoTranscriptError(LookupError):
    """The lecture has no transcript to summarize."""


@dataclass(frozen=True)
class CoverageUnit:
    """A stretch of lecture the summary is expected to say something about.

    The book workflow requires a citation from every content-bearing node of
    the chapter, because a node is a topic and an uncited node is a topic the
    summary skipped. A lecture's equivalent is its published chapters when it
    has them, and its transcript windows when it does not — a time slice is a
    cruder unit than a section, but an uncited one is still forty minutes the
    summary demonstrably never reached.
    """

    key: str
    label: str
    window_ranks: tuple[int, ...]
    required: bool

    def satisfied_by(self, cited_ranks: set[int]) -> bool:
        return any(rank in cited_ranks for rank in self.window_ranks)


@dataclass(frozen=True)
class SummaryCoverage:
    units: tuple[CoverageUnit, ...]
    missing_required: tuple[CoverageUnit, ...]
    missing_optional: tuple[CoverageUnit, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_required

    @property
    def required_total(self) -> int:
        return sum(1 for unit in self.units if unit.required)

    def describe(self) -> str:
        covered = self.required_total - len(self.missing_required)
        return f"{covered} of {self.required_total} required stretches cited"


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


def coverage_units(
    scope: LectureScope, chapters: list[dict[str, Any]]
) -> tuple[CoverageUnit, ...]:
    """What the summary must touch, in the lecture's own segmentation.

    Chapters win when the source published them: they are the lecturer's own
    topics, which is what a reader means by "cover everything". Without them
    the windows are the only segmentation there is, and a window too thin to
    carry content is optional so that silence never forces padding.
    """

    if chapters:
        units: list[CoverageUnit] = []
        for chapter in chapters:
            start = int(chapter["start_ms"])
            end = int(chapter["end_ms"] or start)
            # Strict overlap on both edges: a window ending exactly where the
            # chapter begins contains none of it, and counting it would let a
            # summary "cover" a chapter by citing the moment before it.
            ranks = tuple(
                window.rank
                for window in scope.windows
                if window.start_ms is not None
                and window.start_ms < max(end, start + 1)
                and (window.end_ms or window.start_ms) > start
            )
            # A chapter the transcript never reaches cannot be cited, and
            # demanding it would fail every summary of that lecture forever.
            if not ranks:
                continue
            units.append(
                CoverageUnit(
                    key=f"chapter:{chapter['chapter_index']}",
                    label=(
                        f"{chapter['title']} "
                        f"({format_timestamp(start)}–{format_timestamp(end)})"
                    ),
                    window_ranks=ranks,
                    required=True,
                )
            )
        if units:
            return tuple(units)

    return tuple(
        CoverageUnit(
            key=f"window:{window.rank}",
            label=(
                f"{format_timestamp(window.start_ms)}"
                f"–{format_timestamp(window.end_ms)}"
            ),
            window_ranks=(window.rank,),
            required=len(window.excerpt) >= SUBSTANTIVE_WINDOW_CHARACTERS,
        )
        for window in scope.windows
    )


def evaluate_coverage(
    text: str, units: tuple[CoverageUnit, ...]
) -> SummaryCoverage:
    """Check what the draft actually cited, not what it was asked to cite."""

    cited = {int(match.group(1)) for match in SOURCE_CITATION.finditer(text)}
    missing_required = tuple(
        unit for unit in units if unit.required and not unit.satisfied_by(cited)
    )
    missing_optional = tuple(
        unit
        for unit in units
        if not unit.required and not unit.satisfied_by(cited)
    )
    return SummaryCoverage(
        units=units,
        missing_required=missing_required,
        missing_optional=missing_optional,
    )


def _windows_for(
    scope: LectureScope, units: tuple[CoverageUnit, ...]
) -> list[VideoEvidenceRef]:
    wanted = {rank for unit in units for rank in unit.window_ranks}
    return [window for window in scope.windows if window.rank in wanted]


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
    """Summarize the complete transcript, then check it actually covered it.

    Asking for complete coverage in the prompt is not the same as getting it.
    The book workflow does not trust that either: it lists the sections that
    must be cited, parses the draft to see which ones were, and repairs the
    gap. This is the same three steps over a lecture's own segmentation.

    Deliberately not streamed. A draft that is about to gain a coverage
    addendum should not already be on screen, and a provider stream that ends
    without a terminal finish reason yields a plausible half-summary that
    validation would then be run against — the book path disabled streaming
    here for that second reason before this one existed.
    """

    units = coverage_units(scope, chapters)
    text, cost = _draft_summary(
        question=question,
        windows=scope.windows,
        scope=scope,
        video_title=video_title,
        chapters=chapters,
        units=units,
        dependencies=dependencies,
    )

    coverage = evaluate_coverage(text, units)
    warnings: list[str] = []
    for _ in range(COVERAGE_REPAIR_ATTEMPTS):
        if coverage.complete:
            break
        addendum, spent = _generate(
            build_coverage_addendum_messages(
                video_title=video_title,
                missing=coverage.missing_required,
                windows=_windows_for(scope, coverage.missing_required),
            ),
            dependencies=dependencies,
            token_callback=None,
        )
        cost += spent
        if not addendum.strip():
            break
        # An addendum rather than a rewrite: the draft is already grounded and
        # cited, and regenerating it risks losing coverage it had to gain
        # coverage it lacked.
        text = f"{text.rstrip()}\n\n## Also covered\n\n{addendum.strip()}"
        coverage = evaluate_coverage(text, units)

    if not coverage.complete:
        # Said out loud rather than swallowed: a summary with a hole in it is
        # still useful, but the reader must know which part is missing before
        # they rely on it.
        warnings.append(
            "This summary does not cite "
            + "; ".join(unit.label for unit in coverage.missing_required[:5])
            + (
                f" and {len(coverage.missing_required) - 5} more"
                if len(coverage.missing_required) > 5
                else ""
            )
            + ". Ask about those stretches directly for a grounded answer."
        )

    if token_callback is not None:
        # The whole answer at once, so the interface renders the validated
        # text through the same path a streamed answer arrives on.
        token_callback("token", text)

    return AnswerDraft(
        answer=text,
        outcome="answer",
        citations=extract_citations(text, scope.windows),
        visual_cards=[],
        cost_usd=round(cost, 6),
        image_count=0,
        coverage=coverage.describe(),
        warnings=tuple(warnings),
    )


def _draft_summary(
    *,
    question: str,
    windows: list[VideoEvidenceRef],
    scope: LectureScope,
    video_title: str,
    chapters: list[dict[str, Any]],
    units: tuple[CoverageUnit, ...],
    dependencies: VideoAnswerDependencies,
) -> tuple[str, float]:
    """One summary of the supplied windows, mapped and reduced if needed."""

    batches = _batches(windows)
    cost = 0.0
    if len(batches) == 1:
        return _generate(
            build_summary_messages(
                question=question,
                windows=batches[0],
                video_title=video_title,
                chapters=chapters,
                duration_ms=scope.duration_ms,
                units=units,
            ),
            dependencies=dependencies,
            token_callback=None,
        )

    partials: list[str] = []
    for index, batch in enumerate(batches, start=1):
        ranks = {window.rank for window in batch}
        part, spent = _generate(
            build_summary_messages(
                question=question,
                windows=batch,
                video_title=video_title,
                chapters=chapters,
                duration_ms=scope.duration_ms,
                # Each stretch is told only about the coverage it can satisfy;
                # naming units it holds no windows for would invite a citation
                # to evidence it was never given.
                units=tuple(
                    unit
                    for unit in units
                    if any(rank in ranks for rank in unit.window_ranks)
                ),
                part=(index, len(batches)),
            ),
            dependencies=dependencies,
            token_callback=None,
        )
        partials.append(part)
        cost += spent
    combined, spent = _generate(
        build_reduce_messages(
            question=question, partials=partials, video_title=video_title
        ),
        dependencies=dependencies,
        token_callback=None,
    )
    return combined, cost + spent


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
