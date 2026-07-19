"""Resolve book hierarchy references without retrieval or model calls."""

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal


ScopeKind = Literal["book", "chapter", "section"]
ResolutionKind = Literal["book", "chapter", "section", "scope"]
CHAPTER_NUMBER = re.compile(r"^\s*chapter\s+(\d+)\b", re.IGNORECASE)
NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


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
class ScopeCandidate:
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
        candidates: tuple[ScopeCandidate, ...],
    ) -> None:
        self.kind = kind
        self.reference = reference
        self.candidates = candidates
        choices = "; ".join(
            f"book {candidate.book_id}, {candidate.path_text} "
            f"(PDF pp. {candidate.start_page}–{candidate.end_page})"
            for candidate in candidates
        )
        super().__init__(
            f"{kind} reference {reference!r} is ambiguous: {choices}"
        )


def _normalize(value: object) -> str:
    return " ".join(NON_WORD.sub(" ", str(value).casefold()).split())


def _node(row: sqlite3.Row) -> ScopeNode:
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


def _candidate(row: sqlite3.Row) -> ScopeCandidate:
    return ScopeCandidate(
        book_id=row["book_id"],
        book_title=row["book_title"],
        node_id=row["id"],
        path_text=row["path_text"],
        start_page=row["start_page"],
        end_page=row["end_page"],
    )


def _book_rows(
    connection: sqlite3.Connection,
    *,
    book_id: int | None,
) -> list[sqlite3.Row]:
    if book_id is None:
        rows = connection.execute(
            "SELECT * FROM books ORDER BY id"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM books WHERE id = ?",
            (book_id,),
        ).fetchall()
    if not rows:
        raise ScopeNotFoundError("book", book_id)
    return rows


def _book_candidate(row: sqlite3.Row) -> ScopeCandidate:
    return ScopeCandidate(
        book_id=row["id"],
        book_title=row["title"],
        node_id=None,
        path_text=row["title"],
        start_page=1,
        end_page=row["page_count"] or 1,
    )


def _all_book_nodes(
    connection: sqlite3.Connection,
    book_id: int,
) -> tuple[ScopeNode, ...]:
    rows = connection.execute(
        "SELECT * FROM nodes WHERE book_id = ? ORDER BY toc_index",
        (book_id,),
    ).fetchall()
    return tuple(_node(row) for row in rows)


def _subtree_nodes(
    connection: sqlite3.Connection,
    node_id: int,
) -> tuple[ScopeNode, ...]:
    rows = connection.execute(
        """
        WITH RECURSIVE subtree AS (
            SELECT *
            FROM nodes
            WHERE id = ?

            UNION ALL

            SELECT child.*
            FROM nodes AS child
            JOIN subtree AS parent ON child.parent_id = parent.id
        )
        SELECT *
        FROM subtree
        ORDER BY toc_index
        """,
        (node_id,),
    ).fetchall()
    return tuple(_node(row) for row in rows)


def _resolved_node(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    kind: Literal["chapter", "section"],
) -> ResolvedScope:
    nodes = _subtree_nodes(connection, row["id"])
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
    connection: sqlite3.Connection,
    reference: str | None = None,
    *,
    book_id: int | None = None,
) -> ResolvedScope:
    """Resolve one book by ID or deterministic title matching."""

    rows = _book_rows(connection, book_id=book_id)
    if reference is not None:
        target = _normalize(reference)
        if not target:
            raise ScopeNotFoundError("book", reference)
        exact = [row for row in rows if _normalize(row["title"]) == target]
        rows = exact or [
            row for row in rows if target in _normalize(row["title"])
        ]
        if not rows:
            raise ScopeNotFoundError("book", reference)
    if len(rows) > 1:
        raise AmbiguousScopeError(
            "book",
            reference,
            tuple(_book_candidate(row) for row in rows),
        )

    book = rows[0]
    nodes = _all_book_nodes(connection, book["id"])
    return ResolvedScope(
        kind="book",
        book_id=book["id"],
        book_title=book["title"],
        root_node_id=None,
        display_path=book["title"],
        start_page=1,
        end_page=book["page_count"] or max(
            (node.end_page for node in nodes),
            default=1,
        ),
        nodes=nodes,
    )


