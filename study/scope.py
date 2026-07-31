"""Resolve book hierarchy references without retrieval or model calls."""

from dataclasses import dataclass
from collections.abc import Sequence
import re
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection

from parsing.outline_roles import (
    CHAPTER,
    SEARCHABLE_ROLES,
    chapter_number,
)
from retrieval.models import book_scope
from storage.database import parse_owner_id


ScopeKind = Literal["book", "chapter", "section"]
ResolutionKind = Literal["book", "chapter", "section", "scope"]
CHAPTER_NUMBER = re.compile(r"^\s*chapter\s+(\d+)\b", re.IGNORECASE)
NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
# Front matter, parts, and appendices are no longer typed `chapter`, but a
# reader still asks to summarize the preface by name. Only a *number* is
# restricted to chapters; a title may name any top-level scope.
TOP_LEVEL_ROLES = ("chapter", "part", "appendix", "front_matter", "back_matter")


@dataclass(frozen=True)
class ScopeNode:
    """One canonical table-of-contents node."""

    id: int
    book_id: int
    parent_id: int | None
    toc_index: int
    level: int
    node_type: str
    title: str
    path_text: str
    start_page: int
    end_page: int


@dataclass(frozen=True)
class ScopeMatch:
    """A reader-facing scope match used in ambiguity errors."""

    book_id: int
    book_title: str
    node_id: int | None
    path_text: str
    start_page: int
    end_page: int


@dataclass(frozen=True)
class ResolvedScope:
    """An exact book, chapter, or section plus its ordered descendants."""

    kind: ScopeKind
    book_id: int
    book_title: str
    root_node_id: int | None
    display_path: str
    start_page: int
    end_page: int
    nodes: tuple[ScopeNode, ...]

    @property
    def node_ids(self) -> tuple[int, ...]:
        return tuple(node.id for node in self.nodes)


class ScopeResolutionError(ValueError):
    """Base class for deterministic scope resolution failures."""


class ScopeNotFoundError(ScopeResolutionError):
    """No canonical scope matched the supplied reference."""

    def __init__(self, kind: ResolutionKind, reference: object) -> None:
        self.kind = kind
        self.reference = reference
        super().__init__(f"no {kind} matched {reference!r}")


class AmbiguousScopeError(ScopeResolutionError):
    """More than one canonical scope matched the supplied reference."""

    def __init__(
        self,
        kind: ResolutionKind,
        reference: object,
        candidates: tuple[ScopeMatch, ...],
    ) -> None:
        self.kind = kind
        self.reference = reference
        self.candidates = candidates
        choices = "; ".join(
            f"book {candidate.book_id}, {candidate.path_text} "
            f"(PDF pp. {candidate.start_page}–{candidate.end_page})"
            for candidate in candidates
        )
        super().__init__(f"{kind} reference {reference!r} is ambiguous: {choices}")


def _normalize(value: object) -> str:
    return " ".join(NON_WORD.sub(" ", str(value).casefold()).split())


def _node(row: Any) -> ScopeNode:
    return ScopeNode(
        id=row["id"],
        book_id=row["book_id"],
        parent_id=row["parent_id"],
        toc_index=row["toc_index"],
        level=row["toc_level"],
        node_type=row["node_type"],
        title=row["title"],
        path_text=row["path_text"],
        start_page=row["start_page"],
        end_page=row["end_page"],
    )


def _candidate(row: Any) -> ScopeMatch:
    return ScopeMatch(
        book_id=row["book_id"],
        book_title=row["book_title"],
        node_id=row["id"],
        path_text=row["path_text"],
        start_page=row["start_page"],
        end_page=row["end_page"],
    )


def _book_rows(
    connection: Connection,
    *,
    owner_id: UUID,
    book_id: int | None,
    book_ids: Sequence[int] | None = None,
) -> list[Any]:
    scope = book_scope(book_id, book_ids)
    if scope is None:
        rows = connection.execute(
            "select * from books where owner_id = %s order by id",
            (owner_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "select * from books where owner_id = %s and id = any(%s) order by id",
            (owner_id, scope),
        ).fetchall()
    if not rows:
        raise ScopeNotFoundError("book", book_id if scope is None else scope)
    return rows


TITLE_STOPWORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
     "or", "the", "to", "with"}
)


