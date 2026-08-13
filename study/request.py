"""Parse explicit natural-language study requests into deterministic scopes."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg import Connection

from .scope import (
    ResolvedScope,
    resolve_book,
    resolve_chapter,
    resolve_named_scope,
    resolve_section,
)

StudyIntent = Literal["summarize", "list_chapters", "list_sections"]
RequestedScopeKind = Literal["book", "chapter", "section", "named"]


@dataclass(frozen=True)
class StudyRequest:
    """A parsed operation with an explicit scope reference."""

    intent: StudyIntent
    scope_kind: RequestedScopeKind
    scope_reference: str
    chapter_reference: str | None = None
    # "summarize chapter 1 of ddia" names the book inline. Without it the
    # chapter reference is matched across every book in scope, and "chapter 1"
    # exists in all of them.
    book_reference: str | None = None


class UnsupportedStudyRequestError(ValueError):
    """The deterministic parser does not understand the request."""


LIST_SECTIONS = (
    re.compile(
        r"^(?:list|show)(?:\s+me)?\s+(?:all\s+)?(?:the\s+)?sections\s+"
        r"(?:in|of|under)\s+(?:chapter\s+)?(.+?)\s*[?.]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+(?:are\s+)?(?:all\s+)?(?:the\s+)?sections"
        r"(?:\s+are)?\s+(?:present\s+)?(?:in|of|under)\s+"
        r"(?:chapter\s+)?(.+?)\s*[?.]?$",
        re.IGNORECASE,
    ),
)
LIST_CHAPTERS = (
    re.compile(
        r"^(?:list|show)(?:\s+me)?\s+(?:all\s+)?(?:the\s+)?chapters"
        r"(?:\s+(?:in|of|from)\s+(?P<book_reference>.+?))?\s*[?.]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+chapters\s+does\s+(?P<book_reference>.+?)\s+"
        r"(?:have|contain)\s*[?.]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+(?:are\s+)?(?:all\s+)?(?:the\s+)?chapters"
        r"(?:\s+are)?(?:\s+(?:present|included|listed))?\s+"
        r"(?:in|of|from)\s+(?P<book_reference>.+?)\s*[?.]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:give|show)\s+me\s+(?:the\s+)?(?:chapter\s+list|"
        r"table\s+of\s+contents)(?:\s+(?:for|of|from)\s+"
        r"(?P<book_reference>.+?))?\s*[?.]?$",
        re.IGNORECASE,
    ),
)
WHOLE_DOCUMENT_SUMMARY = re.compile(
    r"^(?:explain|summari[sz]e|review)\s+"
    r"(?:all\s+of\s+)?"
    r"(?:this|the(?:\s+(?:selected|current|whole))?|selected|current)\s+"
    r"(?:pdf|paper|document|book)\s*[?.]?$",
    re.IGNORECASE,
)
SUMMARIZE_SECTION = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?section\s+(.+?)"
    r"(?:\s+in\s+chapter\s+(.+?))?\s*[?.]?$",
    re.IGNORECASE,
)
# Only a numbered chapter may carry an inline book reference. A titled chapter
# ("summarize chapter Storage and Retrieval") can contain "of" itself, and
# splitting on it would cut the title in half.
SUMMARIZE_CHAPTER_IN_BOOK = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?chapter\s+(?P<chapter>\d+)\s+"
    r"(?:of|in|from)\s+(?P<book_reference>.+?)\s*[?.]?$",
    re.IGNORECASE,
)
LIST_SECTIONS_IN_BOOK = re.compile(
    r"^(?:list|show)(?:\s+me)?\s+(?:all\s+)?(?:the\s+)?sections\s+"
    r"(?:in|of|under)\s+chapter\s+(?P<chapter>\d+)\s+"
    r"(?:of|in|from)\s+(?P<book_reference>.+?)\s*[?.]?$",
    re.IGNORECASE,
)
SUMMARIZE_CHAPTER = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?chapter\s+(.+?)\s*[?.]?$",
    re.IGNORECASE,
)
INTERVIEW_REVIEW_CHAPTER = (
    re.compile(
        r"^(?:turn|convert)\s+(?:the\s+)?(.+?)\s+chapter\s+into\s+"
        r"(?:an?\s+)?interview(?:[- ](?:prep(?:aration)?|review))?.*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:prepare|review)\s+(?:the\s+)?(.+?)\s+chapter\s+"
        r"(?:for|as)\s+(?:an?\s+)?interview.*$",
        re.IGNORECASE,
    ),
)
SUMMARIZE_NAMED = re.compile(
    r"^summari[sz]e\s+(?:the\s+)?(.+?)\s*[?.]?$",
    re.IGNORECASE,
)


def _clean_reference(value: str) -> str:
    return value.strip().strip("\"'“”‘’").strip()


def _clean_book_reference(value: str | None) -> str:
    """Turn conversational book labels and UI mentions into a title fragment."""

    if value is None:
        return ""
    reference = value.strip()
    mention = re.fullmatch(r"@\[(.+)]", reference)
    if mention:
        # UI mentions carry the canonical display title. Preserve it exactly:
        # unlike conversational phrasing, a trailing "Book" can be part of
        # the actual title (for example, "Sample Book").
        return _clean_reference(mention.group(1))
    reference = _clean_reference(reference)
    if re.fullmatch(
        r"(?:this|the|selected|current)(?:\s+(?:selected|current))?\s+book",
        reference,
        re.IGNORECASE,
    ):
        return ""
    # Readers naturally say "the <title> book". Both words are conversational
    # wrappers rather than reliable parts of the stored title. Partial title
    # matching in resolve_book still handles books whose real title ends in
    # "Book".
    reference = re.sub(r"^the\s+", "", reference, flags=re.IGNORECASE)
    reference = re.sub(r"\s+book$", "", reference, flags=re.IGNORECASE)
    return _clean_reference(reference)


def parse_study_request(query: str) -> StudyRequest:
    """Parse the supported explicit query forms without a model call."""

    query = " ".join(query.split())
    if WHOLE_DOCUMENT_SUMMARY.fullmatch(query):
        return StudyRequest(
            intent="summarize",
            scope_kind="book",
            # The selected document id is the reference. Keeping this empty
            # avoids pretending that conversational words such as "this PDF"
            # are part of its stored title.
            scope_reference="",
        )
    for pattern in LIST_CHAPTERS:
        match = pattern.fullmatch(query)
        if match:
            return StudyRequest(
                intent="list_chapters",
                scope_kind="book",
                scope_reference=_clean_book_reference(
                    match.groupdict().get("book_reference")
                ),
            )

    match = LIST_SECTIONS_IN_BOOK.fullmatch(query)
    if match:
        return StudyRequest(
            intent="list_sections",
            scope_kind="chapter",
            scope_reference=match.group("chapter"),
            book_reference=_clean_book_reference(match.group("book_reference")),
        )

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

    for pattern in INTERVIEW_REVIEW_CHAPTER:
        match = pattern.fullmatch(query)
        if match:
            return StudyRequest(
                intent="summarize",
                scope_kind="chapter",
                scope_reference=_clean_reference(match.group(1)),
            )

    match = SUMMARIZE_CHAPTER_IN_BOOK.fullmatch(query)
    if match:
        return StudyRequest(
            intent="summarize",
            scope_kind="chapter",
            scope_reference=match.group("chapter"),
            book_reference=_clean_book_reference(match.group("book_reference")),
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
        "'summarize TITLE', 'list chapters', and "
        "'list sections in/under chapter N or TITLE', or "
        "'explain this PDF'"
    )


def resolve_study_request(
    connection: Connection,
    request: StudyRequest,
    *,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
) -> ResolvedScope:
    """Map a parsed request to one canonical book, chapter, or section."""

    if request.book_reference:
        # Naming the book inline narrows everything that follows to it, so
        # "chapter 1 of ddia" cannot collide with chapter 1 of four other
        # books the reader also has open.
        book_ids = [
            resolve_book(
                connection,
                request.book_reference,
                owner_id=owner_id,
                book_id=book_id,
                book_ids=book_ids,
            ).book_id
        ]
        book_id = None

    if request.scope_kind == "book":
        return resolve_book(
            connection,
            request.scope_reference or None,
            owner_id=owner_id,
            book_id=book_id,
            book_ids=book_ids,
        )
    if request.scope_kind == "chapter":
        return resolve_chapter(
            connection,
            request.scope_reference,
            owner_id=owner_id,
            book_id=book_id,
            book_ids=book_ids,
        )
    if request.scope_kind == "section":
        return resolve_section(
            connection,
            request.scope_reference,
            owner_id=owner_id,
            book_id=book_id,
            book_ids=book_ids,
            chapter=request.chapter_reference,
        )
    return resolve_named_scope(
        connection,
        request.scope_reference,
        owner_id=owner_id,
        book_id=book_id,
        book_ids=book_ids,
    )
