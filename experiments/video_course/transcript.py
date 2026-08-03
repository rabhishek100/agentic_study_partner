"""Parse YouTube WebVTT into stable, non-overlapping transcript segments."""

from __future__ import annotations

from html import unescape
from pathlib import Path
import re

from .artifacts import stable_hash
from .models import Chapter, TranscriptSegment


TIMING = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")


def timestamp_ms(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(".")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(millis)
    )


def clean_caption(value: str) -> str:
    value = unescape(TAG.sub("", value)).replace("\u200b", " ")
    return SPACE.sub(" ", value).strip()


def _suffix(previous: str, current: str) -> str:
    """Remove the rolling prefix YouTube repeats in automatic captions."""

    if not previous:
        return current
    if current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous) :].strip()
    previous_words = previous.split()
    current_words = current.split()
    for overlap in range(min(len(previous_words), len(current_words), 20), 0, -1):
        if previous_words[-overlap:] == current_words[:overlap]:
            return " ".join(current_words[overlap:])
    return current


def parse_vtt(
    path: Path,
    *,
    chapters: list[Chapter],
    target_window_ms: int = 30_000,
) -> list[TranscriptSegment]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cues: list[tuple[int, int, str]] = []
    index = 0
    previous = ""
    while index < len(lines):
        match = TIMING.search(lines[index])
        if not match:
            index += 1
            continue
        start = timestamp_ms(match.group("start"))
        end = timestamp_ms(match.group("end"))
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            if not lines[index].startswith("NOTE"):
                text_lines.append(lines[index])
            index += 1
        full = clean_caption(" ".join(text_lines))
        delta = _suffix(previous, full)
        previous = full or previous
        if delta:
            cues.append((start, end, delta))

    segments: list[TranscriptSegment] = []
    bucket: list[str] = []
    bucket_start = 0
    bucket_end = 0
    for start, end, text in cues:
        if not bucket:
            bucket_start = start
        bucket.append(text)
        bucket_end = max(bucket_end, end)
        chapter_boundary = _chapter_index(chapters, bucket_start) != _chapter_index(
            chapters, start
        )
        if bucket_end - bucket_start >= target_window_ms or chapter_boundary:
            _append_segment(segments, bucket_start, bucket_end, bucket, chapters)
            bucket = []
            bucket_end = 0
    if bucket:
        _append_segment(segments, bucket_start, bucket_end, bucket, chapters)
    return segments


def _append_segment(
    segments: list[TranscriptSegment],
    start: int,
    end: int,
    texts: list[str],
    chapters: list[Chapter],
) -> None:
    text = SPACE.sub(" ", " ".join(texts)).strip()
    if not text:
        return
    identifier = "transcript-" + stable_hash([start, end, text])[:16]
    segments.append(
        TranscriptSegment(
            id=identifier,
            start_ms=start,
            end_ms=max(start + 1, end),
            text=text,
            chapter_index=_chapter_index(chapters, start),
        )
    )


def _chapter_index(chapters: list[Chapter], timestamp: int) -> int | None:
    for chapter in chapters:
        if chapter.start_ms <= timestamp < chapter.end_ms:
            return chapter.index
    return None

