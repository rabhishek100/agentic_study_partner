"""Reproduce a resolved scope as ordered, reading-sized chat segments.

No model is called anywhere in this module. A verbatim passage is a
rearrangement of canonical storage into something a phone can read, and every
decision it makes — what to include, where to break, what to call a page — is
one a reader can check against the book.

The one liberty taken with the source text is splitting a stored block at
blank lines the block already contains. That boundary was written by the
author and preserved by the parser; splitting there changes no characters.
Nothing else is reflowed, rewrapped, joined, or trimmed beyond surrounding
whitespace.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from parsing.models import NON_CONTENT_CATEGORIES
from storage.database import parse_owner_id

from .content import EvidenceBundle
from .contracts import FigureRef, PassageSegment, ReadingRef
from .scope import ResolvedScope

# One installment. Measured against the corpus rather than guessed: a chapter
# of the library's technical books averages about 1 700 characters per printed
# page, so this is roughly three or four pages — long enough that continuing is
# occasional, short enough that a phone renders it instantly.
DEFAULT_INSTALLMENT_CHARACTERS = 6_000
MAXIMUM_INSTALLMENT_CHARACTERS = 40_000

# A whole-book verbatim request on a 900-page reference would hold several
# megabytes of text in memory to count it. Refusing is better than serving it
# slowly, and the refusal can name the thing the reader almost certainly meant.
MAXIMUM_PASSAGE_CHARACTERS = 2_000_000


class PassageTooLargeError(ValueError):
    """The requested scope is too large to reproduce in a conversation."""


@dataclass(frozen=True)
class ReadingPassage:
    """Every segment of one scope, in reading order."""

    scope: ResolvedScope
    segments: tuple[PassageSegment, ...]
    omitted_block_count: int

    @property
    def total_characters(self) -> int:
        return sum(len(segment.text or "") for segment in self.segments)

    def reference(self) -> ReadingRef:
        scope = self.scope
        return ReadingRef(
            book_id=scope.book_id,
            book_title=scope.book_title,
            node_id=scope.root_node_id,
            kind=scope.kind,
            display_path=scope.display_path,
            start_page=scope.start_page,
            end_page=scope.end_page,
            printed_start_page=scope.printed_page(scope.start_page),
            printed_end_page=scope.printed_page(scope.end_page),
            total_segments=len(self.segments),
            total_characters=self.total_characters,
            omitted_block_count=self.omitted_block_count,
        )


def load_figure_details(
    connection: Connection,
    node_ids: Sequence[int],
    *,
    owner_id: str | UUID,
) -> dict[int, dict]:
    """Captions and decorative verdicts for the image blocks in a scope.

    Loaded here rather than widened into `load_scope_content`, which the
    summarizer also uses and which deliberately ignores images. The decorative
    rule is the one `study.figures` applies: publisher badges and rendered
    headings were marked at ingest and are not part of the chapter.
    """

    if not node_ids:
        return {}
    owner = parse_owner_id(owner_id)
    rows = connection.execute(
        """
        select
            content_blocks.id as block_id,
            image_captions.caption,
            image_captions.skipped_reason
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
        """,
        (owner, list(node_ids)),
    ).fetchall()
    return {
        row["block_id"]: {
            "caption": row["caption"],
            "decorative": row["skipped_reason"] is not None
            and row["skipped_reason"] != "failed",
        }
        for row in rows
    }


# How the parser's own block categories are set on the page. This is the whole
# of the typographic judgment in this module, and none of it is a judgment
# about the *text*: a list item carries exactly the characters the parser
# stored, and only its setting says it is a list item.
#
# `UncategorizedText` deliberately falls through to a paragraph. It is the
# parser's "I could not tell", and 18 000 blocks of it across the corpus are
# ordinary prose the classifier declined to label.
SEGMENT_KIND_BY_CATEGORY = {
    "Title": "heading",
    "ListItem": "list_item",
    "FigureCaption": "caption",
    "Formula": "formula",
}

# A rotated stamp in a page margin — an arXiv identifier down the side of a
# preprint's first page — reaches the layout parser as one block per glyph.
# Four in a row is the signature: no prose block is a single character, and a
# run of them is marginalia rather than a sequence of one-letter paragraphs.
# Below this length the blocks are kept, because a lone short block is far
# more likely to be an equation fragment or a stray numeral in real text.
MARGINALIA_RUN_LENGTH = 4


def _marginalia(blocks: Sequence) -> set[int]:
    """Block ids belonging to a run of single-glyph blocks on one page."""

    marked: set[int] = set()
    run: list = []

    def close() -> None:
        if len(run) >= MARGINALIA_RUN_LENGTH:
            marked.update(block.id for block in run)
        run.clear()

    for block in blocks:
        single = (
            block.block_type == "text"
            and len((block.readable_text or "").strip()) == 1
        )
        if single and (not run or run[-1].page_number == block.page_number):
            run.append(block)
            continue
        close()
        if single:
            run.append(block)
    close()
    return marked


def _paragraphs(text: str) -> list[str]:
    """Split one stored block at the blank lines it already contains."""

    parts = [part.strip() for part in text.split("\n\n")]
    return [part for part in parts if part]


def build_reading_passage(
    evidence: EvidenceBundle,
    *,
    figure_details: dict[int, dict] | None = None,
) -> ReadingPassage:
    """Turn one loaded scope into ordered segments, reproducing its text."""

    details = figure_details or {}
    scope = evidence.scope
    segments: list[PassageSegment] = []
    omitted = 0

    def add(**fields) -> None:
        segments.append(PassageSegment(index=len(segments), **fields))

    for node_content in evidence.nodes:
        node = node_content.node
        opened = False
        stamped = _marginalia(node_content.blocks)

        def open_node() -> None:
            # Emitted lazily so a node the parser recorded but left empty does
            # not put a bare heading in front of the reader.
            nonlocal opened
            if opened:
                return
            opened = True
            add(
                kind="heading",
                node_id=node.id,
                page=node.start_page,
                printed_page=scope.printed_page(node.start_page),
                level=node.level,
                text=node.title,
            )

        for block in node_content.blocks:
            if block.category in NON_CONTENT_CATEGORIES or block.id in stamped:
                omitted += 1
                continue

            printed = scope.printed_page(block.page_number)

            if block.block_type == "image":
                detail = details.get(block.id, {})
                if not block.has_image_payload or detail.get("decorative"):
                    omitted += 1
                    continue
                open_node()
                add(
                    kind="figure",
                    node_id=node.id,
                    page=block.page_number,
                    printed_page=printed,
                    figure=FigureRef(
                        book_id=scope.book_id,
                        node_id=node.id,
                        block_id=block.id,
                        page=block.page_number,
                        mime_type=block.image_mime_type or "image/png",
                        path=node.path_text,
                        caption=detail.get("caption"),
                    ),
                )
                continue

            if block.block_type == "table":
                flat = (block.table_text or "").strip()
                html = (block.table_html or "").strip()
                if not flat and not html:
                    omitted += 1
                    continue
                open_node()
                add(
                    kind="table",
                    node_id=node.id,
                    page=block.page_number,
                    printed_page=printed,
                    text=flat,
                    html=html or None,
                )
                continue

            body = (block.readable_text or "").strip()
            if not body:
                omitted += 1
                continue
            open_node()
            kind = SEGMENT_KIND_BY_CATEGORY.get(block.category, "text")
            for paragraph in _paragraphs(body):
                add(
                    kind=kind,
                    node_id=node.id,
                    page=block.page_number,
                    printed_page=printed,
                    # A heading the parser found *inside* a node sits one
                    # level under that node's own title, which is the only
                    # structure a paper has: its hierarchy is one node deep.
                    level=node.level + 1 if kind == "heading" else None,
                    text=paragraph,
                )

    passage = ReadingPassage(
        scope=scope,
        segments=tuple(segments),
        omitted_block_count=omitted,
    )
    if passage.total_characters > MAXIMUM_PASSAGE_CHARACTERS:
        raise PassageTooLargeError(
            f"{scope.display_path} is too long to read in the chat "
            f"({passage.total_characters:,} characters). Ask for one chapter "
            "or section at a time."
        )
    return passage


@dataclass(frozen=True)
class Installment:
    """One reading-sized run of segments, plus where the next one starts."""

    segments: tuple[PassageSegment, ...]
    offset: int
    next_offset: int | None


def installment(
    segments: Sequence[PassageSegment],
    *,
    offset: int = 0,
    max_characters: int = DEFAULT_INSTALLMENT_CHARACTERS,
) -> Installment:
    """Take whole segments up to a character budget, never splitting one.

    At least one segment is always returned when any remain, so a single
    paragraph longer than the whole budget still arrives rather than
    stalling the reader at a boundary it can never cross.
    """

    budget = max(1, min(int(max_characters), MAXIMUM_INSTALLMENT_CHARACTERS))
    start = max(0, int(offset))
    taken: list[PassageSegment] = []
    used = 0
    for segment in segments[start:]:
        cost = len(segment.text or "")
        if taken and used + cost > budget:
            break
        taken.append(segment)
        used += cost
    end = start + len(taken)
    return Installment(
        segments=tuple(taken),
        offset=start,
        next_offset=end if end < len(segments) else None,
    )


def passage_headline(passage: ReadingPassage) -> str:
    """The one line the turn's answer carries above the passage itself.

    Written here rather than in the query layer because it is the only prose
    this route produces, and it should be readable next to the rule it
    describes: what follows is reproduced, not summarized.
    """

    reference = passage.reference()
    start = reference.printed_start_page or reference.start_page
    end = reference.printed_end_page or reference.end_page
    pages = f"page {start}" if start == end else f"pages {start}–{end}"
    # A whole-document scope's path *is* the title, and "Title, pages 1-17 of
    # Title" reads as a bug.
    within = (
        "" if reference.display_path == reference.book_title
        else f" of *{reference.book_title}*"
    )
    return (
        f"**{reference.display_path}** · {pages}{within}\n\n"
        "The complete text follows, reproduced from the source rather than "
        "summarized."
    )


def node_ids(evidence: EvidenceBundle) -> Iterable[int]:
    return (node_content.node.id for node_content in evidence.nodes)