def _title_acronyms(title: str) -> set[str]:
    """Initials a reader would plausibly use for this title.

    Both the full initials and the stopword-stripped ones, since "Designing
    Data-Intensive Applications" is `ddia` either way but "An Introduction to
    Statistical Learning" is `aitsl` written out and `isl` spoken.

    Only the leading words count: a subtitle is not part of how anyone
    abbreviates a book, and including it would make the acronym unusable.
    """

    words = _normalize(title).split()[:6]
    if len(words) < 2:
        return set()
    significant = [word for word in words if word not in TITLE_STOPWORDS]
    acronyms = {"".join(word[0] for word in words)}
    if len(significant) >= 2:
        acronyms.add("".join(word[0] for word in significant))
    return {acronym for acronym in acronyms if len(acronym) >= 3}


def _book_candidate(row: Any) -> ScopeMatch:
    return ScopeMatch(
        book_id=row["id"],
        book_title=row["title"],
        node_id=None,
        path_text=row["title"],
        start_page=1,
        end_page=row["page_count"] or 1,
    )


def _all_book_nodes(
    connection: Connection,
    book_id: int,
    *,
    owner_id: UUID,
) -> tuple[ScopeNode, ...]:
    rows = connection.execute(
        """
        select * from nodes
        where book_id = %s and owner_id = %s
        order by toc_index
        """,
        (book_id, owner_id),
    ).fetchall()
    return tuple(_node(row) for row in rows)


def _subtree_nodes(
    connection: Connection,
    node_id: int,
    *,
    owner_id: UUID,
) -> tuple[ScopeNode, ...]:
    rows = connection.execute(
        """
        WITH RECURSIVE subtree AS (
            SELECT *
            FROM nodes
            WHERE id = %s AND owner_id = %s

            UNION ALL

            SELECT child.*
            FROM nodes AS child
            JOIN subtree AS parent
              ON child.parent_id = parent.id
             AND child.owner_id = parent.owner_id
        )
        SELECT *
        FROM subtree
        ORDER BY toc_index
        """,
        (node_id, owner_id),
    ).fetchall()
    return tuple(_node(row) for row in rows)


def _resolved_node(
    connection: Connection,
    row: Any,
    *,
    owner_id: UUID,
    kind: Literal["chapter", "section"],
) -> ResolvedScope:
    nodes = _subtree_nodes(connection, row["id"], owner_id=owner_id)
    return ResolvedScope(
        kind=kind,
        book_id=row["book_id"],
        book_title=row["book_title"],
        root_node_id=row["id"],
        display_path=row["path_text"],
        start_page=min(node.start_page for node in nodes),
        end_page=max(node.end_page for node in nodes),
        nodes=nodes,
    )


def resolve_book(
    connection: Connection,
    reference: str | None = None,
    *,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
) -> ResolvedScope:
    """Resolve one book by ID or deterministic title matching."""

    owner = parse_owner_id(owner_id)
    rows = _book_rows(
        connection, owner_id=owner, book_id=book_id, book_ids=book_ids
    )
    if reference is not None:
        target = _normalize(reference)
        if not target:
            raise ScopeNotFoundError("book", reference)
        exact = [row for row in rows if _normalize(row["title"]) == target]
        rows = (
            exact
            or [row for row in rows if target in _normalize(row["title"])]
            # Readers abbreviate long titles - "ddia", "dmls". Tried last, so
            # an abbreviation can never take a book a real title match found.
            or [row for row in rows if target in _title_acronyms(row["title"])]
        )
        if not rows:
            raise ScopeNotFoundError("book", reference)
    if len(rows) > 1:
        raise AmbiguousScopeError(
            "book",
            reference,
            tuple(_book_candidate(row) for row in rows),
        )

    book = rows[0]
    nodes = _all_book_nodes(connection, book["id"], owner_id=owner)
    return ResolvedScope(
        kind="book",
        book_id=book["id"],
        book_title=book["title"],
        root_node_id=None,
        display_path=book["title"],
        start_page=1,
        end_page=book["page_count"]
        or max(
            (node.end_page for node in nodes),
            default=1,
        ),
        nodes=nodes,
    )


