"""Whether a summary covered a stretch of lecture, or only cited it.

`video.lecture.evaluate_coverage` counts a coverage unit satisfied when any
citation marker in the draft points at one of its windows. That is the check
the runtime can afford — it is deterministic, free, and it catches the failure
it was built for, which is a summary that stops talking about the lecture
halfway through.

It cannot catch the failure one level down. "The lecture then moves on to
further material [S12]" satisfies window 12 by that rule and tells a reader
nothing, and the coverage-repair addendum is exactly the kind of call that
produces sentences like it: it is asked to cover named stretches, it is
graded on citing them, and citing them is cheaper than reading them. So the
runtime reports "24 of 24 required stretches cited" for a summary that
demonstrably covered fewer, and no measurement in the project contradicts it.

This module is that measurement. It does not change what the runtime accepts;
it reports `cited` and `substantive` as two separate rates so the gap between
them is a number rather than a suspicion.

The substantive test asks whether the claim carrying a unit's marker talks
about what is actually in that stretch. Each unit's *distinctive* terms are
computed by TF-IDF across the lecture's own units, so a term earns its place
by being concentrated in one stretch rather than by being frequent — "attention"
is distinctive of the stretches that introduce it and worthless in a lecture
where it appears throughout. A claim passes when it carries enough words of
its own and shares enough of that stretch's distinctive vocabulary.

This is a proxy and is treated as one. It answers "is this claim about that
stretch of lecture", not "is this a good summary of it", and a run may pair it
with the semantic judge to check the proxy against a reader's reading.

A proxy can also be wrong, and this one's first run said so loudly: it scored
a genuinely thorough summary as 0% substantive across all 24 stretches,
because it attributed each marker to a sentence rather than to a claim, and
this prompt's output puts its markers at the end of a paragraph. What it had
actually measured was the empty string after a full stop. `claims` is the
correction. The lesson is kept here because a measurement built to catch
overstated coverage is exactly the kind that gets believed when it is wrong.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re

from video.answers import SOURCE_CITATION
from video.lecture import CoverageUnit, LectureScope


# Below this a sentence is a pointer rather than a claim: "See also [S12]",
# "Finally, the lecture concludes [S24]". The floor counts content words only,
# so a long sentence made of filler does not pass on length.
MINIMUM_CONTENT_WORDS = 6
# One shared term is a coincidence in a technical lecture, where a stretch
# about tokenization and a stretch about embeddings both say "token". Two
# distinctive terms is the smallest signal that reads as deliberate.
MINIMUM_DISTINCTIVE_MATCHES = 2
# How many distinctive terms per unit are offered for matching. Enough that a
# summary may choose which aspect of a four-minute stretch to report, few
# enough that the list stays specific to that stretch.
DISTINCTIVE_TERMS_PER_UNIT = 20
# A term appearing in most units is background vocabulary for this lecture,
# whatever its TF-IDF weight in one of them.
MAXIMUM_UNIT_FREQUENCY = 0.5
MINIMUM_TERM_LENGTH = 4

PARAGRAPH = re.compile(r"\n\s*\n")
WORD = re.compile(r"[a-z][a-z0-9'-]*")
# Markdown scaffolding carries no claim and must not be counted as content.
MARKUP = re.compile(r"^[\s#>*\-–—•\d.)]+", re.MULTILINE)

# Spoken-lecture filler and discourse words. These survive TF-IDF badly: a
# transcript stretch where the lecturer says "basically" nine times makes it
# look distinctive of that stretch, and a summary sentence would never repeat
# it. Everything here is a word that carries no topic.
FILLER = frozenset(
    """
    about actually after again against also always another anything around
    back basically because become been before being between both bunch called
    came cannot come comes coming completely could course different
    does doing done down during each either else end enough even ever every
    everyone exactly fine first from front give given goal goes going good
    great guess have having here high idea instead into just keep kind know
    known last later least less like little long look looking lot made make
    makes making many maybe mean means might more most much must name need
    needs never next nice number often okay once only other others over
    part people perhaps point pretty probably question questions quite rather
    really right said same say says second see seen sense several she should
    show shown side similar simple since slide slides some someone something
    sometimes soon sort sorry start still stuff such super sure take takes
    talk talked talking tell than that their them then there these they thing
    things think this those though three through thus time times today
    together told took toward turn typically under until upon used uses using
    usually very want wants way ways well were what when where whether which
    while will with without word words work would yeah year years your
    """.split()
)


@dataclass(frozen=True)
class UnitSubstance:
    """One coverage unit, measured twice."""

    key: str
    label: str
    required: bool
    cited: bool
    substantive: bool
    distinctive_terms: tuple[str, ...]
    matched_terms: tuple[str, ...]
    citing_claims: tuple[str, ...]
    content_words: int

    @property
    def vacuous(self) -> bool:
        """Cited, and said nothing about the stretch it cited."""

        return self.cited and not self.substantive


@dataclass(frozen=True)
class SummarySubstance:
    units: tuple[UnitSubstance, ...]

    @property
    def required(self) -> tuple[UnitSubstance, ...]:
        return tuple(unit for unit in self.units if unit.required)

    @property
    def cited_rate(self) -> float:
        """What the runtime reports as coverage."""

        return _rate([unit.cited for unit in self.required])

    @property
    def substantive_rate(self) -> float:
        """What a reader would recognise as coverage."""

        return _rate([unit.substantive for unit in self.required])

    @property
    def vacuous_units(self) -> tuple[UnitSubstance, ...]:
        return tuple(unit for unit in self.required if unit.vacuous)

    def summary(self) -> dict[str, float | int]:
        return {
            "required_units": len(self.required),
            "cited_rate": round(self.cited_rate, 4),
            "substantive_rate": round(self.substantive_rate, 4),
            "vacuous_citation_count": len(self.vacuous_units),
            # The number this whole module exists to produce: how much of the
            # runtime's reported coverage is citation alone.
            "coverage_overstatement": round(
                self.cited_rate - self.substantive_rate, 4
            ),
        }


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def _stem(word: str) -> str:
    """Enough normalization to match 'embedding' with 'embeddings'.

    Plurals only, and deliberately so. A suffix stemmer that also strips
    "-ization" turns "tokenization" into "token" — collapsing the term that
    identifies one stretch of this lecture into the term that appears
    throughout it. A wrong match inflates the very number this module exists
    to keep honest, so the rule stops where it stops being obviously safe.
    """

    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _terms(text: str) -> list[str]:
    return [
        stem
        for word in WORD.findall(text.lower())
        if len(word) >= MINIMUM_TERM_LENGTH
        and word not in FILLER
        and len(stem := _stem(word)) >= MINIMUM_TERM_LENGTH
    ]


def _unit_text(unit: CoverageUnit, scope: LectureScope) -> str:
    # Transcript only. A slide's description was written by a model, and
    # scoring a summary against its vocabulary would reward echoing that
    # description rather than reporting the lecture.
    wanted = set(unit.window_ranks)
    return " ".join(
        window.excerpt for window in scope.windows if window.rank in wanted
    )


def distinctive_terms(
    units: tuple[CoverageUnit, ...], scope: LectureScope
) -> dict[str, tuple[str, ...]]:
    """The vocabulary that belongs to each stretch rather than to the lecture.

    TF-IDF over the lecture's own units, so the reference corpus is the same
    recording. A term shared by most stretches is this lecturer's background
    vocabulary and proves nothing about which stretch a sentence is discussing.
    """

    counts = {unit.key: Counter(_terms(_unit_text(unit, scope))) for unit in units}
    total = len(units) or 1
    document_frequency: Counter[str] = Counter()
    for counter in counts.values():
        document_frequency.update(counter.keys())

    selected: dict[str, tuple[str, ...]] = {}
    for unit in units:
        counter = counts[unit.key]
        scored = [
            (
                frequency * math.log(total / document_frequency[term]),
                term,
            )
            for term, frequency in counter.items()
            if document_frequency[term] <= max(1, int(total * MAXIMUM_UNIT_FREQUENCY))
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        selected[unit.key] = tuple(
            term for _, term in scored[:DISTINCTIVE_TERMS_PER_UNIT]
        )
    return selected


def claims(answer: str) -> list[tuple[str, int]]:
    """What each citation marker is standing behind, paired with its rank.

    A marker cites the prose since the last marker in its paragraph, which is
    how citation-carrying writing is actually read and, more to the point, how
    this summary prompt's output is actually shaped: markers land at the end of
    a paragraph, not after every sentence. Splitting on sentence boundaries
    instead attributes "[S1]" alone — everything before the full stop belongs
    to the unmarked sentence in front of it — and then measures every summary
    ever written as vacuous. That is not a hypothetical; it is what the first
    run of this module reported before the attribution was fixed.

    Consecutive markers stand behind the same claim: "[S3][S4]" at the end of a
    paragraph cites it twice, not once with an empty second citation.
    """

    found: list[tuple[str, int]] = []
    for paragraph in PARAGRAPH.split(answer):
        cleaned = MARKUP.sub("", paragraph).strip()
        markers = list(SOURCE_CITATION.finditer(cleaned))
        if not markers:
            continue
        cursor = 0
        previous: str | None = None
        for marker in markers:
            text = cleaned[cursor:marker.start()].strip()
            if not _terms(text):
                # Either a run of adjacent markers, or a marker opening the
                # paragraph it belongs to.
                text = previous if previous is not None else cleaned[marker.end():]
            else:
                previous = text
            found.append((text, int(marker.group(1))))
            cursor = marker.end()
    return found


def measure_summary_substance(
    answer: str, *, scope: LectureScope, units: tuple[CoverageUnit, ...]
) -> SummarySubstance:
    """Score each unit as cited and, separately, as substantively covered."""

    vocabulary = distinctive_terms(units, scope)
    prepared = [
        (text, rank, set(_terms(text)), len(_terms(text)))
        for text, rank in claims(answer)
    ]

    measured: list[UnitSubstance] = []
    for unit in units:
        ranks = set(unit.ranks)
        terms = set(vocabulary.get(unit.key, ()))
        citing = [item for item in prepared if item[1] in ranks]
        matched: set[str] = set()
        substantive = False
        for _, _, claim_terms, content_words in citing:
            overlap = claim_terms & terms
            matched |= overlap
            if (
                content_words >= MINIMUM_CONTENT_WORDS
                and len(overlap) >= MINIMUM_DISTINCTIVE_MATCHES
            ):
                substantive = True
        measured.append(
            UnitSubstance(
                key=unit.key,
                label=unit.label,
                required=unit.required,
                cited=bool(citing),
                substantive=substantive,
                distinctive_terms=vocabulary.get(unit.key, ()),
                matched_terms=tuple(sorted(matched)),
                citing_claims=tuple(item[0] for item in citing),
                content_words=max((item[3] for item in citing), default=0),
            )
        )
    return SummarySubstance(units=tuple(measured))


__all__ = [
    "SummarySubstance",
    "UnitSubstance",
    "distinctive_terms",
    "measure_summary_substance",
]
