"""Select the figures that sit inside an answer's evidence.

Selection is deterministic and explainable: a figure appears if and only if
its canonical block lives in a node the answer drew on *and* on a page that
node's evidence actually covers. There is no model call, no ranking, and no
threshold, which is what makes the rule defensible — "this diagram is on a
page the answer cites" is a claim anyone can check.

Known limitation, recorded rather than hidden: a figure one page outside the
cited range is missed, and a decorative image on a cited page is indistinguish-
able from a substantive diagram. The upgrade path is captioning images with a
vision model at ingest so figures compete in retrieval on their own merit;
per AGENTS.md that is justified by a measured failure, so
`scripts/evaluate_figures.py` measures this rule's precision and recall first.
"""

from collections.abc import Iterable, Sequence
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id

from .contracts import CitationRef, EvidenceRef, FigureRef


# Measured across the real corpus (`scripts.evaluate_figures`, 810 figures in
# four books): 809 of 810 sit on a page their own node's text also covers, so a
# tolerance of 0 is not what limits this rule. Widening it would buy almost
# nothing and let neighbouring sections' figures leak in.
PAGE_TOLERANCE = 0

# The same measurement found a book with 225 figures spread over 15 nodes — a
# shallow table of contents concentrates images into very few nodes. Citing one
# such node would otherwise render dozens of figures under a short answer, so
# the count is capped and the excess is reported rather than silently dropped.
DEFAULT_FIGURE_LIMIT = 6


def _cited_pages(
    evidence: Sequence[EvidenceRef],
    citations: Sequence[CitationRef],
) -> dict[int, set[int]]:
    """Map each node the answer *cited* to the pages it cited there.

    Retrieval returns a fixed number of candidates whether or not the answer
    uses them, so selecting from all of it surfaced figures from passages the
    answer never referred to — four figures under an answer citing one source.
    Citations are therefore authoritative when present: a figure is shown
    because the answer pointed at its page, not because retrieval happened to
    return its neighbourhood.

    Evidence is the fallback for turns that record no citation at all — an
    abstention, or a summary whose markers were stripped — where the answer
    still rests on everything retrieved.
    """

    pages: dict[int, set[int]] = {}
    if citations:
        cited_nodes = {citation.node_id for citation in citations}
        for citation in citations:
            pages.setdefault(citation.node_id, set()).add(citation.page)
        # A cited node's evidence pages count too: the model names one page per
        # marker, but the passage it drew on can span several.
        for reference in evidence:
            if reference.node_id in cited_nodes:
                pages.setdefault(reference.node_id, set()).update(reference.pages)
        return pages

    for reference in evidence:
        pages.setdefault(reference.node_id, set()).update(reference.pages)
    return pages


def _expand(pages: Iterable[int], tolerance: int) -> set[int]:
    if tolerance <= 0:
        return set(pages)
    widened: set[int] = set()
    for page in pages:
        widened.update(range(page - tolerance, page + tolerance + 1))
    return widened


def select_figures(
    connection: Connection,
    *,
    owner_id: str | UUID,
    evidence: Sequence[EvidenceRef],
    citations: Sequence[CitationRef] = (),
    page_tolerance: int = PAGE_TOLERANCE,
    limit: int = DEFAULT_FIGURE_LIMIT,
) -> list[FigureRef]:
    """Return the figures inside the answer's cited evidence, in reading order.

    At most `limit` are returned, preferring the best-ranked evidence, because
    one node in a shallow table of contents can own hundreds of figures.
    """

    wanted = _cited_pages(evidence, citations)
    if not wanted:
        return []

    owner = parse_owner_id(owner_id)
    rank_by_node = {
        reference.node_id: reference.rank
        for reference in evidence
        if reference.rank is not None
    }

    rows = connection.execute(
        """
        select
            content_blocks.id as block_id,
            content_blocks.book_id,
            content_blocks.node_id,
            content_blocks.page_number,
            content_blocks.block_index,
            image_blocks.mime_type,
            nodes.path_text,
            nodes.toc_index
        from content_blocks
        join image_blocks
          on image_blocks.block_id = content_blocks.id
         and image_blocks.owner_id = content_blocks.owner_id
        join nodes
          on nodes.id = content_blocks.node_id
         and nodes.owner_id = content_blocks.owner_id
        where content_blocks.owner_id = %s
          and content_blocks.block_type = 'image'
          and content_blocks.node_id = any(%s)
        order by nodes.toc_index, content_blocks.block_index
        """,
        (owner, list(wanted)),
    ).fetchall()

    figures: list[FigureRef] = []
    for row in rows:
        allowed = _expand(wanted.get(row["node_id"], set()), page_tolerance)
        if row["page_number"] not in allowed:
            continue
        figures.append(
            FigureRef(
                book_id=row["book_id"],
                node_id=row["node_id"],
                block_id=row["block_id"],
                page=row["page_number"],
                mime_type=row["mime_type"],
                path=row["path_text"],
                evidence_rank=rank_by_node.get(row["node_id"]),
            )
        )

    return apply_limit(figures, limit)


def apply_limit(figures: list[FigureRef], limit: int) -> list[FigureRef]:
    """Keep the best-ranked figures, restoring reading order afterwards.

    Ranking decides *which* survive; the gallery still runs front-to-back
    through the book so it reads like the book does.
    """

    if limit < 0:
        raise ValueError("limit cannot be negative")
    if len(figures) <= limit:
        return list(figures)

    def rank_key(indexed: tuple[int, FigureRef]) -> tuple[int, int, int]:
        position, figure = indexed
        rank = figure.evidence_rank
        # Unranked evidence sorts last; position keeps the order stable.
        return (rank if rank is not None else 10_000, figure.page, position)

    ordered = sorted(enumerate(figures), key=rank_key)[:limit]
    kept = {position for position, _ in ordered}
    return [figure for position, figure in enumerate(figures) if position in kept]
