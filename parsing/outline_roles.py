"""Name every outline entry's structural role, one book at a time.

Depth alone cannot say which entries are chapters. "Designing Machine Learning
Systems" puts its chapters at outline level 1; "Designing Data-Intensive
Applications" puts *Parts* at level 1 and its twelve chapters at level 2. A
depth rule calls Part I a chapter and Chapter 5 a section, so `list the
chapters` answered with "Copyright, Table of Contents, Preface, Part I..." and
"summarize chapter 5" could not resolve at all, because only a node typed
`chapter` may answer to a chapter number.

Reading the role off each title independently is what this replaces, and it
failed for the opposite reason: it classified "Chapter 1 Introduction" as a
chapter and "1 Introduction" as something else, so a book that numbered its
chapters without the word lost all thirteen of them.

The rule here uses both signals, and classifies the *book* rather than each
node. In a well-structured book the chapters are a consecutively numbered run
of siblings at exactly one outline depth, so the depth whose siblings yield the
longest run of 1, 2, 3, ... is the chapter level. That is a claim about the
outline that can be checked rather than guessed: if no depth produces a
credible run, `chapter_level` returns None and the caller keeps the old depth
rule instead of inventing structure.

No role is excluded from scope search (see `SEARCHABLE_ROLES`). The earlier
title rule was destructive because unmatched entries landed in a class that
search filtered out; a merely *odd* label costs a strange word in a listing,
where an invisible one costs whole books.
"""

import re


CHAPTER = "chapter"
PART = "part"
APPENDIX = "appendix"
FRONT_MATTER = "front_matter"
BACK_MATTER = "back_matter"
SECTION = "section"
SUBSECTION = "subsection"
NESTED_SECTION = "nested_section"

# Every role a node can hold. Scope search must accept all of them: a role that
# search does not select is a role that hides content, which is the failure
# this module exists to prevent.
SEARCHABLE_ROLES = (
    CHAPTER,
    PART,
    APPENDIX,
    FRONT_MATTER,
    BACK_MATTER,
    SECTION,
    SUBSECTION,
    NESTED_SECTION,
)

# A run this short is a coincidence, not a numbering scheme. Two books in the
# corpus open with front matter that happens to start "1 ..."; requiring three
# consecutive entries keeps them from being mistaken for a chapter sequence.
MINIMUM_CHAPTER_RUN = 3

# A book's chapters run through the book. Anything numbered 1, 2, 3, ... inside
# a few pages is a list, not a chapter sequence.
#
# One scanned book made the difference concrete. Its chapters are unnumbered
# titles, so no depth carried a chapter run - except a numbered list of four
# diffusion-model steps on pages 288 to 291, which formed a perfect 1..4. That
# depth was elected, its four list items became the book's chapters, and all
# 302 entries preceding them became front matter. Requiring the run to cover
# a real share of the book rejects it: three pages of 351 is 0.9%.
MINIMUM_CHAPTER_SPAN = 0.5

_CHAPTER_WORD = re.compile(r"^\s*(?:chapter|ch\.?)\s+(\d+)\b", re.IGNORECASE)
# A bare leading integer, as in "5 Resampling Methods". The negative lookahead
# rejects "5.1 Cross-Validation": dotted numbering marks a subsection, and
# treating its first component as a chapter number would match every section of
# chapter 5 to the chapter itself.
_BARE_NUMBER = re.compile(r"^\s*(\d+)(?!\s*\.\s*\d)\s*[.:)]?\s+\S")
_PART = re.compile(r"^\s*part\s+(?:[ivxlcdm]+|\d+)\b", re.IGNORECASE)
_APPENDIX = re.compile(r"^\s*appendix\b", re.IGNORECASE)


def chapter_number(title: str) -> int | None:
    """Return the chapter number a title declares, if it declares one.

    Parts and appendices are numbered too, and their numbering restarts, so
    they are refused here rather than allowed to corrupt the run.
    """

    if _PART.match(title) or _APPENDIX.match(title):
        return None
    match = _CHAPTER_WORD.match(title)
    if match:
        return int(match.group(1))
    match = _BARE_NUMBER.match(title)
    return int(match.group(1)) if match else None


def _longest_run(numbers: list[int | None]) -> tuple[int, int]:
    """Find the longest consecutive run of 1, 2, 3, ... in document order.

    Returns the run's (start index, length) within `numbers`. Unnumbered
    entries do not break a run: "Designing Data-Intensive Applications"
    interleaves the preface's unnumbered subsections with its chapters at the
    same depth, and both belong to that level.
    """

    best_start = best_length = 0
    start = length = 0
    expected = 1
    for index, number in enumerate(numbers):
        if number is None:
            continue
        if number == expected:
            if length == 0:
                start = index
            length += 1
            expected += 1
            if length > best_length:
                best_start, best_length = start, length
        elif number == 1:
            start, length, expected = index, 1, 2
            if length > best_length:
                best_start, best_length = start, length
        else:
            length, expected = 0, 1
    return best_start, best_length


