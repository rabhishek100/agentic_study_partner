"""What a document or lecture is called, when nobody has said.

Every source arrives with a name its author never meant a reader to see:
`cme295-lecture1-h264.mp4`, `1706.03762v7.pdf`, `Chapter 3 FINAL (2) copy.pdf`.
The interface has been showing those, and a library of them cannot be scanned.

The rule everywhere is the same, in this order:

1. What the file says it is — the PDF's `/Title`, the container's `title` tag,
   the video platform's own title. Authored, and usually right.
2. What the content says it is — the first title on the first page, which for
   a paper is the paper's name and is often the only place it exists.
3. What the filename says it is, cleaned up. Never the raw filename: the
   separators, the version suffix, and the codec noise are for a filesystem,
   not for a reader.

Nothing here invents a title. When a name carries no words — `1706.03762v7` —
the cleaned filename is returned as it stands, because a wrong title is worse
than an ugly one, and the reader can rename it.
"""

from __future__ import annotations

import re
from pathlib import Path


# Metadata titles are frequently a placeholder the author never changed. One
# deck in this corpus carries "TestDoc", which would otherwise have named a
# 550-slide course in the library.
PLACEHOLDER_TITLES = frozenset(
    {
        "book",
        "document",
        "microsoft word",
        "new document",
        "pdf",
        "presentation",
        "testdoc",
        "untitled",
        "video",
    }
)

_EXPORT_SUFFIX = re.compile(r"\.(?:docx?|pptx?|indd|pages|pdf|tex)$", re.IGNORECASE)

MINIMUM_TITLE_CHARACTERS = 4
MAXIMUM_TITLE_CHARACTERS = 200

# Noise a filename picks up on its way through tools and downloads. Matched as
# whole words, and deliberately never a bare number: "Lecture 6" and "1706" are
# the parts of a filename most likely to be the thing the reader is looking for.
_FILENAME_NOISE = re.compile(
    r"""
    ^(?:v|ver|version)\d+(?:\.\d+)*$            # v7, ver2, version1.4
    | ^\d{3,4}p$                                  # 720p, 1080p
    | ^(?:x?264|x?265|h\.?26[45]|hevc|avc|aac|mp3|webm|mp4|mkv|mov)$
    | ^(?:hd|uhd|4k|8k|hq|sd)$
    | ^(?:compressed|converted|output|final|draft|reencoded|remux|export|exported)$
    | ^(?:copy|dup|duplicate)$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A word has to hold letters to be a word. A stem with none of them —
# `1706.03762v7` — is an identifier, and there is nothing in it to extract.
_HAS_WORDS = re.compile(r"[A-Za-z]{3,}")

# Words a title-caser must not capitalise, and acronyms it must not lower.
_LOWERCASE_WORDS = frozenset(
    {
        "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
        "nor", "of", "on", "onto", "or", "over", "per", "the", "to", "via",
        "with", "vs",
    }
)


def _looks_authored(value: str) -> bool:
    """Whether a metadata title is a real name rather than a tool's leftovers."""

    candidate = value.strip()
    if len(candidate) < MINIMUM_TITLE_CHARACTERS:
        return False
    folded = candidate.casefold()
    if folded in PLACEHOLDER_TITLES:
        return False
    # "Microsoft Word - chapter3.docx" and friends: an export name, not a title.
    if folded.startswith("microsoft word") or _EXPORT_SUFFIX.search(candidate):
        return False
    # A metadata title that is just the filename teaches the reader nothing.
    return not re.fullmatch(r"[\w.\-]+\.[a-z0-9]{2,4}", candidate, re.IGNORECASE)


def _capitalise(word: str) -> str:
    """Title-case one word, leaving anything already shouting or mixed alone."""

    if not word:
        return word
    # ML, LLM, GPU, CS231n — an author's capitalisation is information.
    if word.upper() == word and len(word) > 1:
        return word
    if any(character.isupper() for character in word[1:]):
        return word
    return word[0].upper() + word[1:]


def readable_title(filename: str) -> str:
    """A filename, rewritten as something a reader can scan.

    Extension, separators, and the noise a file collects on its way through
    tools are removed; word order and spelling are not touched, because the
    words are the only part of a filename that carries meaning.
    """

    stem = Path(filename.replace("\\", "/").rsplit("/", 1)[-1]).stem.strip()
    if not stem:
        return filename.strip()
    # Nothing to extract from an identifier. Returned as it stands rather than
    # rearranged into a different-looking identifier.
    if not _HAS_WORDS.search(stem):
        return stem

    words = [word for word in re.split(r"[\s_\-–—.+]+", stem) if word]
    kept = [word for word in words if not _FILENAME_NOISE.fullmatch(word)]
    # A name made entirely of noise — `1080p-final.mp4` — keeps its words
    # rather than becoming empty.
    if not kept:
        kept = words

    expanded: list[str] = []
    for word in kept:
        # `AttentionIsAllYouNeed` is one word to a filesystem and five to a
        # reader. A digit is split off only when what precedes it is long
        # enough to be a word itself: `lecture1` becomes "Lecture 1", while
        # `CS231n` and `cme295` are names and stay whole.
        parts = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word)
        parts = re.sub(r"(?<=[A-Za-z]{4})(?=\d)", " ", parts)
        expanded.extend(part for part in parts.split(" ") if part)

    cleaned = " ".join(_capitalise(word) for word in expanded).strip()
    if not cleaned:
        return stem
    # Lowercase the small words, except the first, which always leads.
    words_out = cleaned.split(" ")
    for index in range(1, len(words_out)):
        if words_out[index].casefold() in _LOWERCASE_WORDS:
            words_out[index] = words_out[index].casefold()
    return " ".join(words_out)[:MAXIMUM_TITLE_CHARACTERS]


def resolve_title(
    *,
    embedded: str | None = None,
    from_content: str | None = None,
    filename: str,
) -> str:
    """The best available name, in the order the module docstring sets out."""

    for candidate in (embedded, from_content):
        if candidate and _looks_authored(candidate):
            return " ".join(candidate.split())[:MAXIMUM_TITLE_CHARACTERS]
    return readable_title(filename)


def looks_machine_generated(title: str, filename: str) -> bool:
    """Whether a stored title is one nobody chose.

    Used by the backfill to tell a title the pipeline defaulted to from one a
    reader typed. Only the first kind may be replaced — a rename is a decision,
    and re-deriving over it would undo the reader's work every time the
    heuristic changed.
    """

    stored = title.strip()
    if not stored:
        return True
    name = Path(filename.replace("\\", "/").rsplit("/", 1)[-1])
    if stored in {filename.strip(), name.name, name.stem}:
        return True
    if stored.casefold() in PLACEHOLDER_TITLES:
        return True
    # `YouTube video dQw4w9WgXcQ`: the placeholder written at creation, left
    # behind when acquisition never reached the point of replacing it.
    return bool(re.fullmatch(r"YouTube video [\w-]{6,}", stored))
