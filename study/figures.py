"""Select the figures that sit inside an answer's evidence.

Selection is deterministic and explainable: a figure appears if and only if
its canonical block lives in a node the answer drew on *and* on a page that
node's evidence actually covers. There is no model call, no ranking, and no
threshold, which is what makes the rule defensible — "this diagram is on a
page the answer cites" is a claim anyone can check.

Since captioning was added, this is the *fallback* path rather than the main
one. A captioned figure carries searchable text, so it can be retrieved and
cited like any other evidence and placed where the answer refers to it. This
rule still runs so that a figure sitting on a cited page is offered even when
the model did not cite the figure itself.

Figures the captioner marked decorative — publisher badges, rendered headings —
are excluded here, because showing them was the worst thing about the first
version of this gallery.
"""

from collections.abc import Iterable, Sequence
import logging
from uuid import UUID

from psycopg import Connection

from base64 import b64encode

from storage.book_images import load_figure
from storage.database import parse_owner_id
from video.media_store import MediaStoreError

from .contracts import CitationRef, EvidenceRef, FigureRef


logger = logging.getLogger("study_partner.study.figures")


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
            image_captions.caption,
            image_captions.skipped_reason,
            nodes.path_text,
            nodes.toc_index
        from content_blocks
        join image_blocks
          on image_blocks.block_id = content_blocks.id
         and image_blocks.owner_id = content_blocks.owner_id
        left join image_captions
          on image_captions.block_id = content_blocks.id
         and image_captions.owner_id = content_blocks.owner_id
        join nodes
          on nodes.id = content_blocks.node_id
         and nodes.owner_id = content_blocks.owner_id
        where content_blocks.owner_id = %s
          and content_blocks.block_type = 'image'
          and content_blocks.node_id = any(%s)
          -- Publisher badges and decorative fragments are recorded with a
          -- reason at ingest; showing them was the single worst thing about
          -- the first version of this gallery.
          and (
              image_captions.skipped_reason is null
              or image_captions.skipped_reason = 'failed'
          )
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
                caption=row["caption"],
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


# Two is the cap, and it is about attention rather than bytes. A page with six
# figures on it is a page whose figures are decoration; the ones worth sending
# are the ones the cited evidence actually sits beside.
DEFAULT_IMAGE_LIMIT = 2


def load_figure_images(
    connection: Connection,
    *,
    owner_id: str | UUID,
    figures: Sequence[FigureRef],
    limit: int = DEFAULT_IMAGE_LIMIT,
) -> list[tuple[str, str]]:
    """The bytes behind the first few figures, as (mime type, base64).

    Stored base64 already, so this is a read rather than an encode. Returned in
    the order given, because the labels the model is shown ([F1], [F2]) are
    positional and an answer citing [F2] has to mean the second one.
    """

    wanted = list(figures)[:limit]
    if not wanted:
        return []
    owner = parse_owner_id(owner_id)
    rows = connection.execute(
        """
        select block_id, owner_id, mime_type, storage_key
        from image_blocks
        where owner_id = %s and block_id = any(%s)
        """,
        (owner, [figure.block_id for figure in wanted]),
    ).fetchall()
    by_block = {row["block_id"]: row for row in rows}
    images: list[tuple[str, str]] = []
    for figure in wanted:
        row = by_block.get(figure.block_id)
        if row is None:
            continue
        # A figure whose bytes are missing is skipped rather than sent as an
        # empty image: the label positions would shift under the model. The
        # model wants base64, so a stored object is re-encoded on the way out
        # rather than being kept that way at rest.
        try:
            payload = load_figure(row)
        except MediaStoreError as error:
            logger.warning("figure %s could not be read: %s", row["block_id"], error)
            continue
        images.append((row["mime_type"], b64encode(payload).decode("ascii")))
    return images
