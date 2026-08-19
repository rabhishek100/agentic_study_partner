"""Resolve a reader's selection in a source to canonical content.

A side chat's quote anchor points at generated answer text, which can never be
cited. A **source** anchor points at the reader's own material, so what it names
*is* citable — but only after the server has matched it back to something
canonical. That asymmetry is the whole reason this module exists, and it is
what keeps *source vs. derived* true for selections:

*   The client sends a page, a node, or a run of text it read off a rendered
    page. None of that is evidence.
*   This module turns it into chunk ids from `public.chunks`, which are.
*   A selection that matches nothing is **not** guessed at. The reader's words
    still reach the model as context, the page they were made on still grounds
    the answer, and the turn records that the selection itself did not resolve.

Resolution is deterministic — page arithmetic and string containment, no model
call — so it runs on every book already in the database and cannot drift from
the parser. The matching direction mirrors `frontend/lib/pdf-match.ts`, which
solves the same problem the other way round when a citation highlights a
rendered page.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id

from .contracts import (
    DocumentAnchor,
    DocumentPageAnchor,
    DocumentPassageAnchor,
    DocumentSectionAnchor,
)
from .scope import ScopeResolutionError, resolve_node
from .side_context import AnchoredSource

# The same ceiling `side_context` applies to pinned chunks, for the same
# reason: a side turn's evidence set has to stay near a normal turn's size, or
# the cost argument for asking small questions in a small window inverts. A
# section anchor would otherwise pin a whole chapter.
MAX_CHUNKS_PER_ANCHOR = 3

# How much of a selection has to be found for it to count as matched. Enough
# to be specific — a dozen words of technical prose occur once in a book — and
# short enough to survive a selection that runs across a chunk boundary, where
# neither chunk holds the whole thing.
PROBE_WORDS = 12

# Ligatures the PDF text layer emits as single code points, and the quotes and
# dashes typesetting substitutes. `NFKC` already decomposes the ligatures; the
# punctuation it leaves alone, and a selection carrying a typographic
# apostrophe would then fail to match canonical text carrying a plain one.
_PUNCTUATION = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        "­": "",
    }
)

# A word broken across a rendered line keeps its hyphen in the text layer; a
# genuine compound keeps one too, and nothing in the text distinguishes the two
# without a dictionary. Both sides of every comparison are folded through this,
# so the hyphen simply stops being a difference: "class-\nimbalance" and
# "class-imbalance" both become "classimbalance". The cost is that "re-cover"
# and "recover" collide, which inside one page of one book is a price worth
# paying to make line-broken selections match at all.
_WORD_HYPHEN = re.compile(r"(\w)-\s*(\w)")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Fold a run of text to the form both sides of a match are compared in.

    Case, whitespace, ligatures, typographic punctuation and hyphenation are
    all differences between what a reader sees and what the parser stored, and
    none of them change which passage is meant.
    """

    folded = unicodedata.normalize("NFKC", text or "").translate(_PUNCTUATION)
    folded = _WORD_HYPHEN.sub(r"\1\2", folded)
    return _WHITESPACE.sub(" ", folded).strip().casefold()


def _probe(normalised: str, *, words: int = PROBE_WORDS) -> str:
    return " ".join(normalised.split(" ")[:words])


def _tail_probe(normalised: str, *, words: int = PROBE_WORDS) -> str:
    return " ".join(normalised.split(" ")[-words:])


@dataclass(frozen=True)
class ResolvedAnchor:
    """One anchor, and the canonical content it names.

    `matched` is about the *selection*, not about the anchor: an unmatched
    passage still carries the chunks of the page it was made on, because the
    page is where the reader was and grounding there is better than grounding
    nowhere. What it must not do is pretend the selected sentence was found.
    """

    anchor_id: str
    kind: str
    chunk_ids: tuple[str, ...]
    label: str
    selected_text: str = ""
    matched: bool = True
    dropped: tuple[str, ...] = ()

    def as_source(self) -> AnchoredSource:
        """Hand this to the context assembler in the terms it works in.

        The assembler is surface-neutral on purpose, so this is the one-way
        door out of book vocabulary: chunk ids become identities, and the
        lecture resolver produces the same struct out of evidence units.
        """

        return AnchoredSource(
            anchor_id=self.anchor_id,
            label=self.label,
            identities=self.chunk_ids,
            selected_text=self.selected_text,
            matched=self.matched,
        )


def _page_rows(
    connection: Connection,
    *,
    owner: UUID,
    book_id: int,
    page: int,
) -> list[Any]:
    """Every chunk covering a page, in reading order.

    No build filter: `replace_book_chunks` deletes a book's prior builds before
    inserting the new one, so a book has exactly one live set of chunks and
    retrieval does not filter either.
    """

    return connection.execute(
        """
        select id, section_title, path_text, start_page, end_page, text
        from chunks
        where owner_id = %s
          and source_book_id = %s
          and start_page <= %s
          and end_page >= %s
        order by toc_index, chunk_index
        """,
        (owner, book_id, page, page),
    ).fetchall()


