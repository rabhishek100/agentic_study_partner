"""Chapters for a lecture whose source never published any.

A YouTube upload may ship a chapter list; an uploaded file never does, and the
lecture this project serves has none. That is not a cosmetic gap. The summary
workflow prefers chapters over transcript windows as its coverage units —
"a chapter is a topic, and an uncited chapter is a topic the summary skipped" —
so without them a hundred-minute lecture is checked against equal time slices,
which is a cruder unit than the lecture's own. And the topic inventory, given
chapters, renders them with no model call and no opportunity to invent a topic;
given none, it pays a model to infer an outline the slides already stated.

So they are derived from what the lecturer put on screen. A slide-based lecture
announces its own structure: the title stays while the body changes, and a new
title is a new topic. That signal is already computed — the visual observations
carry the text read off every frame — so this costs no model call, is
deterministic, and stays rebuildable from canonical data, which is what
`AGENTS.md` requires of anything derived.

What it cannot do is invent structure that was never shown. A lecture with no
slides, or one whose frames were never analysed, yields nothing and the caller
falls back to transcript windows exactly as before. Silence is the right answer
there: a made-up outline over a whiteboard talk would be a confident fiction in
the one place a reader uses to navigate.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence


CHAPTER_METHOD_VERSION = "video-chapters-slide-title-v1"

# Two words of shared leading text is what makes one frame a continuation of
# the slide before it. One word joins "Attention mechanism" to "Attention is
# all you need"; three splits a title that gained a subtitle mid-section.
MINIMUM_SHARED_WORDS = 2
# A chapter shorter than this is a slide, not a topic. Ninety seconds is about
# the shortest stretch a reader would navigate to on purpose, and merging below
# it is what turns 51 slide runs into 21 chapters on the production lecture.
MINIMUM_CHAPTER_MS = 90_000
# Slide titles are short; the rest of the line is the slide's body, and there
# is no separator between them because the frame reader returns one string.
# Anything past this is body text, and a chapter label is not a place to put it.
MAXIMUM_TITLE_WORDS = 6
MAXIMUM_TITLE_CHARACTERS = 80
# Two places a slide's body reliably starts, and a title reliably does not
# continue: a capitalised article beginning a sentence — "Tokenization | A cute
# teddy bear is reading" — and a year opening a timeline entry —
# "High-level timeline | 1980s Recurrent neural networks". Neither fires at the
# first word, where both are ordinary title material.
#
# A year, specifically, rather than any number: "Welcome to CME 295" is a course
# name and loses its number to the looser rule.
SENTENCE_ARTICLE = frozenset({"A", "An", "The"})
YEAR = re.compile(r"^\d{4}s?$")

# The frame reader says so when it cannot read a slide. Those frames must not
# start a chapter — an unreadable frame is not evidence of a new topic — but
# they should not end one either, so they extend whatever is open.
UNREADABLE = re.compile(
    r"not read|too small|too faint|blurr|indistinct|unreadable|"
    r"not reliably legible|no technical",
    re.IGNORECASE,
)
BRACKETED = re.compile(r"\[.*?\]")
KEEPABLE = re.compile(r"[^\w\s:&/-]")


@dataclass(frozen=True)
class DerivedChapter:
    """One stretch of lecture that kept the same slide title."""

    index: int
    title: str
    start_ms: int
    end_ms: int
    frame_count: int

    def provenance(self) -> dict[str, Any]:
        return {
            "method": CHAPTER_METHOD_VERSION,
            "signal": "slide_title_run",
            "frame_count": self.frame_count,
        }


def _words(text: str | None) -> list[str]:
    """The readable leading text of a frame, as comparable words."""

    collapsed = " ".join((text or "").split())
    return KEEPABLE.sub(" ", BRACKETED.sub(" ", collapsed)).split()


def _shared(left: Sequence[str], right: Sequence[str]) -> list[str]:
    shared: list[str] = []
    for first, second in zip(left, right):
        if first.casefold() != second.casefold():
            break
        shared.append(first)
    return shared


def _title(words: Sequence[str]) -> str:
    """The slide's leading text, trimmed to something a reader can scan.

    Only two cuts, both checked against the whole lecture before being kept: a
    capitalised article and a year, neither at the first word. Two looser rules
    were tried and reverted — any leading digit takes "Welcome to CME 295" down
    to "Welcome to CME", and a lowercase preposition takes "Summary of main
    methods" down to "Summary". A truncated true title beats a confidently
    wrong short one, so anything not clearly body text stays.
    """

    kept: list[str] = []
    for position, word in enumerate(words):
        if position and (YEAR.match(word) or word in SENTENCE_ARTICLE):
            break
        kept.append(word)
        if len(kept) >= MAXIMUM_TITLE_WORDS:
            break
    while kept and len(" ".join(kept)) > MAXIMUM_TITLE_CHARACTERS:
        kept.pop()
    return " ".join(kept).strip(" :-&/,") or " ".join(words[:2])


def _runs(readable: list[tuple[int, list[str]]]) -> list[dict[str, Any]]:
    """Group consecutive frames that kept sharing leading text.

    A boundary has to be confirmed by the frame after it as well. One frame
    misread at an angle, or one wide shot of the room, would otherwise split a
    section in two and the split would look exactly like a real topic change.
    """

    runs: list[dict[str, Any]] = []
    for position, (timestamp, words) in enumerate(readable):
        if runs:
            shared = _shared(runs[-1]["title"], words)
            if len(shared) >= MINIMUM_SHARED_WORDS:
                runs[-1].update(title=shared, end_ms=timestamp)
                runs[-1]["frame_count"] += 1
                continue
            following = readable[position + 1][1] if position + 1 < len(readable) else []
            if len(_shared(runs[-1]["title"], following)) >= MINIMUM_SHARED_WORDS:
                runs[-1]["end_ms"] = timestamp
                runs[-1]["frame_count"] += 1
                continue
        runs.append(
            {
                "title": list(words),
                "start_ms": timestamp,
                "end_ms": timestamp,
                "frame_count": 1,
            }
        )
    return runs


def _merge_short(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold anything too brief to be a topic into the chapter before it."""

    merged: list[dict[str, Any]] = []
    for run in runs:
        if merged and run["end_ms"] - run["start_ms"] < MINIMUM_CHAPTER_MS:
            previous = merged[-1]
            shared = _shared(previous["title"], run["title"])
            previous["end_ms"] = run["end_ms"]
            previous["frame_count"] += run["frame_count"]
            # Keep the shared title when the short run was the same slide
            # continuing; keep the longer-lived one when it was something else
            # passing through, because that is what the stretch was about.
            if len(shared) >= MINIMUM_SHARED_WORDS:
                previous["title"] = shared
            continue
        merged.append(run)
    return merged