def list_chapters(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_id: int,
) -> tuple[ScopeNode, ...]:
    """Return canonical chapters in table-of-contents order."""

    owner = parse_owner_id(owner_id)
    _book_rows(connection, owner_id=owner, book_id=book_id)
    rows = connection.execute(
        """
        SELECT *
        FROM nodes
        WHERE book_id = %s AND owner_id = %s AND node_type = 'chapter'
        ORDER BY toc_index
        """,
        (book_id, owner),
    ).fetchall()
    return tuple(_node(row) for row in rows)


def _chapter_aliases(title: str) -> set[str]:
    aliases = {_normalize(title)}
    match = CHAPTER_NUMBER.match(title)
    if match:
        number = match.group(1)
        aliases.update({number, f"chapter {number}"})
        remainder = title[match.end() :].lstrip(" .:—–-")
        if remainder:
            aliases.add(_normalize(remainder))
    return aliases


def _node_aliases(row: Any) -> set[str]:
    """Every name that identifies one node exactly.

    A node's full path is one of them. Chapters used to be matched on their
    title alone, which broke as soon as a chapter had an ancestor: the turn
    executor renders a resolved scope back into "Summarize <display path>."
    and re-resolves it, and for a chapter under a Part that path stopped
    matching anything - silently downgrading a chapter summary to a retrieval
    answer.
    """

    aliases = {_normalize(row["title"]), _normalize(row["path_text"])}
    if row["node_type"] == CHAPTER:
        aliases |= _chapter_aliases(row["title"])
    return aliases


def _reference_chapter_number(reference: object) -> str | None:
    normalized = _normalize(reference)
    if normalized.isdigit():
        return normalized
    match = re.fullmatch(r"chapter\s+(\d+)", normalized)
    return match.group(1) if match else None


def _title_chapter_number(title: str) -> str | None:
    """Read the chapter number a title declares, with or without the word.

    Requiring the literal word left one book's thirteen chapters ("1
    Introduction", "2 Statistical Learning") unable to answer "chapter 2" even
    though they were correctly typed as chapters. This is safe to loosen only
    because the caller filters to nodes already typed `chapter`, which are now
    exactly the consecutively numbered run.
    """

    number = chapter_number(title)
    return str(number) if number is not None else None


def resolve_chapter(
    connection: Connection,
    reference: str | int,
    *,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
) -> ResolvedScope:
    """Resolve a chapter number or title, returning its complete subtree.

    With several books in scope a bare "chapter 3" matches once per book and
    raises `AmbiguousScopeError`, which the turn analyser turns into a
    clarifying question rather than a guess.
    """

    owner = parse_owner_id(owner_id)
    _book_rows(connection, owner_id=owner, book_id=book_id, book_ids=book_ids)
    scope = book_scope(book_id, book_ids)
    parameters: tuple[object, ...] = (owner,) if scope is None else (owner, scope)
    predicate = "" if scope is None else "AND nodes.book_id = any(%s)"
    rows = connection.execute(
        f"""
        SELECT nodes.*, books.title AS book_title
        FROM nodes
        JOIN books ON books.id = nodes.book_id AND books.owner_id = nodes.owner_id
        WHERE nodes.owner_id = %s AND nodes.node_type = any(%s) {predicate}
        ORDER BY nodes.book_id, nodes.toc_index
        """,
        (owner, list(TOP_LEVEL_ROLES), *parameters[1:]),
    ).fetchall()

    number = _reference_chapter_number(reference)
    if number is not None:
        # A number addresses chapters only. A preface or an appendix may open
        # with a digit without being chapter 1.
        matches = [
            row
            for row in rows
            if row["node_type"] == CHAPTER
            and _title_chapter_number(row["title"]) == number
        ]
    else:
        target = _normalize(reference)
        if not target:
            raise ScopeNotFoundError("chapter", reference)
        exact = [row for row in rows if target in _node_aliases(row)]
        matches = exact or [
            row
            for row in rows
            if any(target in alias for alias in _node_aliases(row))
        ]
    if not matches:
        raise ScopeNotFoundError("chapter", reference)
    if len(matches) > 1:
        raise AmbiguousScopeError(
            "chapter",
            reference,
            tuple(_candidate(row) for row in matches),
        )
    return _resolved_node(connection, matches[0], owner_id=owner, kind="chapter")


