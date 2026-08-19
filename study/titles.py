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
    | ^(?:compressed|compress|converted|output|final|draft|reencoded|remux|export|exported)$
    | ^(?:copy|dup|duplicate)$
    | ^(?:pdf|epub|djvu|scan|scanned|ocr|free|full|complete|ebook)$
    | ^\(\d+\)$                                  # the (1) a second download adds
    | ^\d{10}$|^\d{13}$                           # ISBN-10, ISBN-13
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Where a file came from, which is never what it is about. Book filenames from
# the download sites arrive prefixed with the site's own domain:
# `pdfcoffee.com_system-design-interview-...` names the host, not the book. The
# prefix is removed before the name is split into words, because by then the
# dot separating host from domain is gone and the two halves look like words.
_LEADING_SITE = re.compile(
    r"^(?:[\w-]+\.(?:com|net|org|pub|io|co|info|xyz|to|cc)|libgen|z-?lib"
    r"|annas-archive|b-ok|sci-hub)[\s_\-–—.]+",
    re.IGNORECASE,
)

# Only a real extension is an extension. `Path.stem` cuts at the last dot, which
# turns `pdfcoffee.com_system-design-interview` into "pdfcoffee" — the one part
# of that name carrying no information at all.
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")

# Acronyms a filename lowercases and a reader expects to see shouting. Only
# words that are unambiguously acronyms in this library's subject matter.
_ACRONYMS = frozenset(
    {
        "ai", "ml", "nlp", "llm", "llms", "gpu", "cpu", "tpu", "api", "apis",
        "sql", "http", "https", "rag", "cnn", "rnn", "lstm", "gan", "gans",
        "vae", "mlp", "svm", "pca", "kkt", "ocr", "ci", "cd", "os", "io",
    }
)

# A course code glued to its number: `cme295`, `cs231`, `ee364`. The letters are
# an acronym a filename lowercased and the digits are the course; a reader says
# them as two things. Bounded to 3-4 letters, and only when nothing follows the
# digits, so `CS231n` — where the trailing letter makes the whole token the
# course's name — is left as its author wrote it.
_COURSE_CODE = re.compile(r"^([A-Za-z]{3,4})(\d+)$")

# A name that is shaped like a filename, whatever column it is sitting in.
#
# This is the case the first version of the backfill missed entirely. Uploads
# store their bytes under a normalised name — 42 of the 46 documents in this
# library have `source_filename` set to the literal `original.pdf` — so the
# reader's filename survives only in the title the pipeline defaulted to. There
# is nothing to compare against, and the shape of the name has to be the
# evidence instead.
_FILENAME_SHAPED = (
    re.compile(r"_"),                       # 07_Neural_Turing_Machines
    re.compile(r"^\d{1,3}[\s._-]"),          # 26 Kolmogorov..., an index prefix
    re.compile(r"\(\d+\)\s*$"),              # PythonMastery (1)
    re.compile(r"^\[[\d.v]+\]"),             # [1409.2329] Recurrent...
    # PythonMastery, 2019BurkovTheHundred-page: no spaces, and more than one
    # capital start inside it — a title would have used spaces.
    re.compile(r"^\S+$"),
)

# An index a collection numbers its documents with, and the arXiv id a download
# prefixes them with. Neither is part of what the document is called.
_LEADING_INDEX = re.compile(r"^(?:\d{1,3}[\s._-]+|\[[\d.v]+\]\s*)")

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
    # "generative ai system design" is a filename; "Generative AI System
    # Design" is a title. A filename lowercases what an author capitalised.
    if word.casefold() in _ACRONYMS:
        return word.upper()
    # `ee364a` is EE364a: the letters are a department code a filename
    # lowercased, and the tail belongs to the course number. `CS231n` already
    # reads this way and is unchanged by it.
    code = re.fullmatch(r"([A-Za-z]{2,4})(\d+[A-Za-z]?)", word)
    if code:
        return code.group(1).upper() + code.group(2)
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

    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem = _EXTENSION.sub("", name).strip()
    stem = _LEADING_SITE.sub("", stem).strip()
    stem = _LEADING_INDEX.sub("", stem).strip()
    # A run of underscores is where a colon was: a filesystem cannot hold one,
    # so the downloader wrote `Dropout___A_Simple_Way`. Restoring it is what
    # makes the subtitle read as a subtitle instead of as a run-on.
    stem = re.sub(r"_{2,}", ": ", stem)
    if not stem:
        return name
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
        course = _COURSE_CODE.fullmatch(word)
        if course:
            expanded.extend([course.group(1).upper(), course.group(2)])
            continue
        parts = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word)
        parts = re.sub(r"(?<=[A-Za-z]{4})(?=\d)", " ", parts)
        # `2019BurkovTheHundred-page` starts with the year it was published.
        parts = re.sub(r"(?<=\d)(?=[A-Z])", " ", parts)
        expanded.extend(part for part in parts.split(" ") if part)

    cleaned = " ".join(_capitalise(word) for word in expanded).strip()
    if not cleaned:
        return stem
    # Lowercase the small words, except the first, which always leads — and
    # except the one after a colon, which leads the subtitle.
    words_out = cleaned.split(" ")
    for index in range(1, len(words_out)):
        if words_out[index].casefold() not in _LOWERCASE_WORDS:
            continue
        if words_out[index - 1].endswith(":"):
            continue
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
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if stored in {filename.strip(), name, _EXTENSION.sub("", name)}:
        return True
    if stored.casefold() in PLACEHOLDER_TITLES:
        return True
    # `YouTube video dQw4w9WgXcQ`: the placeholder written at creation, left
    # behind when acquisition never reached the point of replacing it.
    if re.fullmatch(r"YouTube video [\w-]{6,}", stored):
        return True
    # A title that is a filename in everything but the column it sits in.
    if any(shape.search(stored) for shape in _FILENAME_SHAPED):
        # Except when cleaning it would change nothing: a one-word title like
        # "Transformers" is shaped like a filename and is also just correct.
        return readable_title(stored) != stored
    return False
