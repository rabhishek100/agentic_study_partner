"""Small deterministic candidate set for the conversation control model."""

from pathlib import Path
import re
import sqlite3

from storage.sqlite import connect_readonly

from .contracts import ConversationState, ScopeCandidate


NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
CHAPTER = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)
CHAPTER_TITLE = re.compile(r"^\s*chapter\s+(\d+)\b", re.IGNORECASE)
STOPWORDS = {
    "a", "about", "an", "and", "approach", "are", "book", "by", "can",
    "chapter", "compare", "cover", "do", "does", "explain", "for", "from",
    "how", "in", "is", "it", "list", "of", "on", "one", "or", "please",
    "section", "summarize", "summarise", "that", "the", "them", "then",
    "this", "to", "was", "what", "when", "where", "which", "why", "with",
}


def _normalize(value):
    return " ".join(NON_WORD.sub(" ", str(value).casefold()).split())


def _tokens(value):
    return {
        token for token in _normalize(value).split() if token not in STOPWORDS
    }


def _aliases(title):
    aliases = {_normalize(title)}
    match = CHAPTER_TITLE.match(title)
    if match:
        aliases.add(f"chapter {match.group(1)}")
        remainder = title[match.end():].lstrip(" .:—–-")
        if remainder:
            aliases.add(_normalize(remainder))
    return aliases


def _rows(connection, book_id):
    predicate = "AND book_id = ?" if book_id is not None else ""
    parameters = (book_id,) if book_id is not None else ()
    return connection.execute(
        f"""
        SELECT id, book_id, parent_id, toc_index, node_type, title,
               path_text, start_page, end_page
        FROM nodes
        WHERE node_type IN ('chapter','section','subsection','nested_section')
        {predicate}
        ORDER BY book_id, toc_index
        """,
        parameters,
    ).fetchall()


def _bounds(rows):
    by_id = {row["id"]: row for row in rows}
    bounds = {
        row["id"]: [row["start_page"], row["end_page"]] for row in rows
    }
    for row in rows:
        parent = row["parent_id"]
        visited = set()
        while parent in by_id and parent not in visited:
            visited.add(parent)
            bounds[parent][0] = min(bounds[parent][0], row["start_page"])
            bounds[parent][1] = max(bounds[parent][1], row["end_page"])
            parent = by_id[parent]["parent_id"]
    return by_id, bounds


def _parent_chapter(row, by_id):
    parent = row["parent_id"]
    while parent in by_id:
        candidate = by_id[parent]
        if candidate["node_type"] == "chapter":
            return candidate
        parent = candidate["parent_id"]
    return None


def _add(ranked, row, score, reason):
    current = ranked.setdefault(
        row["id"], {"row": row, "score": score, "reasons": []}
    )
    current["score"] = max(current["score"], score)
    if reason not in current["reasons"]:
        current["reasons"].append(reason)


def find_scope_candidates(
    question: str,
    state: ConversationState,
    source_path: str | Path = "data/books.sqlite3",
    *,
    limit: int = 8,
) -> list[ScopeCandidate]:
    """Rank named, recent, active, and evidence-backed canonical scopes."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    normalized = _normalize(question)
    question_tokens = _tokens(question)
    chapter_numbers = set(CHAPTER.findall(question))
    recent = _normalize(
        " ".join(message.content[:1500] for message in state.recent_messages())
    )
    recent_chapters = set(CHAPTER.findall(recent))

    with connect_readonly(source_path) as connection:
        rows = _rows(connection, state.book_id)
    by_id, bounds = _bounds(rows)
    ranked = {}

    for row in rows:
        match = CHAPTER_TITLE.match(row["title"])
        number = match.group(1) if match else None
        if number in chapter_numbers:
            _add(ranked, row, 1200, f"explicit Chapter {number} reference")

        aliases = _aliases(row["title"])
        phrases = [
            alias
            for alias in aliases
            if f" {alias} " in f" {normalized} "
            and (len(alias.split()) > 1 or len(alias) >= 5)
        ]
        if normalized in aliases:
            _add(ranked, row, 1000, f"exact title match: {normalized}")
        elif phrases:
            phrase = max(phrases, key=len)
            _add(ranked, row, 900 + len(phrase), f"title phrase: {phrase}")
        else:
            overlap = _tokens(row["title"]) & question_tokens
            if len(overlap) >= 2 or any(len(word) >= 7 for word in overlap):
                _add(
                    ranked,
                    row,
                    500 + 40 * len(overlap),
                    "title words: " + ", ".join(sorted(overlap)),
                )

        if number in recent_chapters:
            _add(ranked, row, 400, f"recent Chapter {number} mention")
        recent_alias = next(
            (
                alias
                for alias in aliases
                if len(alias) >= 5 and f" {alias} " in f" {recent} "
            ),
            None,
        )
        if recent_alias:
            _add(ranked, row, 375, f"recent title mention: {recent_alias}")

    active_id = state.active_scope.node_id if state.active_scope else None
    if active_id in by_id:
        _add(ranked, by_id[active_id], 700, "active conversation scope")
    for evidence in state.previous_evidence:
        if evidence.node_id in by_id:
            _add(ranked, by_id[evidence.node_id], 550, "previous-turn evidence")

    for item in list(ranked.values()):
        chapter = _parent_chapter(item["row"], by_id)
        if chapter:
            _add(
                ranked,
                chapter,
                max(300, min(item["score"] - 100, 700)),
                "parent chapter of matched scope",
            )

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            -item["score"],
            item["row"]["book_id"],
            item["row"]["toc_index"],
        ),
    )
    selected = ordered[:limit]
    if (
        selected
        and active_id in ranked
        and active_id not in {item["row"]["id"] for item in selected}
    ):
        selected[-1] = ranked[active_id]

    return [
        ScopeCandidate(
            book_id=item["row"]["book_id"],
            node_id=item["row"]["id"],
            kind=(
                "chapter"
                if item["row"]["node_type"] == "chapter"
                else "section"
            ),
            title=item["row"]["title"],
            display_path=item["row"]["path_text"],
            start_page=bounds[item["row"]["id"]][0],
            end_page=bounds[item["row"]["id"]][1],
            match_reason="; ".join(item["reasons"]),
        )
        for item in selected
    ]
