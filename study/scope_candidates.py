"""Deterministic canonical scope candidates for conversational analysis."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import sqlite3

from storage.sqlite import connect_readonly

from .contracts import ConversationState, ScopeCandidate


NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
CHAPTER_REFERENCE = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)
CHAPTER_TITLE = re.compile(r"^\s*chapter\s+(\d+)\b", re.IGNORECASE)
ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
STOPWORDS = {
    "a",
    "about",
    "actually",
    "an",
    "and",
    "approach",
    "approaches",
    "are",
    "be",
    "been",
    "being",
    "book",
    "by",
    "can",
    "chapter",
    "compare",
    "could",
    "cover",
    "covers",
    "did",
    "do",
    "does",
    "explain",
    "first",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "its",
    "list",
    "of",
    "on",
    "one",
    "or",
    "please",
    "second",
    "section",
    "should",
    "subsection",
    "summarize",
    "summarise",
    "that",
    "the",
    "their",
    "them",
    "then",
    "these",
    "they",
    "third",
    "this",
    "those",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
}


@dataclass
class _RankedNode:
    row: sqlite3.Row
    score: int
    reasons: list[str] = field(default_factory=list)


def _normalize(value: str) -> str:
    return " ".join(NON_WORD.sub(" ", value.casefold()).split())


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalize(value).split()
        if token not in STOPWORDS
    }


def _aliases(title: str) -> set[str]:
    aliases = {_normalize(title)}
    chapter = CHAPTER_TITLE.match(title)
    if chapter:
        aliases.add(f"chapter {chapter.group(1)}")
        remainder = title[chapter.end() :].lstrip(" .:—–-")
        if remainder:
            aliases.add(_normalize(remainder))
    for separator in (":", "—", "–"):
        if separator in title:
            prefix = _normalize(title.split(separator, 1)[0])
            if prefix:
                aliases.add(prefix)
    return {alias for alias in aliases if alias}


def _phrase_present(phrase: str, text: str) -> bool:
    return f" {phrase} " in f" {text} "


def _eligible_short_alias(alias: str, acronyms: set[str]) -> bool:
    tokens = alias.split()
    return (
        len(tokens) > 1
        or len(alias) >= 5
        or alias in acronyms
    )


def _rows(
    connection: sqlite3.Connection,
    *,
    book_id: int | None,
) -> list[sqlite3.Row]:
    parameters: tuple[object, ...] = ()
    predicate = ""
    if book_id is not None:
        predicate = "AND nodes.book_id = ?"
        parameters = (book_id,)
    return connection.execute(
        f"""
        SELECT
            nodes.id,
            nodes.book_id,
            nodes.parent_id,
            nodes.toc_index,
            nodes.node_type,
            nodes.title,
            nodes.path_text,
            nodes.start_page,
            nodes.end_page
        FROM nodes
        WHERE nodes.node_type IN (
            'chapter', 'section', 'subsection', 'nested_section'
        )
        {predicate}
        ORDER BY nodes.book_id, nodes.toc_index
        """,
        parameters,
    ).fetchall()


def _chapter_number(title: str) -> str | None:
    match = CHAPTER_TITLE.match(title)
    return match.group(1) if match else None


def _kind(row: sqlite3.Row) -> str:
    return "chapter" if row["node_type"] == "chapter" else "section"


def _add(
    ranked: dict[int, _RankedNode],
    row: sqlite3.Row,
    *,
    score: int,
    reason: str,
) -> None:
    existing = ranked.get(row["id"])
    if existing is None:
        ranked[row["id"]] = _RankedNode(
            row=row,
            score=score,
            reasons=[reason],
        )
        return
    existing.score = max(existing.score, score)
    if reason not in existing.reasons:
        existing.reasons.append(reason)


def _chapter_ancestor(
    row: sqlite3.Row,
    nodes: dict[int, sqlite3.Row],
) -> sqlite3.Row | None:
    current = row
    visited: set[int] = set()
    while current["parent_id"] is not None:
        if current["id"] in visited:
            return None
        visited.add(current["id"])
        parent = nodes.get(current["parent_id"])
        if parent is None:
            return None
        if parent["node_type"] == "chapter":
            return parent
        current = parent
    return None


def _scope_bounds(
    rows: list[sqlite3.Row],
    nodes: dict[int, sqlite3.Row],
) -> dict[int, tuple[int, int]]:
    bounds = {
        row["id"]: [row["start_page"], row["end_page"]]
        for row in rows
    }
    for row in rows:
        parent_id = row["parent_id"]
        visited: set[int] = set()
        while parent_id in nodes and parent_id not in visited:
            visited.add(parent_id)
            parent_bounds = bounds[parent_id]
            parent_bounds[0] = min(parent_bounds[0], row["start_page"])
            parent_bounds[1] = max(parent_bounds[1], row["end_page"])
            parent_id = nodes[parent_id]["parent_id"]
    return {
        node_id: (values[0], values[1])
        for node_id, values in bounds.items()
    }


def _candidate(
    item: _RankedNode,
    bounds: dict[int, tuple[int, int]],
) -> ScopeCandidate:
    row = item.row
    start_page, end_page = bounds[row["id"]]
    return ScopeCandidate(
        book_id=row["book_id"],
        node_id=row["id"],
        kind=_kind(row),
        title=row["title"],
        display_path=row["path_text"],
        start_page=start_page,
        end_page=end_page,
        match_reason="; ".join(item.reasons),
    )


def find_scope_candidates(
    question: str,
    state: ConversationState,
    source_path: str | Path = "data/books.sqlite3",
    *,
    limit: int = 8,
) -> list[ScopeCandidate]:
    """Return a small, ranked set of canonical chapter/section candidates."""

    if limit <= 0:
        raise ValueError("limit must be positive")

    normalized_question = _normalize(question)
    question_tokens = _tokens(question)
    question_acronyms = {
        token.casefold() for token in ACRONYM.findall(question)
    }
    explicit_chapters = set(CHAPTER_REFERENCE.findall(question))
    recent_text = _normalize(
        " ".join(
            message.content[:1500]
            for message in state.recent_messages(turns=3)
        )
    )
    recent_chapters = set(CHAPTER_REFERENCE.findall(recent_text))

    with connect_readonly(source_path) as connection:
        rows = _rows(connection, book_id=state.book_id)
    nodes = {row["id"]: row for row in rows}
    bounds = _scope_bounds(rows, nodes)
    ranked: dict[int, _RankedNode] = {}

    for row in rows:
        chapter_number = _chapter_number(row["title"])
        if chapter_number in explicit_chapters:
            _add(
                ranked,
                row,
                score=1200,
                reason=f"explicit Chapter {chapter_number} reference",
            )

        aliases = _aliases(row["title"])
        exact_aliases = [
            alias
            for alias in aliases
            if alias == normalized_question
        ]
        phrase_aliases = [
            alias
            for alias in aliases
            if _phrase_present(alias, normalized_question)
            and _eligible_short_alias(alias, question_acronyms)
        ]
        if exact_aliases:
            _add(
                ranked,
                row,
                score=1000,
                reason=f"exact title match: {max(exact_aliases, key=len)}",
            )
        elif phrase_aliases:
            best_phrase = max(
                phrase_aliases,
                key=lambda alias: (len(alias.split()), len(alias)),
            )
            _add(
                ranked,
                row,
                score=900 + 10 * len(best_phrase.split()) + len(best_phrase),
                reason=f"title phrase: {best_phrase}",
            )
        else:
            title_tokens = _tokens(row["title"])
            overlap = title_tokens & question_tokens
            long_overlap = {
                token
                for token in overlap
                if len(token) >= 7 or token in question_acronyms
            }
            if len(overlap) >= 2 or long_overlap:
                coverage = len(overlap) / max(len(title_tokens), 1)
                score = 500 + int(250 * coverage) + 20 * len(overlap)
                _add(
                    ranked,
                    row,
                    score=score,
                    reason="title words: " + ", ".join(sorted(overlap)),
                )

        if chapter_number in recent_chapters:
            _add(
                ranked,
                row,
                score=400,
                reason=f"recent Chapter {chapter_number} mention",
            )
        recent_aliases = [
            alias
            for alias in aliases
            if _phrase_present(alias, recent_text)
            and _eligible_short_alias(alias, set())
        ]
        if recent_aliases:
            _add(
                ranked,
                row,
                score=375,
                reason=(
                    "recent title mention: "
                    + max(recent_aliases, key=len)
                ),
            )

    active_node_id = (
        state.active_scope.node_id if state.active_scope is not None else None
    )
    if active_node_id in nodes:
        _add(
            ranked,
            nodes[active_node_id],
            score=700,
            reason="active conversation scope",
        )

    for evidence in state.previous_evidence:
        if evidence.node_id in nodes:
            _add(
                ranked,
                nodes[evidence.node_id],
                score=550,
                reason="previous-turn evidence",
            )

    for item in list(ranked.values()):
        if item.row["node_type"] == "chapter":
            continue
        chapter = _chapter_ancestor(item.row, nodes)
        if chapter is not None:
            _add(
                ranked,
                chapter,
                score=max(300, min(item.score - 100, 700)),
                reason="parent chapter of matched scope",
            )

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            -item.score,
            item.row["book_id"],
            item.row["toc_index"],
            item.row["id"],
        ),
    )
    selected = ordered[:limit]
    if (
        limit > 1
        and active_node_id in ranked
        and active_node_id not in {item.row["id"] for item in selected}
    ):
        selected[-1] = ranked[active_node_id]
        selected.sort(
            key=lambda item: (
                -item.score,
                item.row["book_id"],
                item.row["toc_index"],
                item.row["id"],
            )
        )
    return [_candidate(item, bounds) for item in selected]
