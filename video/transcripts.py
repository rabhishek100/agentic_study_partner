"""Deterministic parsing and validation for timestamped video transcripts."""

from dataclasses import dataclass
from html import unescape
import re


TIMING = re.compile(
    r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})(?:\s+.*)?$"
)
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")


class InvalidTranscript(ValueError):
    pass


@dataclass(frozen=True)
class TranscriptCue:
    cue_index: int
    start_ms: int
    end_ms: int
    text: str
    raw_text: str


def timestamp_ms(value: str) -> int:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours, minutes, seconds = 0, int(parts[0]), parts[1]
    elif len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), parts[2]
    else:
        raise InvalidTranscript("invalid WebVTT timestamp")
    whole, millis = seconds.split(".", 1)
    if minutes >= 60 or int(whole) >= 60 or len(millis) != 3:
        raise InvalidTranscript("invalid WebVTT timestamp")
    return hours * 3_600_000 + minutes * 60_000 + int(whole) * 1_000 + int(millis)


def clean_caption(value: str) -> str:
    cleaned = unescape(TAG.sub("", value)).replace("\u200b", " ")
    return SPACE.sub(" ", cleaned).strip()


def _rolling_suffix(previous: str, current: str) -> str:
    if not previous:
        return current
    if current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous) :].strip()
    previous_words, current_words = previous.split(), current.split()
    maximum = min(len(previous_words), len(current_words), 30)
    for overlap in range(maximum, 0, -1):
        if previous_words[-overlap:] == current_words[:overlap]:
            return " ".join(current_words[overlap:])
    return current


def parse_webvtt(payload: str | bytes) -> list[TranscriptCue]:
    """Return cleaned cue-level captions while retaining raw cue text.

    The raw WebVTT object remains canonical in media storage. Cue text removes
    YouTube's rolling prefix so retrieval does not repeat every spoken phrase.
    """

    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    lines = text.splitlines()
    cues: list[TranscriptCue] = []
    previous = ""
    index = 0
    while index < len(lines):
        match = TIMING.search(lines[index].strip())
        if match is None:
            index += 1
            continue
        start = timestamp_ms(match.group("start"))
        end = timestamp_ms(match.group("end"))
        if end <= start:
            raise InvalidTranscript("WebVTT cue must end after it starts")
        index += 1
        raw_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            if not lines[index].lstrip().startswith("NOTE"):
                raw_lines.append(lines[index])
            index += 1
        raw = " ".join(raw_lines).strip()
        full = clean_caption(raw)
        delta = _rolling_suffix(previous, full)
        previous = full or previous
        if delta:
            cues.append(
                TranscriptCue(
                    cue_index=len(cues),
                    start_ms=start,
                    end_ms=end,
                    text=delta,
                    raw_text=raw,
                )
            )
    if not cues:
        raise InvalidTranscript("WebVTT contained no usable caption cues")
    return cues


def transcript_coverage(cues: list[TranscriptCue], *, duration_ms: int) -> float:
    """Measure timestamp coverage as an interval union, never double-counting."""

    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    intervals = sorted(
        (max(0, cue.start_ms), min(duration_ms, cue.end_ms))
        for cue in cues
        if cue.start_ms < duration_ms and cue.end_ms > 0
    )
    covered = 0
    current_start = current_end = 0
    for start, end in intervals:
        if end <= start:
            continue
        if start > current_end:
            covered += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    covered += current_end - current_start
    return min(1.0, max(0.0, covered / duration_ms))
