"""The deterministic coverage contract for one deck.

A deck claims to cover a chapter. That claim is only checkable if the list of
things it must cover is built *before* any model call, from canonical content,
by code that cannot be talked out of an entry. That is what this module is: a
topic inventory, plus the evidence each topic is generated from and the exact
markers a card about it is allowed to cite.

Book topics are the content-bearing nodes of the subtree — the same unit
`study.summarize` requires a summary to cite, because a node is a topic and an
uncited node is a topic that was skipped. Lecture topics are
`video.lecture.coverage_units`: published chapters where the source has them,
transcript windows where it does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import tiktoken
from psycopg import Connection

from parsing.models import NON_CONTENT_CATEGORIES
from storage.database import parse_owner_id
from study.content import EvidenceBundle
from study.context import DEFAULT_ENCODING
from study.summarize import OPTIONAL_INTERVIEW_SECTION, OPTIONAL_RECAP_TITLES
from video.lecture import CoverageUnit, LectureScope

from .contracts import DeckFigure

# A node with less readable text than this is a heading, a stub, or a figure
# caption stranded on its own. Requiring a card for it would force the padding
# the prompts everywhere else in this project forbid, so it becomes an optional
# topic: cards are welcome, absence is not a coverage failure.
SUBSTANTIVE_NODE_CHARACTERS = 400
# Same idea for a lecture: `video.lecture` uses 240 characters for a transcript
# window, and a topic here is at least a window.
SUBSTANTIVE_WINDOW_CHARACTERS = 240

# Sections that are long enough to look required and have nothing to card.
# A chapter recap restates what its own sections already said, and a reading
# list is a list of books. Requiring a card for either forces the padding every
# prompt in this project forbids, so the generator refuses — and the deck was
# then marked `partial` for behaving correctly, which is how this set was
# found. `study.summarize` already draws the same distinction for summaries;
# the recap titles and the lab/exercise pattern come from there rather than
# being restated, and the bibliography titles are the deck's own addition.
NON_CARDABLE_TITLES = OPTIONAL_RECAP_TITLES | {
    "reference material",
    "reference materials",
    "references",
    "further reading",
    "bibliography",
    "acknowledgements",
    "acknowledgments",
    "index",
    "glossary",
}

# One generation call may carry several small adjacent topics. This is a call
# -batching decision only — it never merges two topics into one coverage entry,
# because that is exactly how "we covered everything" quietly becomes untrue.
GENERATION_BATCH_TOKENS = 6_000

# A single node can own hundreds of images in a shallow table of contents. Two
# per topic is what fits beside a card back without turning it into a gallery.
FIGURES_PER_TOPIC = 2


@dataclass(frozen=True)
class Topic:
    """One unit the deck is required to say something about.

    `evidence_text` is what the generation call sees, and `allowed_markers` is
    what a card about it may cite. They are built together, from the same
    blocks, so a marker the model copies from the evidence always resolves and
    a marker it invents never does.
    """

    key: str
    ordinal: int
    label: str
    required: bool
    evidence_text: str
    allowed_markers: frozenset[str]
    figures: tuple[DeckFigure, ...] = ()
    # Book locator.
    node_id: int | None = None
    start_page: int | None = None
    end_page: int | None = None
    # Lecture locator.
    start_ms: int | None = None
    end_ms: int | None = None
    evidence_ranks: tuple[int, ...] = ()

    @property
    def token_estimate(self) -> int:
        return _tokens(self.evidence_text)


@dataclass(frozen=True)
class ScopeInventory:
    """Every topic in one deck's scope, plus the preamble each call shares."""

    source_kind: str
    scope_key: str
    title: str
    source_title: str
    outline: str
    topics: tuple[Topic, ...] = field(default_factory=tuple)

    @property
    def required_topics(self) -> tuple[Topic, ...]:
        return tuple(topic for topic in self.topics if topic.required)


def _tokens(text: str) -> int:
    return len(tiktoken.get_encoding(DEFAULT_ENCODING).encode(text))


def cardable(title: str, path_text: str = "") -> str | bool:
    """Whether a section is the kind of thing a card can be made from.

    Optional, not excluded: a recap sometimes states a comparison more crisply
    than the sections it summarizes, and a card drawn from it is welcome. What
    changes is that its absence stops counting as a coverage failure.
    """

    if title.casefold().strip() in NON_CARDABLE_TITLES:
        return False
    parts = (part.strip() for part in (path_text or title).split(" :: "))
    return not any(OPTIONAL_INTERVIEW_SECTION.match(part) for part in parts)


