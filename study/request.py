"""Parse explicit natural-language study requests into deterministic scopes."""

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal

from .scope import (
    ResolvedScope,
    resolve_chapter,
    resolve_named_scope,
    resolve_section,
)


StudyIntent = Literal["summarize", "list_sections"]
RequestedScopeKind = Literal["chapter", "section", "named"]


@dataclass(frozen=True)
class StudyRequest:
    """A parsed operation with an explicit scope reference."""

    intent: StudyIntent
    scope_kind: RequestedScopeKind
    scope_reference: str
    chapter_reference: str | None = None


class UnsupportedStudyRequestError(ValueError):
    """The deterministic parser does not understand the request."""


LIST_SECTIONS = (
    re.compile(
        r"^list\s+(?:the\s+)?sections\s+(?:in|of)\s+chapter\s+(.+?)\s*[?.]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+sections\s+(?:are\s+)?"
        r"(?:present\s+)?in\s+chapter\s+(.+?)\s*[?.]?$",
        re.IGNORECASE,
    ),
)
SUMMARIZE_SECTION = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?section\s+(.+?)"
    r"(?:\s+in\s+chapter\s+(.+?))?\s*[?.]?$",
    re.IGNORECASE,
)
SUMMARIZE_CHAPTER = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?chapter\s+(.+?)\s*[?.]?$",
    re.IGNORECASE,
)
SUMMARIZE_NAMED = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?(.+?)\s*[?.]?$",
    re.IGNORECASE,
)


def _clean_reference(value: str) -> str:
    return value.strip().strip("\"'“”‘’").strip()


def parse_study_request(query: str) -> StudyRequest:
    """Parse the supported explicit query forms without a model call."""

    query = " ".join(query.split())
    for pattern in LIST_SECTIONS:
        match = pattern.fullmatch(query)
        if match:
            return StudyRequest(
                intent="list_sections",
                scope_kind="chapter",
                scope_reference=_clean_reference(match.group(1)),
            )

    match = SUMMARIZE_SECTION.fullmatch(query)
    if match:
        return StudyRequest(
            intent="summarize",
            scope_kind="section",
            scope_reference=_clean_reference(match.group(1)),
            chapter_reference=(
                _clean_reference(match.group(2)) if match.group(2) else None
            ),
        )

    match = SUMMARIZE_CHAPTER.fullmatch(query)
    if match:
        return StudyRequest(
            intent="summarize",
            scope_kind="chapter",
            scope_reference=_clean_reference(match.group(1)),
        )

    match = SUMMARIZE_NAMED.fullmatch(query)
    if match:
        return StudyRequest(
            intent="summarize",
            scope_kind="named",
            scope_reference=_clean_reference(match.group(1)),
        )

    raise UnsupportedStudyRequestError(
        "supported forms are: 'summarize chapter N', "
        "'summarize section TITLE in chapter N', "
        "'summarize TITLE', and 'list sections in chapter N'"
    )


def resolve_study_request(
    connection: sqlite3.Connection,
    request: StudyRequest,
    *,
    book_id: int | None = None,
) -> ResolvedScope:
    """Map a parsed request to one canonical chapter or section subtree."""

    if request.scope_kind == "chapter":
        return resolve_chapter(
            connection,
            request.scope_reference,
            book_id=book_id,
        )
    if request.scope_kind == "section":
        return resolve_section(
            connection,
            request.scope_reference,
            book_id=book_id,
            chapter=request.chapter_reference,
        )
    return resolve_named_scope(
        connection,
        request.scope_reference,
        book_id=book_id,
    )
