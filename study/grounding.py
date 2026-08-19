"""The grounding ladder: which rung answered a turn, and when to climb.

The reader asked for "the book, else the library, else the model, else the
web, by closest match". Implemented as prompt instructions that would be a
claim; implemented here it is a decision with a recorded cause, because the
climbing rule is the retry step of the study graph and every step it takes is
written onto the turn.

Two properties are load-bearing and both are deterministic:

*   **The verdict that provokes a widening is the abstention the system
    already produces.** Grounded answering abstains when its evidence does not
    support the question — that behaviour predates this module and is measured
    against the gold set. Reusing it as the retry signal means the ladder adds
    no new judgement call, and no second model call, to decide whether to
    escalate.
*   **Climbing is monotone and finite.** `open_source → library →
    model_knowledge`, each at most once, and the last rung does not retry. A
    ladder that could revisit a rung would be a loop with a model call in it.

Nothing here runs unless a caller supplies a policy. Without one the graph
behaves exactly as it did before, which is what keeps the main chat's measured
routing numbers valid.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .contracts import GroundingRung, TurnResult

# Enough of an abstention to identify it in the inspector without copying a
# paragraph of model prose onto every widening.
REASON_CHARS = 240


@dataclass(frozen=True)
class GroundingPolicy:
    """Which sources each rung may search, and how far the ladder may go.

    `source_book_ids` is the source the reader has open — the whole point of a
    source-first session, and the only rung that is never skipped.
    `library_book_ids` is everything else they could consult; when it adds
    nothing to the open source, that rung is skipped rather than repeated.

    `allow_model_knowledge` is the **stay in this source** lock. Off, the
    ladder stops at the reader's own material and the turn abstains, which is
    the behaviour AGENTS.md asks for by default and which the product owner
    chose to make opt-in instead.
    """

    source_book_ids: tuple[int, ...]
    library_book_ids: tuple[int, ...] = ()
    allow_model_knowledge: bool = True

    @classmethod
    def for_source(
        cls,
        source_book_ids: Iterable[int],
        *,
        library_book_ids: Iterable[int] = (),
        allow_model_knowledge: bool = True,
    ) -> "GroundingPolicy":
        source = tuple(sorted({int(book) for book in source_book_ids}))
        library = tuple(sorted({int(book) for book in library_book_ids} | set(source)))
        return cls(
            source_book_ids=source,
            library_book_ids=library,
            allow_model_knowledge=allow_model_knowledge,
        )

    def books_for(self, rung: GroundingRung) -> tuple[int, ...]:
        return self.library_book_ids if rung == "library" else self.source_book_ids

    def next_rung(self, rung: GroundingRung) -> GroundingRung | None:
        """The rung to try after `rung` failed, or None to stop and abstain."""

        if rung in {"anchor", "open_source"}:
            if set(self.library_book_ids) - set(self.source_book_ids):
                return "library"
            return "model_knowledge" if self.allow_model_knowledge else None
        if rung == "library":
            return "model_knowledge" if self.allow_model_knowledge else None
        # `model_knowledge` decides for itself whether the question needs live
        # search; there is nothing above it to climb to.
        return None


def insufficiency(result: TurnResult) -> str | None:
    """Why this result does not answer the question, or None if it does.

    Only two things count. An abstention is the system saying so in as many
    words. An answer with no evidence behind it is the same failure wearing a
    different outcome — it happens when retrieval returns nothing and the
    route was not one that retrieves.

    A clarification is deliberately not insufficient: the turn asked the reader
    a question and widening the search would answer one they did not ask. Nor
    is an error, which must surface rather than be escalated past.
    """

    if result.outcome == "abstain":
        answer = " ".join((result.answer or "").split())
        return answer[:REASON_CHARS] or "the evidence was judged insufficient"
    if result.outcome == "answer" and not result.evidence:
        return "the search returned no evidence"
    return None


def settled_rung(
    result: TurnResult,
    attempted: GroundingRung,
    *,
    pinned_ids: Sequence[str] = (),
) -> GroundingRung:
    """The rung an answer actually rests on, which can be lower than the pass.

    A turn searched at `open_source` whose every citation resolves to a pinned
    anchor was answered by the passage the reader was looking at, and saying so
    is the difference between "somewhere in this book" and "here". It is a
    property of the citations rather than a guess, so it costs nothing.
    """

    if result.source_type == "web_search":
        return "web_search"
    if result.source_type == "model_knowledge":
        return "model_knowledge"
    if attempted == "open_source" and pinned_ids and result.citations:
        pinned = set(pinned_ids)
        by_rank = {
            reference.rank or position: reference.chunk_id
            for position, reference in enumerate(result.evidence, start=1)
        }
        cited = {
            by_rank.get(citation.evidence_rank)
            for citation in result.citations
            if citation.evidence_rank
        }
        cited.discard(None)
        if cited and cited <= pinned:
            return "anchor"
    return attempted