def derive_chapters(
    observations: Iterable[Any], *, duration_ms: int
) -> tuple[DerivedChapter, ...]:
    """Chapters from the slide titles a lecture showed, in order.

    `observations` are the successful visual observations in time order, each
    carrying `timestamp_ms` and `visible_text`. Returns an empty tuple when the
    lecture shows nothing readable — the caller must fall back rather than
    treat an empty outline as an outline.

    Chapters tile the lecture: each runs to the next one's start and the last
    to the end of the recording, so no moment falls outside every chapter and
    coverage over them is coverage over all of it.
    """

    readable: list[tuple[int, list[str]]] = []
    for observation in observations:
        text = _field(observation, "visible_text")
        words = _words(text)
        if len(words) < MINIMUM_SHARED_WORDS or UNREADABLE.search(text or ""):
            continue
        readable.append((int(_field(observation, "timestamp_ms")), words))

    if not readable:
        return ()

    runs = _merge_short(_runs(readable))
    if not runs:
        return ()

    chapters: list[DerivedChapter] = []
    for index, run in enumerate(runs):
        start = int(run["start_ms"])
        end = (
            int(runs[index + 1]["start_ms"])
            if index + 1 < len(runs)
            else max(duration_ms, int(run["end_ms"]) + 1)
        )
        if end <= start:
            continue
        chapters.append(
            DerivedChapter(
                index=len(chapters),
                title=_title(run["title"]),
                start_ms=start,
                end_ms=end,
                frame_count=int(run["frame_count"]),
            )
        )
    # A single chapter spanning the whole lecture is the same statement as no
    # chapters at all, and a worse one: it looks like a segmentation.
    return tuple(chapters) if len(chapters) > 1 else ()


def _field(observation: Any, name: str) -> Any:
    if isinstance(observation, dict):
        return observation.get(name)
    return getattr(observation, name, None)


__all__ = [
    "CHAPTER_METHOD_VERSION",
    "DerivedChapter",
    "derive_chapters",
]