def book_scope_key(
    book_id: int, node_id: int, generation_mode: str = "topic_generated"
) -> str:
    if generation_mode == "book_extracted":
        return f"book:{book_id}:node:{node_id}:mode:book_extracted"
    return f"book:{book_id}:node:{node_id}"


def video_scope_key(video_id: str | UUID) -> str:
    return f"video:{video_id}"


def paper_scope_key(book_id: int) -> str:
    """Stable identity for a complete paper with several possible roots."""

    return f"paper:{book_id}"


def book_inventory(
    bundle: EvidenceBundle,
    *,
    connection: Connection | None = None,
    owner_id: str | UUID | None = None,
) -> ScopeInventory:
    """Inventory one resolved book scope, one topic per content-bearing node.

    The evidence for a topic is that node's blocks only. A model given the
    whole chapter and asked for cards about section 7.3 writes cards about the
    chapter; a model given section 7.3 writes cards about section 7.3, and its
    citations cannot wander outside it because no other marker is in the
    prompt.
    """

    scope = bundle.scope
    figures_by_node: dict[int, tuple[DeckFigure, ...]] = {}
    if connection is not None and owner_id is not None:
        figures_by_node = _book_figures(
            connection,
            owner_id=owner_id,
            node_ids=[node.node.id for node in bundle.nodes],
        )

    topics: list[Topic] = []
    for node_content in bundle.nodes:
        node = node_content.node
        lines: list[str] = []
        markers: set[str] = set()
        characters = 0
        for block in node_content.blocks:
            if block.category in NON_CONTENT_CATEGORIES:
                continue
            marker = f"[N{block.node_id}:P{block.page_number}]"
            if block.block_type == "image":
                caption = _caption_for(figures_by_node.get(node.id, ()), block.id)
                if not caption:
                    continue
                value = f"Figure: {caption}"
            else:
                value = (block.readable_text or "").strip()
                if block.block_type == "table" and value:
                    value = f"Table:\n{value}"
            if not value:
                continue
            lines.append(f"{marker}\n{value}")
            markers.add(marker)
            characters += len(value)

        if not lines:
            continue

        topics.append(
            Topic(
                key=f"node:{node.id}",
                ordinal=len(topics),
                label=node.path_text or node.title,
                required=(
                    characters >= SUBSTANTIVE_NODE_CHARACTERS
                    and cardable(node.title, node.path_text)
                ),
                evidence_text="\n\n".join(lines),
                allowed_markers=frozenset(markers),
                figures=figures_by_node.get(node.id, ())[:FIGURES_PER_TOPIC],
                node_id=node.id,
                start_page=node.start_page,
                end_page=node.end_page,
            )
        )

    whole_paper = scope.document_type == "paper" and scope.root_node_id is None
    return ScopeInventory(
        source_kind="book",
        scope_key=(
            paper_scope_key(scope.book_id)
            if whole_paper
            else book_scope_key(scope.book_id, scope.root_node_id or 0)
        ),
        title=scope.display_path,
        source_title=scope.book_title,
        outline="\n".join(
            f"- {topic.label} (pp. {topic.start_page}–{topic.end_page})"
            for topic in topics
        ),
        topics=tuple(topics),
    )


def paper_inventory(
    bundle: EvidenceBundle,
    *,
    connection: Connection | None = None,
    owner_id: str | UUID | None = None,
) -> ScopeInventory:
    """Inventory every canonical section of one complete scientific paper."""

    if bundle.scope.document_type != "paper" or bundle.scope.root_node_id is not None:
        raise ValueError("paper inventory requires a complete paper scope")
    inventory = book_inventory(
        bundle, connection=connection, owner_id=owner_id
    )
    return ScopeInventory(
        source_kind=inventory.source_kind,
        scope_key=paper_scope_key(bundle.scope.book_id),
        title="Full paper",
        source_title=inventory.source_title,
        outline=inventory.outline,
        topics=inventory.topics,
    )