def _capped(
    chunk_ids: Sequence[str],
    *,
    what: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if len(chunk_ids) <= MAX_CHUNKS_PER_ANCHOR:
        return tuple(chunk_ids), ()
    dropped = (
        f"{len(chunk_ids) - MAX_CHUNKS_PER_ANCHOR} chunk(s) of {what} beyond "
        f"the limit of {MAX_CHUNKS_PER_ANCHOR}",
    )
    return tuple(chunk_ids[:MAX_CHUNKS_PER_ANCHOR]), dropped


def _page_label(rows: Sequence[Any], page: int) -> str:
    title = (rows[0]["section_title"] if rows else "") or ""
    return f"p. {page} · {title}".rstrip(" ·") if title else f"p. {page}"


def _resolve_page(
    connection: Connection,
    anchor: DocumentPageAnchor,
    *,
    owner: UUID,
) -> ResolvedAnchor:
    rows = _page_rows(
        connection,
        owner=owner,
        book_id=anchor.book_id,
        page=anchor.page,
    )
    chunk_ids, dropped = _capped(
        [row["id"] for row in rows],
        what=f"page {anchor.page}",
    )
    return ResolvedAnchor(
        anchor_id=anchor.anchor_id,
        kind=anchor.kind,
        chunk_ids=chunk_ids,
        label=_page_label(rows, anchor.page),
        # A page with no parsed text — a full-page figure, an unOCR'd scan —
        # is a real state, and the honest report of it is "nothing matched"
        # rather than an empty success.
        matched=bool(rows),
        dropped=dropped,
    )


def _resolve_passage(
    connection: Connection,
    anchor: DocumentPassageAnchor,
    *,
    owner: UUID,
) -> ResolvedAnchor:
    rows = _page_rows(
        connection,
        owner=owner,
        book_id=anchor.book_id,
        page=anchor.page,
    )
    wanted = normalise(anchor.selected_text)
    label = _page_label(rows, anchor.page)

    whole = [row["id"] for row in rows if wanted and wanted in normalise(row["text"])]
    if whole:
        chunk_ids, dropped = _capped(whole, what="the selection")
        return ResolvedAnchor(
            anchor_id=anchor.anchor_id,
            kind=anchor.kind,
            chunk_ids=chunk_ids,
            label=label,
            selected_text=anchor.selected_text,
            dropped=dropped,
        )

    # A selection that runs across a chunk boundary is contained by neither
    # chunk. Its head and its tail still are, and taking both is what keeps a
    # two-paragraph selection anchored to both paragraphs.
    head, tail = _probe(wanted), _tail_probe(wanted)
    partial = [
        row["id"]
        for row in rows
        if (head and head in normalise(row["text"]))
        or (tail and tail in normalise(row["text"]))
    ]
    if partial:
        chunk_ids, dropped = _capped(partial, what="the selection")
        return ResolvedAnchor(
            anchor_id=anchor.anchor_id,
            kind=anchor.kind,
            chunk_ids=chunk_ids,
            label=label,
            selected_text=anchor.selected_text,
            dropped=dropped,
        )

    # Nothing on the page contains the selection. Drawn text — a figure label,
    # a caption baked into an image — is the usual cause, and it is a designed
    # state rather than an error: ground on the page, quote the words, and say
    # the selection itself did not resolve.
    chunk_ids, dropped = _capped(
        [row["id"] for row in rows],
        what=f"page {anchor.page}",
    )
    return ResolvedAnchor(
        anchor_id=anchor.anchor_id,
        kind=anchor.kind,
        chunk_ids=chunk_ids,
        label=label,
        selected_text=anchor.selected_text,
        matched=False,
        dropped=dropped,
    )


def _resolve_section(
    connection: Connection,
    anchor: DocumentSectionAnchor,
    *,
    owner: UUID,
) -> ResolvedAnchor:
    try:
        scope = resolve_node(connection, anchor.node_id, owner_id=owner)
    except ScopeResolutionError:
        # The node was removed, or belongs to another owner's book. Nothing to
        # pin, and nothing to invent.
        return ResolvedAnchor(
            anchor_id=anchor.anchor_id,
            kind=anchor.kind,
            chunk_ids=(),
            label=f"section {anchor.node_id}",
            matched=False,
        )
    rows = connection.execute(
        """
        select id
        from chunks
        where owner_id = %s and source_book_id = %s and source_node_id = any(%s)
        order by toc_index, chunk_index
        """,
        (owner, scope.book_id, list(scope.node_ids)),
    ).fetchall()
    chunk_ids, dropped = _capped(
        [row["id"] for row in rows],
        what=scope.display_path,
    )
    return ResolvedAnchor(
        anchor_id=anchor.anchor_id,
        kind=anchor.kind,
        chunk_ids=chunk_ids,
        label=scope.display_path,
        matched=bool(rows),
        dropped=dropped,
    )


def resolve_document_anchors(
    connection: Connection,
    anchors: Iterable[DocumentAnchor],
    *,
    owner_id: str | UUID,
) -> tuple[ResolvedAnchor, ...]:
    """Resolve every document anchor to the canonical chunks it names."""

    owner = parse_owner_id(owner_id)
    resolved: list[ResolvedAnchor] = []
    for anchor in anchors:
        if isinstance(anchor, DocumentPageAnchor):
            resolved.append(_resolve_page(connection, anchor, owner=owner))
        elif isinstance(anchor, DocumentPassageAnchor):
            resolved.append(_resolve_passage(connection, anchor, owner=owner))
        elif isinstance(anchor, DocumentSectionAnchor):
            resolved.append(_resolve_section(connection, anchor, owner=owner))
        else:  # pragma: no cover - the union has no fourth document member
            raise TypeError(f"not a document anchor: {type(anchor).__name__}")
    return tuple(resolved)