def list_chapters(
    connection: sqlite3.Connection,
    *,
    book_id: int,
) -> tuple[ScopeNode, ...]:
    """Return canonical chapters in table-of-contents order."""

    _book_rows(connection, book_id=book_id)
    rows = connection.execute(
        """
        SELECT *
        FROM nodes
        WHERE book_id = ? AND node_type = 'chapter'
        ORDER BY toc_index
        """,
        (book_id,),
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


def _reference_chapter_number(reference: object) -> str | None:
    normalized = _normalize(reference)
    if normalized.isdigit():
        return normalized
    match = re.fullmatch(r"chapter\s+(\d+)", normalized)
    return match.group(1) if match else None


def _title_chapter_number(title: str) -> str | None:
    match = re.match(r"chapter\s+(\d+)\b", _normalize(title))
    return match.group(1) if match else None


def resolve_chapter(
    connection: sqlite3.Connection,
    reference: str | int,
    *,
    book_id: int | None = None,
) -> ResolvedScope:
    """Resolve a chapter number or title, returning its complete subtree."""

    _book_rows(connection, book_id=book_id)
    parameters: tuple[object, ...] = () if book_id is None else (book_id,)
    predicate = "" if book_id is None else "AND nodes.book_id = ?"
    rows = connection.execute(
        f"""
        SELECT nodes.*, books.title AS book_title
        FROM nodes
        JOIN books ON books.id = nodes.book_id
        WHERE nodes.node_type = 'chapter' {predicate}
        ORDER BY nodes.book_id, nodes.toc_index
        """,
        parameters,
    ).fetchall()

    number = _reference_chapter_number(reference)
    if number is not None:
        matches = [
            row
            for row in rows
            if _title_chapter_number(row["title"]) == number
        ]
    else:
        target = _normalize(reference)
        if not target:
            raise ScopeNotFoundError("chapter", reference)
        exact = [
            row for row in rows if target in _chapter_aliases(row["title"])
        ]
        matches = exact or [
            row
            for row in rows
            if any(target in alias for alias in _chapter_aliases(row["title"]))
        ]
    if not matches:
        raise ScopeNotFoundError("chapter", reference)
    if len(matches) > 1:
        raise AmbiguousScopeError(
            "chapter",
            reference,
            tuple(_candidate(row) for row in matches),
        )
    return _resolved_node(connection, matches[0], kind="chapter")


def resolve_section(
    connection: sqlite3.Connection,
    reference: str,
    *,
    book_id: int | None = None,
    chapter: str | int | None = None,
) -> ResolvedScope:
    """Resolve one non-chapter TOC node, optionally within a chapter."""

    if chapter is not None:
        chapter_scope = resolve_chapter(
            connection,
            chapter,
            book_id=book_id,
        )
        allowed_ids = set(chapter_scope.node_ids[1:])
        selected_book_id = chapter_scope.book_id
    else:
        selected_book_id = resolve_book(
            connection,
            book_id=book_id,
        ).book_id
        allowed_ids = None

    rows = connection.execute(
        """
        SELECT nodes.*, books.title AS book_title
        FROM nodes
        JOIN books ON books.id = nodes.book_id
        WHERE nodes.book_id = ? AND nodes.node_type != 'chapter'
        ORDER BY nodes.toc_index
        """,
        (selected_book_id,),
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
        if target in _normalize(row["title"])
        or target in _normalize(row["path_text"])
    ]
    if not matches:
        raise ScopeNotFoundError("section", reference)
    if len(matches) > 1:
        raise AmbiguousScopeError(
            "section",
            reference,
            tuple(_candidate(row) for row in matches),
        )
    return _resolved_node(connection, matches[0], kind="section")


def resolve_named_scope(
    connection: sqlite3.Connection,
    reference: str,
    *,
    book_id: int | None = None,
) -> ResolvedScope:
    """Resolve an exact chapter/section title, then a unique partial title."""

    selected_book_id = resolve_book(
        connection,
        book_id=book_id,
    ).book_id
    target = _normalize(reference)
    if not target:
        raise ScopeNotFoundError("scope", reference)
    rows = connection.execute(
        """
        SELECT nodes.*, books.title AS book_title
        FROM nodes
        JOIN books ON books.id = nodes.book_id
        WHERE nodes.book_id = ?
          AND nodes.node_type IN (
              'chapter', 'section', 'subsection', 'nested_section'
          )
        ORDER BY nodes.toc_index
        """,
        (selected_book_id,),
    ).fetchall()

    exact = [
        row
        for row in rows
        if (
            target in _chapter_aliases(row["title"])
            if row["node_type"] == "chapter"
            else target
            in {_normalize(row["title"]), _normalize(row["path_text"])}
        )
    ]
    matches = exact or [
        row
        for row in rows
        if target in _normalize(row["title"])
    ]
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
    return _resolved_node(connection, row, kind=kind)
