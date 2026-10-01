"""Record a flashcard as a conversation turn, so it can be asked about.

A side chat anchors to a parent *turn* and derives its pinned evidence from
that turn's stored result. A card is not a turn — but it has the same shape: a
question, a cited answer, and evidence behind it. Writing it down as one is
what lets highlighting a sentence on a card reuse the whole side-chat
mechanism instead of growing a parallel one.

The one real translation is the locator. A card cites a node and a page,
because that is what a reader needs to open the book at. Pinning works in
chunk ids, because that is what retrieval ranks. This module resolves the
first into the second, and says nothing when it cannot.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from observability import traced
from storage.database import parse_owner_id
from study.contracts import CitationRef, ConversationState, EvidenceRef, TurnResult
from study.conversation import new_conversation_state

from .contracts import CardBack, DeckCard, DeckCitation

# A card cites a handful of pages; each page may sit in more than one chunk.
# Enough to pin what the card rests on without crowding out retrieval.
MAXIMUM_RESOLVED_CHUNKS = 8


def render_back(card: DeckCard) -> str:
    """The card's answer as the prose a conversation turn stores.

    Citation markers are kept exactly as generated. A side chat pins what the
    highlighted passage's markers name, so stripping them here — the way the
    review interface does for readability — would quietly remove the anchor's
    strongest signal.
    """

    back: CardBack = card.back
    parts: list[str] = []
    if back.say_it_aloud.strip():
        parts.append(back.say_it_aloud.strip())
    if back.answer.strip():
        parts.append(back.answer.strip())
    if back.why_it_matters.strip():
        parts.append(back.why_it_matters.strip())

    if card.card_type == "mcq" and back.options:
        parts.append(
            "\n".join(
                f"- **{option.label}.** {option.text}"
                + (f" — {option.rationale}" if option.rationale else "")
                + ("  ✓" if option.correct else "")
                for option in back.options
            )
        )

    for heading, items in (
        ("Key points", back.key_points),
        ("Components", back.components),
        ("Data flow", back.data_flow),
        ("Trade-offs", back.trade_offs),
        ("Failure modes", back.failure_modes),
    ):
        if items:
            parts.append(
                f"**{heading}**\n"
                + "\n".join(f"- {item}" for item in items)
            )

    # Deliberately excluded: `interview_angle` is model knowledge, and a turn's
    # answer is the thing a later turn may be asked to transform or shorten.
    # Letting it in here would launder an uncited claim into the conversation.
    return "\n\n".join(parts)


def resolve_chunks(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_id: int,
    citations: list[DeckCitation],
    limit: int = MAXIMUM_RESOLVED_CHUNKS,
) -> list[dict[str, Any]]:
    """The chunks a card's cited pages actually live in.

    Empty is a valid answer — a book whose chunks were rebuilt since the deck
    was generated may no longer have a chunk on that page. A side chat then
    falls back to ordinary retrieval, which is worse than pinning and much
    better than pinning something that is not what the reader highlighted.
    """

    nodes = [
        citation.node_id
        for citation in citations
        if citation.node_id is not None and citation.page is not None
    ]
    pages = [
        citation.page
        for citation in citations
        if citation.node_id is not None and citation.page is not None
    ]
    if not nodes:
        return []

    # Paired with `unnest` rather than a composite `= any(...)`: Postgres
    # rejects an anonymous composite as a bound parameter, and matching the two
    # arrays independently would resolve node A's page against node B's.
    rows = connection.execute(
        """
        select distinct
            chunks.id as chunk_id,
            chunks.source_node_id as node_id,
            chunks.chunk_index,
            chunks.path_text,
            chunks.text,
            chunks.start_page,
            chunks.end_page
        from public.chunks
        join public.chunk_sources
          on chunk_sources.chunk_id = chunks.id
         and chunk_sources.owner_id = chunks.owner_id
        join unnest(%s::bigint[], %s::int[]) as wanted(node_id, page)
          on wanted.node_id = chunks.source_node_id
         and wanted.page = chunk_sources.page_number
        where chunks.owner_id = %s
          and chunks.source_book_id = %s
        order by chunks.source_node_id, chunks.chunk_index
        limit %s
        """,
        (nodes, pages, parse_owner_id(owner_id), book_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


@traced("decks.conversation.card_turn_result", flow="cards")
def card_turn_result(
    connection: Connection,
    *,
    owner_id: str | UUID,
    card: DeckCard,
    book_id: int,
    book_title: str,
    deck_title: str,
) -> TurnResult:
    """The card, expressed as the turn a side chat can anchor to."""

    chunks = resolve_chunks(
        connection, owner_id=owner_id, book_id=book_id, citations=card.citations
    )
    evidence = [
        EvidenceRef(
            node_id=row["node_id"],
            pages=sorted({row["start_page"], row["end_page"]}),
            path=row["path_text"],
            book_id=book_id,
            book_title=book_title,
            rank=rank,
            chunk_id=row["chunk_id"],
            chunk_index=row["chunk_index"],
            # Not a search: these chunks were named by the card's own
            # citations. The inspector must not imply they were ranked.
            retrieval_method="card_citation",
            score=1.0,
            excerpt=row["text"],
        )
        for rank, row in enumerate(chunks, start=1)
    ]

    rank_by_node = {reference.node_id: reference.rank for reference in evidence}
    citations = [
        CitationRef(
            marker=citation.marker,
            node_id=citation.node_id,
            page=citation.page,
            book_id=book_id,
            evidence_rank=rank_by_node.get(citation.node_id),
        )
        for citation in card.citations
        if citation.node_id is not None and citation.page is not None
    ]

    return TurnResult(
        question=card.front,
        answer=render_back(card),
        route="retrieval_qa",
        history_dependency="independent",
        standalone_query=card.front,
        evidence=evidence,
        citations=citations,
        outcome="answer",
        retrieval_mode="card_citation",
        answer_archetype=(
            "system_design" if card.card_type == "system_design" else "concept_explanation"
        ),
        response_depth="interview",
        routing_reason=f"Seeded from a flashcard in {deck_title}.",
        warnings=(
            []
            if evidence
            else [
                "This card's pages are not in the current chunk build, so the "
                "side chat retrieves rather than pins."
            ]
        ),
    )


def seeded_state(
    conversation_id: UUID,
    *,
    book_ids: list[int],
    result: TurnResult,
) -> ConversationState:
    """Open a card's side chat already knowing the card it was opened over.

    Same reasoning as the answer path: in a side chat "the previous answer" is
    the thing being asked about, so "shorten this" works on the first turn.
    """

    state = new_conversation_state(
        book_ids=book_ids, conversation_id=str(conversation_id)
    )
    state.previous_answer = result.answer
    state.previous_evidence = list(result.evidence)
    state.previous_citations = list(result.citations)
    state.previous_route = result.route
    return state