def chapter_level(
    levels: list[int],
    titles: list[str],
    pages: list[int] | None = None,
) -> int | None:
    """Return the outline depth that holds this book's chapters.

    None means no depth carried a credible numbered run, which is a real
    answer: the caller falls back to naming roles by depth alone.

    ``pages`` is optional but strongly recommended. Without it a numbered list
    buried anywhere in the book can be elected as the chapter sequence, because
    length alone cannot tell "chapters 1 through 4 of this book" from "steps 1
    through 4 of this procedure". With it, a run confined to a handful of pages
    is refused.
    """

    if len(levels) != len(titles):
        raise ValueError("levels and titles must describe the same entries")
    if pages is not None and len(pages) != len(levels):
        raise ValueError("pages must describe the same entries as levels")

    total_span = (max(pages) - min(pages)) if pages else 0

    best_level: int | None = None
    best_length = 0
    for level in sorted(set(levels)):
        at_depth = [
            (index, title)
            for index, (depth, title) in enumerate(zip(levels, titles, strict=True))
            if depth == level
        ]
        numbers = [chapter_number(title) for _, title in at_depth]
        start, length = _longest_run(numbers)
        if length < MINIMUM_CHAPTER_RUN:
            continue
        if pages is not None and total_span > 0:
            run_indices = [index for index, _ in at_depth[start : start + length]]
            covered = pages[run_indices[-1]] - pages[run_indices[0]]
            if covered < MINIMUM_CHAPTER_SPAN * total_span:
                continue
        # Ties go to the shallower depth, which is already sorted first: a
        # book numbering both chapters and their sections 1..N is describing
        # chapters at the higher level.
        if length > best_length:
            best_level, best_length = level, length
    return best_level if best_length >= MINIMUM_CHAPTER_RUN else None


def _depth_role(level: int, chapter_depth: int) -> str:
    offset = level - chapter_depth
    if offset == 1:
        return SECTION
    if offset == 2:
        return SUBSECTION
    return NESTED_SECTION


def outline_roles(
    levels: list[int],
    titles: list[str],
    pages: list[int] | None = None,
) -> list[str]:
    """Name each entry's role, using the whole outline to judge any one entry.

    Entries below the chapter level are named by their distance from it, so a
    book with Parts does not shift every section one name deeper than a book
    without them.
    """

    if len(levels) != len(titles):
        raise ValueError("levels and titles must describe the same entries")
    if not levels:
        return []

    depth = chapter_level(levels, titles, pages)
    if depth is None:
        return [fallback_role(level, title) for level, title in zip(levels, titles)]

    # Locate the run again over the chosen depth, this time keeping the
    # positions so the chapters can be identified individually.
    positions = [index for index, level in enumerate(levels) if level == depth]
    numbers = [chapter_number(titles[index]) for index in positions]
    start, length = _longest_run(numbers)
    chapters = set()
    expected = 1
    for index in positions[start:]:
        if len(chapters) == length:
            break
        if chapter_number(titles[index]) == expected:
            chapters.add(index)
            expected += 1
    first, last = min(chapters), max(chapters)

    roles = []
    for index, (level, title) in enumerate(zip(levels, titles, strict=True)):
        if level > depth:
            roles.append(_depth_role(level, depth))
        elif index in chapters:
            roles.append(CHAPTER)
        elif _APPENDIX.match(title):
            roles.append(APPENDIX)
        elif _PART.match(title):
            roles.append(PART)
        elif index < first:
            roles.append(FRONT_MATTER)
        elif index > last:
            roles.append(BACK_MATTER)
        else:
            # Between the first and last chapter but not one of them, and not
            # a Part: an unnumbered interlude. `section` keeps it addressable
            # without letting it answer to a chapter number.
            roles.append(SECTION)
    return roles


def fallback_role(level: int, title: str) -> str:
    """Name a role from depth alone, for outlines with no numbered run.

    This is the rule that shipped before per-book detection existed. It is
    wrong for books with Parts, but it is never *empty*: every entry keeps a
    searchable role, which is the property that matters when the structural
    signal is absent.
    """

    if level == 1:
        return APPENDIX if _APPENDIX.match(title) else CHAPTER
    return _depth_role(level, 1)
