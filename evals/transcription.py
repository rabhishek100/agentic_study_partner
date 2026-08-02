"""Measure how well each engine reads a page, against a reference.

A generative transcription sits inside the canonical layer, which is where
every citation ultimately resolves. That placement was argued for and never
measured: nothing in the system establishes that the model reads these books
better than the deterministic engine it replaced, or that it does not invent.
This is the measurement.

Three properties shape it.

*Pages are chosen by what they contain, not at random.* Forty random pages of a
system-design book are forty pages of prose, and would say nothing about tables,
mathematics, or the bleed-through pages where the two engines most disagree.
Selection is stratified on signals already stored, so it is reproducible and
does not depend on anyone's judgement about which pages look hard.

*The reference is adjudicated, not authored.* It is produced by a model reading
the page and resolving disagreements between the candidates, and it is labelled
that way everywhere it appears. Calling it ground truth would overstate it: an
adjudicator can share a blind spot with a candidate. A human spot-check of a
sample is what turns "a model graded models" into a measured agreement rate.

*Fabrication is scored separately from accuracy.* A page can have an excellent
character error rate and still contain an invented sentence, and the invented
sentence is the one that matters, because it reads and cites perfectly.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import unicodedata


__all__ = [
    "PageCategory",
    "PageScore",
    "categorize_page",
    "character_error_rate",
    "fabricated_spans",
    "score_page",
    "table_cell_f1",
    "word_error_rate",
]

PROSE = "prose"
TABLE = "table"
FORMULA = "formula"
PATHOLOGICAL = "pathological"

PageCategory = str

_TABLE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_MATH = re.compile(r"\$\$?[^$]+\$\$?")
_FIGURE = re.compile(r"<figure\b", re.IGNORECASE)
_MARKUP = re.compile(r"<[^>]+>|<!--.*?-->|`[^`]*`", re.DOTALL)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# A page needs this much prose before its error rate means anything; below it
# one misread word swings the score by tens of percent.
MINIMUM_SCORED_WORDS = 40


def categorize_page(text: str, *, flagged: bool, unassessable: bool) -> PageCategory:
    """Name what a page is made of, from the transcription already stored.

    Order matters. A page carrying both a table and mathematics is scored as a
    table page, because table structure is the harder thing to get right and
    the thing plain OCR cannot represent at all.
    """

    if flagged or unassessable:
        return PATHOLOGICAL
    if _TABLE.search(text):
        return TABLE
    if len(_MATH.findall(text)) >= 2:
        return FORMULA
    return PROSE


def _normalize(text: str) -> str:
    """Strip what the engines cannot be expected to agree about.

    Markup is one engine's convention and not another's; a deterministic
    reader emits no HTML at all. Comparing it would measure the prompt rather
    than the reading.
    """

    stripped = _MARKUP.sub(" ", text)
    folded = unicodedata.normalize("NFKC", stripped).casefold()
    return " ".join(folded.split())


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein distance, computed one row at a time."""

    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for index, character in enumerate(left, start=1):
        current = [index]
        for position, other in enumerate(right, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (character != other),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(candidate: str, reference: str) -> float:
    """Edit distance over the reference's length, on normalized text."""

    left, right = _normalize(candidate), _normalize(reference)
    if not right:
        return 0.0 if not left else 1.0
    return _edit_distance(left, right) / len(right)


def word_error_rate(candidate: str, reference: str) -> float:
    """Edit distance over words rather than characters."""

    left = _normalize(candidate).split()
    right = _normalize(reference).split()
    if not right:
        return 0.0 if not left else 1.0

    previous = list(range(len(right) + 1))
    for index, word in enumerate(left, start=1):
        current = [index]
        for position, other in enumerate(right, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (word != other),
                )
            )
        previous = current
    return previous[-1] / len(right)


class _Cells(HTMLParser):
    """Every cell of every table, as normalized strings."""

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"td", "th"}:
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current is not None:
            value = " ".join("".join(self._current).split()).casefold()
            if value:
                self.cells.append(value)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)


def _cells(text: str) -> list[str]:
    parser = _Cells()
    parser.feed(text)
    parser.close()
    return parser.cells


def table_cell_f1(candidate: str, reference: str) -> float | None:
    """Agreement on table cells, or None when neither side has a table.

    Cells rather than whole tables: a table with one cell misread is mostly
    right, and scoring it as a miss would hide the difference between an engine
    that represents tables and one that flattens them into prose.
    """

    expected = _cells(reference)
    produced = _cells(candidate)
    if not expected and not produced:
        return None
    if not expected or not produced:
        return 0.0

    remaining = list(expected)
    matched = 0
    for cell in produced:
        if cell in remaining:
            remaining.remove(cell)
            matched += 1
    precision = matched / len(produced)
    recall = matched / len(expected)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# Words in a row that the reference does not contain anywhere. Shorter runs are
# ordinary disagreement; this length is a clause the reference never saw.
FABRICATION_RUN = 12


def fabricated_spans(candidate: str, reference: str) -> list[str]:
    """Runs of words present in the candidate and absent from the reference.

    This is the measurement the character error rate cannot make. A page can
    score 2% CER and still assert a sentence the page does not contain, and
    that sentence is the one that reaches a reader with a citation attached.
    """

    known = set(_WORD.findall(_normalize(reference)))
    words = _WORD.findall(_normalize(candidate))

    spans: list[str] = []
    run: list[str] = []
    for word in words:
        if word in known or len(word) < 4:
            if len(run) >= FABRICATION_RUN:
                spans.append(" ".join(run))
            run = []
            continue
        run.append(word)
    if len(run) >= FABRICATION_RUN:
        spans.append(" ".join(run))
    return spans


@dataclass(frozen=True)
class PageScore:
    """What one engine did with one page."""

    book_id: int
    page: int
    category: PageCategory
    engine: str
    character_error_rate: float
    word_error_rate: float
    table_cell_f1: float | None
    fabricated: tuple[str, ...] = field(default_factory=tuple)
    scored: bool = True

    def provenance(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "page": self.page,
            "category": self.category,
            "engine": self.engine,
            "cer": round(self.character_error_rate, 4),
            "wer": round(self.word_error_rate, 4),
            "table_cell_f1": (
                None if self.table_cell_f1 is None else round(self.table_cell_f1, 4)
            ),
            "fabricated_spans": len(self.fabricated),
            "scored": self.scored,
        }


def score_page(
    *,
    book_id: int,
    page: int,
    category: PageCategory,
    engine: str,
    candidate: str,
    reference: str,
) -> PageScore:
    """Score one engine's reading of one page against the reference."""

    words = len(_normalize(reference).split())
    return PageScore(
        book_id=book_id,
        page=page,
        category=category,
        engine=engine,
        character_error_rate=character_error_rate(candidate, reference),
        word_error_rate=word_error_rate(candidate, reference),
        table_cell_f1=table_cell_f1(candidate, reference),
        fabricated=tuple(fabricated_spans(candidate, reference)),
        # A page with almost no prose is reported but excluded from the
        # aggregate, where one misread word would swing the rate by tens of
        # percent and drown the pages that carry real text.
        scored=words >= MINIMUM_SCORED_WORDS,
    )
