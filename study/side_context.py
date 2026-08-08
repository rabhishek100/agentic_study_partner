"""Assemble one side chat turn's context, highest priority first.

A side chat asks about a passage of another conversation. Two kinds of context
follow from that, and they are deliberately not the same kind of thing:

*   The passages the reader selected are **generated answer text**. They tell
    the model what is being asked about, and they are never citable — locked
    grounding forbids a prior answer supporting a new claim.
*   What that passage's citation markers name **is** evidence, and it is what
    "the pasted text matters more" actually means here: those units are pinned
    to the front of the turn's evidence list, so they carry the lowest markers
    and the answer rests on the same source the quoted sentence did.

The rules are the same whether the source is a book or a lecture, so the
assembly here is surface-neutral: it works on a rank-to-identity mapping that
each surface supplies — book chunk ids on one side, video evidence units on the
other. Only the adapters differ.

Everything else — the exchange the passage came from, then the last few turns
of the main conversation — is surrounding context, included while a token
budget allows and reported when it does not. `build_scope_context` takes the
same approach for chapter scopes: format deterministically, and say exactly
what was left out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import tiktoken

from .contracts import QuoteAnchor, SideContextReport

DEFAULT_ENCODING = "cl100k_base"

# Room for a few highlighted sentences and a couple of compressed turns without
# crowding out the book evidence, which is what the answer must actually rest
# on. A side question is a clarification, not a second chapter review.
DEFAULT_TOKEN_BUDGET = 1_800

# Enough to cover a sentence citing two or three sources. Beyond that a side
# turn's evidence set would be larger than a normal turn's, which inverts the
# cost argument for asking small questions in a small window.
MAX_PINNED_CHUNKS = 3

DEFAULT_RECENT_TURNS = 3

# How much of an earlier answer is enough to recognise it by. The compressed
# tail exists so "how does that relate to what you said before?" resolves, not
# so the whole thread is replayed.
EARLIER_ANSWER_CHARS = 400

SOURCE_MARKER = re.compile(r"\[S(\d+)\]")

QUOTE_HEADING = (
    "Passages the reader is asking about, quoted from an earlier study answer. "
    "They are the focus of this question. They are not source evidence: do not "
    "cite them, and do not treat their wording as the book's."
)
ANCHORED_TURN_HEADING = "The exchange those passages came from:"
EARLIER_HEADING = "Earlier in the main conversation:"


def ranked_identities(
    evidence: Iterable[object],
    *,
    identity: str,
) -> dict[int, str]:
    """Map each `[S…]` rank to the identity of the evidence it named.

    `rank` is populated by every retrieval path; the list position is the
    fallback for a turn recorded before it was, and for a hierarchy answer
    whose evidence carries no ranks.
    """

    ranked: dict[int, str] = {}
    for position, reference in enumerate(evidence, start=1):
        value = getattr(reference, identity, None)
        if not value:
            continue
        ranked.setdefault(getattr(reference, "rank", None) or position, str(value))
    return ranked


@dataclass(frozen=True)
class ParentTurn:
    """One recorded turn of the conversation a side chat hangs off.

    Holds the rank-to-identity mapping rather than either surface's reference
    type. A book chunk and a video evidence unit are different things playing
    the same role here — something a marker points at — and the assembly rules
    are identical for both, so the assembler takes the mapping and each surface
    supplies it.
    """

    turn_index: int
    question: str
    answer: str
    ranked_ids: Mapping[int, str] = field(default_factory=dict)
    citation_ranks: tuple[int, ...] = ()

    @classmethod
    def from_result(
        cls,
        turn_index: int,
        question: str,
        answer: str,
        result: object,
    ) -> "ParentTurn":
        """Build from a stored book `TurnResult`, tolerating one with neither."""

        citations = tuple(getattr(result, "citations", ()) or ())
        return cls(
            turn_index=turn_index,
            question=question,
            answer=answer,
            ranked_ids=ranked_identities(
                getattr(result, "evidence", ()) or (),
                identity="chunk_id",
            ),
            citation_ranks=tuple(
                citation.evidence_rank
                for citation in citations
                if citation.evidence_rank
            ),
        )


@dataclass(frozen=True)
class SideContext:
    """The assembled context for one side turn."""

    request_context: str
    # Handed to the turn analyser so a question like "what does this mean?"
    # can be rewritten into a standalone query about the quoted claim.
    anchored_quotes: tuple[str, ...] = ()
    pinned_chunk_ids: tuple[str, ...] = ()
    report: SideContextReport = field(
        default_factory=lambda: SideContextReport(
            token_count=0,
            token_budget=DEFAULT_TOKEN_BUDGET,
        )
    )


def _collapse(value: str) -> str:
    return " ".join((value or "").split())


def readable_quote(quoted_text: str) -> str:
    """The quote with its citation markers removed, for naming a side chat.

    A reader can legitimately highlight nothing but a citation — "what is this
    source?" is a fair question — and pinning handles that correctly. Naming the
    thread "[S2] [S4]" does not, so the markers come out of the title while
    staying in the quote the model sees.
    """

    return _collapse(SOURCE_MARKER.sub(" ", quoted_text))


def resolve_pins(anchor: QuoteAnchor, turn: ParentTurn) -> tuple[str, ...]:
    """What the quoted passage rests on, in the order it names them.

    A marker that the turn cannot account for is skipped rather than guessed:
    an anchor pointing at evidence that is no longer there should narrow the
    pinned set, never substitute something else.

    A selection carrying no marker at all is the common case — readers
    highlight a sentence, not its citation — so it falls back to what the whole
    answer cited, and then to its best-ranked evidence. That keeps an uncited
    highlight anchored to the right part of the source instead of unanchored.
    """

    ranked = turn.ranked_ids
    if not ranked:
        return ()

    ordered: list[int] = []
    for match in SOURCE_MARKER.finditer(anchor.quoted_text):
        rank = int(match.group(1))
        if rank in ranked and rank not in ordered:
            ordered.append(rank)

    if not ordered:
        for rank in turn.citation_ranks:
            if rank in ranked and rank not in ordered:
                ordered.append(rank)
    if not ordered:
        ordered = [min(ranked)]

    return tuple(ranked[rank] for rank in ordered)


def _quote_block(anchors: Sequence[QuoteAnchor]) -> str:
    lines = [QUOTE_HEADING]
    for position, anchor in enumerate(anchors, start=1):
        lines.append(f'{position}. "{_collapse(anchor.quoted_text)}"')
    return "\n".join(lines)


def _anchored_turn_block(turns: Sequence[ParentTurn]) -> str:
    lines = [ANCHORED_TURN_HEADING]
    for turn in turns:
        lines.append(f"Reader asked: {_collapse(turn.question)}")
        lines.append(f"Answer given: {_collapse(turn.answer)}")
    return "\n".join(lines)


def _earlier_block(turns: Sequence[ParentTurn]) -> str:
    lines = [EARLIER_HEADING]
    for turn in turns:
        opening = _collapse(turn.answer)[:EARLIER_ANSWER_CHARS]
        lines.append(f"Reader asked: {_collapse(turn.question)}")
        lines.append(f"Answer began: {opening}")
    return "\n".join(lines)


def _truncate_to_tokens(text: str, limit: int, encoding) -> str:
    """Cut `text` so that it, plus the ellipsis marking the cut, fits `limit`.

    The marker is re-encoded rather than assumed to cost one token: it merges
    with the preceding characters in some tokenizations, so the only reliable
    way to respect a hard budget is to measure the candidate.
    """

    tokens = encoding.encode(text)
    if len(tokens) <= limit:
        return text
    for kept in range(max(limit - 1, 0), -1, -1):
        candidate = encoding.decode(tokens[:kept]).rstrip() + "…"
        if len(encoding.encode(candidate)) <= limit:
            return candidate
    return ""


def build_side_context(
    anchors: Sequence[QuoteAnchor],
    parent_turns: Iterable[ParentTurn],
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    recent_turns: int = DEFAULT_RECENT_TURNS,
    max_pinned_chunks: int = MAX_PINNED_CHUNKS,
    encoding_name: str = DEFAULT_ENCODING,
) -> SideContext:
    """Format a side turn's context and report what the budget excluded.

    The quoted passages are never dropped — they are the question's subject,
    and a side turn without them is a different question. When a single
    selection is larger than the whole budget it is truncated and the
    truncation is recorded. Surrounding context is dropped from the bottom of
    the ladder up: earlier turns first, then the anchored exchange.
    """

    if token_budget < 0:
        raise ValueError("token_budget cannot be negative")
    encoding = tiktoken.get_encoding(encoding_name)
    turns = {turn.turn_index: turn for turn in parent_turns}
    dropped: list[str] = []

    anchored_indexes: list[int] = []
    pinned: list[str] = []
    for anchor in anchors:
        turn = turns.get(anchor.parent_turn_index)
        if turn is None:
            # The turn was deleted, or the anchor was written against a
            # conversation it does not belong to. Either way there is nothing
            # to pin and nothing honest to add to the surrounding context.
            dropped.append(f"anchor {anchor.anchor_id}: parent turn is missing")
            continue
        if anchor.parent_turn_index not in anchored_indexes:
            anchored_indexes.append(anchor.parent_turn_index)
        for chunk_id in resolve_pins(anchor, turn):
            if chunk_id not in pinned:
                pinned.append(chunk_id)

    if len(pinned) > max_pinned_chunks:
        dropped.append(
            f"{len(pinned) - max_pinned_chunks} pinned chunk(s) beyond the "
            f"limit of {max_pinned_chunks}"
        )
        pinned = pinned[:max_pinned_chunks]

    quotes = tuple(_collapse(anchor.quoted_text) for anchor in anchors)
    blocks: list[str] = []
    used = 0

    if anchors:
        quote_block = _quote_block(anchors)
        quote_tokens = len(encoding.encode(quote_block))
        if quote_tokens > token_budget:
            quote_block = _truncate_to_tokens(quote_block, token_budget, encoding)
            quote_tokens = len(encoding.encode(quote_block))
            dropped.append("quoted passages truncated to fit the budget")
        blocks.append(quote_block)
        used += quote_tokens

    anchored = [turns[index] for index in sorted(anchored_indexes, reverse=True)]
    if anchored:
        block = _anchored_turn_block(anchored)
        cost = len(encoding.encode(block))
        if used + cost <= token_budget:
            blocks.append(block)
            used += cost
        else:
            dropped.append(
                f"the anchored exchange ({len(anchored)} turn(s)) did not fit"
            )

    earlier = [
        turn
        for turn in sorted(turns.values(), key=lambda item: item.turn_index)
        if turn.turn_index not in anchored_indexes
    ][-recent_turns:]
    if earlier:
        block = _earlier_block(earlier)
        cost = len(encoding.encode(block))
        if used + cost <= token_budget:
            blocks.append(block)
            used += cost
        else:
            dropped.append(f"{len(earlier)} earlier main-conversation turn(s) did not fit")

    return SideContext(
        request_context="\n\n".join(blocks),
        anchored_quotes=quotes,
        pinned_chunk_ids=tuple(pinned),
        report=SideContextReport(
            anchor_ids=[anchor.anchor_id for anchor in anchors],
            pinned_chunk_ids=list(pinned),
            token_count=used,
            token_budget=token_budget,
            dropped=dropped,
        ),
    )