def lecture_inventory(
    scope: LectureScope,
    units: tuple[CoverageUnit, ...],
    *,
    video_id: str | UUID,
    title: str,
) -> ScopeInventory:
    """Inventory one lecture from the coverage units the video module defines.

    The units are not recomputed here. `video.lecture.coverage_units` is
    already the answer to "what must a summary of this lecture touch", and a
    deck asking a different question of the same recording would let a lecture
    be fully covered by one feature and not the other.
    """

    by_rank = {item.rank: item for item in scope.citable}
    topics: list[Topic] = []
    for unit in units:
        lines: list[str] = []
        markers: set[str] = set()
        figures: list[DeckFigure] = []
        characters = 0
        for rank in unit.ranks:
            reference = by_rank.get(rank)
            if reference is None or not reference.excerpt.strip():
                continue
            marker = f"[S{rank}]"
            if reference.is_visual:
                lines.append(f"{marker}\nOn screen: {reference.excerpt.strip()}")
                if reference.frame_id is not None:
                    figures.append(
                        DeckFigure(
                            kind="lecture_frame",
                            frame_id=reference.frame_id,
                            start_ms=reference.start_ms,
                            caption=reference.excerpt.strip()[:200] or None,
                        )
                    )
            else:
                lines.append(f"{marker}\n{reference.excerpt.strip()}")
                characters += len(reference.excerpt)
            markers.add(marker)

        if not lines:
            continue

        spans = [
            by_rank[rank]
            for rank in unit.window_ranks or unit.ranks
            if rank in by_rank
        ]
        topics.append(
            Topic(
                key=unit.key,
                ordinal=len(topics),
                label=unit.label,
                required=unit.required and characters >= SUBSTANTIVE_WINDOW_CHARACTERS,
                evidence_text="\n\n".join(lines),
                allowed_markers=frozenset(markers),
                figures=tuple(figures[:FIGURES_PER_TOPIC]),
                start_ms=min((span.start_ms or 0) for span in spans) if spans else None,
                end_ms=max((span.end_ms or 0) for span in spans) if spans else None,
                evidence_ranks=tuple(sorted(unit.ranks)),
            )
        )

    return ScopeInventory(
        source_kind="video",
        scope_key=video_scope_key(video_id),
        title=title,
        source_title=title,
        outline="\n".join(f"- {topic.label}" for topic in topics),
        topics=tuple(topics),
    )


def generation_batches(
    topics: tuple[Topic, ...],
    *,
    token_budget: int = GENERATION_BATCH_TOKENS,
) -> tuple[tuple[Topic, ...], ...]:
    """Group adjacent topics into calls without merging them as coverage units.

    One call per topic is correct and wasteful: a chapter of forty short
    sections would spend forty round trips restating the same preamble. Batches
    keep each call small enough to stay well inside the context window while
    letting neighbouring sections share one, and every topic keeps its own key
    so the coverage check is unaffected by how the calls were grouped.
    """

    batches: list[tuple[Topic, ...]] = []
    current: list[Topic] = []
    used = 0
    for topic in topics:
        cost = topic.token_estimate
        if current and used + cost > token_budget:
            batches.append(tuple(current))
            current, used = [], 0
        current.append(topic)
        used += cost
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _caption_for(figures: tuple[DeckFigure, ...], block_id: int) -> str | None:
    return next(
        (
            figure.caption
            for figure in figures
            if figure.block_id == block_id and figure.caption
        ),
        None,
    )


def _book_figures(
    connection: Connection,
    *,
    owner_id: str | UUID,
    node_ids: list[int],
) -> dict[int, tuple[DeckFigure, ...]]:
    """Captioned figures per node, skipping the decorative ones.

    The same `skipped_reason` filter the answer gallery uses: publisher badges
    and page furniture are recorded with a reason at ingest, and showing them
    was the single worst thing about the first version of that gallery.
    """

    if not node_ids:
        return {}

    rows = connection.execute(
        """
        select
            content_blocks.id as block_id,
            content_blocks.book_id,
            content_blocks.node_id,
            content_blocks.page_number,
            image_blocks.mime_type,
            image_captions.caption
        from content_blocks
        join image_blocks
          on image_blocks.block_id = content_blocks.id
         and image_blocks.owner_id = content_blocks.owner_id
        left join image_captions
          on image_captions.block_id = content_blocks.id
         and image_captions.owner_id = content_blocks.owner_id
        where content_blocks.owner_id = %s
          and content_blocks.block_type = 'image'
          and content_blocks.node_id = any(%s)
          and (
              image_captions.skipped_reason is null
              or image_captions.skipped_reason = 'failed'
          )
        order by content_blocks.node_id, content_blocks.block_index
        """,
        (parse_owner_id(owner_id), node_ids),
    ).fetchall()

    grouped: dict[int, list[DeckFigure]] = {}
    for row in rows:
        grouped.setdefault(row["node_id"], []).append(
            DeckFigure(
                kind="book_image",
                book_id=row["book_id"],
                node_id=row["node_id"],
                block_id=row["block_id"],
                page=row["page_number"],
                mime_type=row["mime_type"],
                caption=row["caption"],
            )
        )
    return {node_id: tuple(items) for node_id, items in grouped.items()}