def resolve_section(
    connection: Connection,
    reference: str,
    *,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
    chapter: str | int | None = None,
) -> ResolvedScope:
    """Resolve one non-chapter TOC node, optionally within a chapter."""

    owner = parse_owner_id(owner_id)
    if chapter is not None:
        chapter_scope = resolve_chapter(
            connection,
            chapter,
            owner_id=owner,
            book_id=book_id,
            book_ids=book_ids,
        )
        allowed_ids = set(chapter_scope.node_ids[1:])
        selected_book_id = chapter_scope.book_id
    else:
        selected_book_id = resolve_book(
            connection,
            owner_id=owner,
            book_id=book_id,
            book_ids=book_ids,
        ).book_id
        allowed_ids = None

    rows = connection.execute(
        """
        SELECT nodes.*, books.title AS book_title
        FROM nodes
        JOIN books ON books.id = nodes.book_id AND books.owner_id = nodes.owner_id
        WHERE nodes.book_id = %s AND nodes.owner_id = %s
          AND nodes.node_type != 'chapter'
        ORDER BY nodes.toc_index
        """,
        (selected_book_id, owner),
    ).fetchall()
    if allowed_ids is not None:
        rows = [row for row in rows if row["id"] in allowed_ids]

    target = _normalize(reference)
    if not target:
        raise ScopeNotFoundError("section", reference)
    exact = [
        row
        for row in rows
        if target in {_normalize(row["title"]), _normalize(row["path_text"])}
    ]
    matches = exact or [
        row
        for row in rows
        if target in _normalize(row["title"]) or target in _normalize(row["path_text"])
    ]
    if not matches:
        raise ScopeNotFoundError("section", reference)
    if len(matches) > 1:
        raise AmbiguousScopeError(
            "section",
            reference,
            tuple(_candidate(row) for row in matches),
        )
    return _resolved_node(connection, matches[0], owner_id=owner, kind="section")


def resolve_named_scope(
    connection: Connection,
    reference: str,
    *,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
) -> ResolvedScope:
    """Resolve an exact chapter/section title, then a unique partial title."""

    owner = parse_owner_id(owner_id)
    selected_book_id = resolve_book(
        connection,
        owner_id=owner,
        book_id=book_id,
        book_ids=book_ids,
    ).book_id
    target = _normalize(reference)
    if not target:
        raise ScopeNotFoundError("scope", reference)
    rows = connection.execute(
        """
        SELECT nodes.*, books.title AS book_title
        FROM nodes
        JOIN books ON books.id = nodes.book_id AND books.owner_id = nodes.owner_id
        WHERE nodes.book_id = %s AND nodes.owner_id = %s
          AND nodes.node_type = any(%s)
        ORDER BY nodes.toc_index
        """,
        (selected_book_id, owner, list(SEARCHABLE_ROLES)),
    ).fetchall()

    exact = [row for row in rows if target in _node_aliases(row)]
    matches = exact or [row for row in rows if target in _normalize(row["title"])]
    if not matches:
        raise ScopeNotFoundError("scope", reference)
    if len(matches) > 1:
        raise AmbiguousScopeError(
            "scope",
            reference,
            tuple(_candidate(row) for row in matches),
        )
    row = matches[0]
    kind = "chapter" if row["node_type"] == "chapter" else "section"
    return _resolved_node(connection, row, owner_id=owner, kind=kind)
