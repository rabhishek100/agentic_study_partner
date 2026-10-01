"""Extract questions printed in books and ground any missing answers.

Question extraction is deliberately separate from topic-card generation.  A
book question is source material, not a prompt-inspired card, and its answer
provenance must remain visible all the way to the review UI.
"""

from __future__ import annotations

import logging
import os
import re
from hashlib import sha256
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import fitz
import httpx
import tiktoken
from psycopg import Connection
from pydantic import BaseModel, Field

from observability import traced
from study.context import DEFAULT_ENCODING

from .contracts import AnswerSource, CardBack, DeckCard, DeckMetrics
from .generate import (
    DEFAULT_GENERATION_MODEL,
    DeckGenerationError,
    GeneratedDeck,
    model_name,
)
from .topics import GENERATION_BATCH_TOKENS, ScopeInventory, Topic
from .validate import (
    DROP_DUPLICATE,
    DROP_MALFORMED,
    DROP_OUT_OF_SCOPE,
    DROP_UNCITED,
    ValidationTally,
    normalized_front,
    parse_marker,
)

logger = logging.getLogger("study_partner.decks.extraction")

EXTRACTION_PROMPT_VERSION = "v5_additive_source_items"
ANSWER_EVIDENCE_TOKENS = GENERATION_BATCH_TOKENS
SEGMENT_OVERLAP_TOKENS = 160
MARKER_LINE = re.compile(r"(?m)(?=^\[N\d+:P\d+\]\s*$)")
MARKER_AT_START = re.compile(r"^\[N\d+:P\d+\]")
MARKER_ONLY_LINE = re.compile(r"(?m)^[ \t]*\[N\d+:P\d+\][ \t]*\n?")
NUMBERED_QUESTION = re.compile(r"(?m)^[ \t]*(?P<number>\d{1,3})[.)][ \t]+(?=\S)")
QUESTION_SECTION = re.compile(
    r"(?:^|::)\s*(?:\d+(?:\.\d+)*\s+)?(?:exercises?|problems?|"
    r"review questions?|study questions?|practice|self[- ]assessment|"
    r"check your understanding)\s*(?=::|$)",
    re.IGNORECASE,
)
EXPLICIT_ITEM = re.compile(
    r"(?im)^[ \t]*(?P<label>"
    r"(?:exercise|problem|question|practice|checkpoint)"
    r"(?:\s+\d+(?:\.\d+)*)?|"
    r"check your understanding|try it|your turn|"
    r"worked example(?:\s+\d+(?:\.\d+)*)?|"
    r"example\s+\d+(?:\.\d+)*"
    r")[.:)]?[ \t]+(?=\S)"
)
SOLUTION_LABEL = re.compile(
    r"(?im)^[ \t]*(?:solution|answer)(?:\s+\d+(?:\.\d+)*)?[.:)]?"
    r"[ \t]*(?=\S)"
)
STRUCTURED_EXAMPLE_TOPIC = re.compile(
    r"(?:^|::)\s*(?:\d+(?:\.\d+)*\s+)?(?:"
    r"(?:worked\s+)?example\b.*|[^:]*\bexample)\s*$",
    re.IGNORECASE,
)
STRUCTURED_EXAMPLE_EVIDENCE = re.compile(
    r"(?im)^(?:\d+(?:\.\d+)+\s+)?(?:worked\s+)?example:\s+\S"
)
SUBPART = re.compile(r"(?<!\w)\(([a-z]|[ivx]{1,4}|\d{1,2})\)")
INSUFFICIENT_ANSWER = re.compile(
    r"\b(?:insufficient evidence|evidence is insufficient|not enough evidence|"
    r"cannot (?:answer|determine)|"
    r"does not (?:include|provide|contain)|unable to (?:answer|determine))\b",
    re.IGNORECASE,
)
RUNNING_HEADER = re.compile(
    r"^(?:\d+\s+\d+\.\s+\D.+|"
    r"\d+(?:\.\d+)+\s+Exercises\s+\d+|"
    r"\d+\s+\d+(?:\.\d+)+\s+Exercises)$",
    re.IGNORECASE,
)
WORDS = re.compile(r"[\w'-]+", re.UNICODE)
STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "based",
        "before",
        "being",
        "between",
        "could",
        "describe",
        "does",
        "each",
        "explain",
        "from",
        "given",
        "have",
        "into",
        "most",
        "should",
        "that",
        "their",
        "then",
        "these",
        "they",
        "this",
        "using",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
    }
)

ProgressCallback = Callable[[int, int], None]


class ExtractedQuestion(BaseModel):
    """One explicit question copied or conservatively cleaned from the book."""

    question: str = Field(
        min_length=1,
        max_length=20_000,
        description="The exact or conservatively cleaned question printed in the book.",
    )
    printed_answer: str | None = Field(
        default=None,
        max_length=12_000,
        description=(
            "The answer or solution printed in the supplied excerpt, or null when the "
            "excerpt does not contain one. Never answer from model knowledge here."
        ),
    )
    citation_marker: str = Field(
        description="An exact marker from the supplied text where the question appears."
    )
    question_citation_markers: list[str] = Field(
        default_factory=list,
        max_length=24,
        description="Every supplied page marker spanned by the complete question.",
    )
    answer_citation_markers: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Exact supplied markers supporting a printed answer.",
    )
    difficulty: Literal["foundational", "intermediate", "advanced"] = "intermediate"
    source_label: str | None = Field(
        default=None,
        max_length=200,
        description="Stable source label such as Exercise 7, when one is printed.",
    )
    source_item_key: str | None = Field(default=None, max_length=100)
    item_kind: Literal["exercise", "worked_example"] = "exercise"
    placement: Literal["inline", "end_of_chapter"] = "inline"
    discovery_method: Literal[
        "numbered_section", "explicit_label", "model_fallback"
    ] = "model_fallback"


class ExtractedQuestionList(BaseModel):
    """Questions found in one bounded evidence batch."""

    questions: list[ExtractedQuestion] = Field(default_factory=list, max_length=40)


class RAGAnswerOutput(BaseModel):
    """An answer whose supporting markers were copied from retrieved evidence."""

    answer: str = Field(min_length=1, max_length=12_000)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    say_it_aloud: str = Field(min_length=1, max_length=400)
    citation_markers: list[str] = Field(min_length=1, max_length=12)
    answer_source: Literal["printed_in_book", "synthesized_from_book"] = Field(
        description=(
            "printed_in_book only when the evidence explicitly gives this answer or "
            "solution; synthesized_from_book when the answer is composed from explanatory "
            "chapter evidence"
        )
    )


@dataclass(frozen=True)
class QuestionScan:
    """The deterministic inventory built before any answer is generated."""

    questions: tuple[ExtractedQuestion, ...]
    evidence_topics: tuple[Topic, ...]
    used_numbered_boundaries: bool


def _openrouter_model(schema: type[BaseModel], *, temperature: float):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise DeckGenerationError(
            "OPENROUTER_API_KEY is required to extract questions from a book"
        )

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_DECK_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_GENERATION_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=temperature,
        extra_body={
            "usage": {"include": True},
            "reasoning": {
                "effort": os.getenv("OPENROUTER_GENERATION_REASONING", "none"),
                "exclude": True,
            },
        },
    )
    return model.with_structured_output(schema, method="json_schema")


def _question_extraction_model():
    return _openrouter_model(ExtractedQuestionList, temperature=0.1)


def _rag_answer_model():
    return _openrouter_model(RAGAnswerOutput, temperature=0.2)


def _token_count(text: str) -> int:
    return len(tiktoken.get_encoding(DEFAULT_ENCODING).encode(text))


def _split_oversized_segment(segment: str, *, token_budget: int) -> list[str]:
    """Split one unusually large canonical block while preserving its marker."""

    marker_match = MARKER_AT_START.match(segment.strip())
    if marker_match is None:
        return []
    marker = marker_match.group(0)
    body = segment.strip()[len(marker) :].strip()
    encoding = tiktoken.get_encoding(DEFAULT_ENCODING)
    body_tokens = encoding.encode(body)
    marker_cost = _token_count(marker) + 2
    window = max(1, token_budget - marker_cost)
    step = max(1, window - min(SEGMENT_OVERLAP_TOKENS, window // 4))
    return [
        f"{marker}\n{encoding.decode(body_tokens[start : start + window])}"
        for start in range(0, len(body_tokens), step)
        if body_tokens[start : start + window]
    ]


def evidence_segments(
    topics: Iterable[Topic], *, token_budget: int = GENERATION_BATCH_TOKENS
) -> tuple[str, ...]:
    """Return marker-preserving evidence pieces no larger than one model batch."""

    segments: list[str] = []
    for topic in topics:
        for raw in MARKER_LINE.split(topic.evidence_text):
            segment = raw.strip()
            if not segment or MARKER_AT_START.match(segment) is None:
                continue
            if _token_count(segment) <= token_budget:
                segments.append(segment)
            else:
                segments.extend(
                    _split_oversized_segment(segment, token_budget=token_budget)
                )
    return tuple(segments)


def evidence_batches(
    topics: Iterable[Topic], *, token_budget: int = GENERATION_BATCH_TOKENS
) -> tuple[str, ...]:
    """Pack canonical blocks into bounded extraction calls in source order."""

    batches: list[str] = []
    current: list[str] = []
    used = 0
    for segment in evidence_segments(topics, token_budget=token_budget):
        cost = _token_count(segment)
        if current and used + cost > token_budget:
            batches.append("\n\n".join(current))
            current, used = [], 0
        current.append(segment)
        used += cost
    if current:
        batches.append("\n\n".join(current))
    return tuple(batches)


def question_section_topics(topics: Iterable[Topic]) -> tuple[Topic, ...]:
    """Prefer explicit exercise/review sections over chapter-wide scanning.

    A path match intentionally includes descendants such as ``Exercises ::
    Applied``.  This is the deterministic guard that keeps rhetorical
    questions in narrative and lab prose out of a source-question deck.
    """

    return tuple(topic for topic in topics if QUESTION_SECTION.search(topic.label))


def structured_example_topics(topics: Iterable[Topic]) -> tuple[Topic, ...]:
    """High-signal example scopes, including parser-misaligned headings.

    Some PDFs expose a TOC node for an example but attach its blocks to the
    preceding subsection. Looking at both the node label and canonical block
    text keeps that parser boundary error from silently hiding the example.
    Empty outline nodes are excluded; their heading is discovered in the
    neighboring topic that actually owns the content.
    """

    return tuple(
        topic
        for topic in topics
        if len(MARKER_ONLY_LINE.sub("", topic.evidence_text).strip()) >= 80
        and (
            STRUCTURED_EXAMPLE_TOPIC.search(topic.label)
            or STRUCTURED_EXAMPLE_EVIDENCE.search(topic.evidence_text)
        )
    )


def _visual_page_text(page: fitz.Page) -> str:
    """Reconstruct readable lines from source-PDF word coordinates.

    Canonical table extraction is intentionally lossless but can flatten cells
    into an ambiguous token stream.  The original PDF's word coordinates let
    us restore rows deterministically without asking a model to guess them.
    """

    raw_words = page.get_text("words")
    source_lines: dict[tuple[int, int], list[tuple]] = {}
    for word in raw_words:
        source_lines.setdefault((word[5], word[6]), []).append(word)
    margin_source_lines = {
        line_id
        for line_id, line_words in source_lines.items()
        if line_words
        and min(item[0] for item in line_words) >= page.rect.width * 0.83
    }
    words = sorted(
        (
            word
            for word in raw_words
            if (word[5], word[6]) not in margin_source_lines
        ),
        key=lambda word: ((word[1] + word[3]) / 2, word[0]),
    )
    if not words:
        return page.get_text("text").strip()

    lines: list[list[object]] = []
    for word in words:
        center = (word[1] + word[3]) / 2
        target = next(
            (
                line
                for line in reversed(lines[-3:])
                if abs(center - float(line[0])) <= 5
            ),
            None,
        )
        if target is None:
            lines.append([center, [word]])
            continue
        target_words = target[1]
        assert isinstance(target_words, list)
        target_words.append(word)
        target[0] = sum((item[1] + item[3]) / 2 for item in target_words) / len(
            target_words
        )

    rendered: list[str] = []
    for _, line_words in lines:
        assert isinstance(line_words, list)
        # ISLP and similar textbooks put short glossary/API callouts in the
        # outside margin.  They are visually separate from the exercise but
        # coordinate extraction otherwise splices them into its sentences
        # (for example ``dictionary`` and ``.min()`` on ISLP page 66).
        # Only discard a line wholly beyond the main text column; full-width
        # question lines that merely end near the margin remain untouched.
        if line_words and min(item[0] for item in line_words) >= page.rect.width * 0.83:
            continue
        rendered.append(
            " ".join(item[4] for item in sorted(line_words, key=lambda item: item[0]))
        )
    return "\n".join(rendered).strip()


def _source_pdf_question_topics(
    connection: Connection,
    *,
    owner_id: str,
    book_id: int | None,
    topics: tuple[Topic, ...],
) -> tuple[Topic, ...]:
    """Re-read question pages from the original PDF when it is available.

    This is a deterministic fidelity pass, not a second canonical store.  The
    stored blocks remain authoritative; source words are used only to restore
    page layout that a table flattener or glyph map damaged.  If retention has
    removed the PDF, generation safely falls back to canonical evidence.
    """

    if book_id is None or not topics:
        return topics
    try:
        row = connection.execute(
            """
            select source_path, source_storage_bucket, source_storage_path,
                   source_storage_backend,
                   viewer_storage_bucket, viewer_storage_path,
                   viewer_storage_backend
            from books where id = %s and owner_id = %s
            """,
            (book_id, owner_id),
        ).fetchone()
        if row is None:
            return topics

        local_path = Path(row["source_path"] or "")
        document: fitz.Document
        if row["source_path"] and local_path.is_file():
            document = fitz.open(local_path)
        else:
            viewer = row["viewer_storage_path"]
            bucket = row["viewer_storage_bucket"] if viewer else row["source_storage_bucket"]
            object_path = viewer or row["source_storage_path"]
            # Same rule as the API's signing path: the backend must come from
            # whichever half of the row supplied the path.
            backend = (
                row["viewer_storage_backend"] if viewer
                else row["source_storage_backend"]
            )
            if not bucket or not object_path:
                return topics
            from ingestion.storage_objects import signed_object_url

            url = signed_object_url(
                bucket, object_path, expires_in=300, backend=backend
            )
            if not url:
                return topics
            response = httpx.get(url, timeout=60, follow_redirects=True)
            response.raise_for_status()
            document = fitz.open(stream=response.content, filetype="pdf")

        hydrated: list[Topic] = []
        with document:
            for topic in topics:
                if (
                    topic.node_id is None
                    or topic.start_page is None
                    or topic.end_page is None
                ):
                    hydrated.append(topic)
                    continue
                pieces: list[str] = []
                markers: set[str] = set()
                for page_number in range(topic.start_page, topic.end_page + 1):
                    if not 1 <= page_number <= document.page_count:
                        continue
                    marker = f"[N{topic.node_id}:P{page_number}]"
                    page_text = _visual_page_text(document.load_page(page_number - 1))
                    if not page_text:
                        continue
                    pieces.append(f"{marker}\n{page_text}")
                    markers.add(marker)
                hydrated.append(
                    replace(
                        topic,
                        evidence_text="\n\n".join(pieces) or topic.evidence_text,
                        allowed_markers=frozenset(markers) or topic.allowed_markers,
                    )
                )
        return tuple(hydrated)
    except Exception:
        logger.warning(
            "could not re-read source PDF question pages; using canonical blocks",
            extra={"book_id": book_id},
            exc_info=True,
        )
        return topics


def _clean_source_question(text: str) -> str:
    """Remove provenance markers without paraphrasing source-authored text."""

    cleaned = MARKER_ONLY_LINE.sub("", text).strip()
    cleaned = "\n".join(
        line
        for line in cleaned.splitlines()
        if not RUNNING_HEADER.match(line.strip())
        and line.strip().casefold() not in {"conceptual", "applied"}
    )
    # Repair line-wrap hyphenation introduced by PDF extraction while leaving
    # semantic hyphens and code untouched.
    cleaned = re.sub(r"(?<=[a-z])-\n[ \t]*(?=[a-z])", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # This glyph in the ISLP PDF has a broken ToUnicode mapping even though the
    # rendered page visibly contains epsilon.  The replacement is deliberately
    # narrow: only a quoted glyph used as the argument to Var is repaired.
    cleaned = re.sub(r"Var\(\s*[\"“”]\s*\)", "Var(ε)", cleaned)
    cleaned = cleaned.replace("ϵ", "ε")
    return cleaned.strip()


def _ordered_span_markers(text: str, *, preceding: str | None = None) -> list[str]:
    markers = [preceding] if preceding else []
    markers.extend(re.findall(r"\[N\d+:P\d+\]", text))
    return list(dict.fromkeys(marker for marker in markers if marker))


def _stable_source_item_key(item: ExtractedQuestion) -> str:
    """Content identity independent of repeated display labels."""

    normalized = normalized_front(item.question)
    digest = sha256(
        f"{item.item_kind}|{item.citation_marker}|{normalized}".encode()
    ).hexdigest()[:24]
    return f"{item.item_kind}:{digest}"


def _source_items(
    items: Iterable[ExtractedQuestion],
) -> tuple[ExtractedQuestion, ...]:
    """Assign stable keys and collapse exact cross-window duplicates."""

    unique: dict[str, ExtractedQuestion] = {}
    for index, item in enumerate(items, start=1):
        label = item.source_label or (
            f"Worked example {index}"
            if item.item_kind == "worked_example"
            else f"Question {index}"
        )
        with_identity = item.model_copy(
            update={
                "source_label": label,
                "source_item_key": item.source_item_key
                or _stable_source_item_key(item),
            }
        )
        assert with_identity.source_item_key is not None
        existing = unique.get(with_identity.source_item_key)
        if existing is None:
            unique[with_identity.source_item_key] = with_identity
            continue
        unique[with_identity.source_item_key] = existing.model_copy(
            update={
                "question_citation_markers": list(
                    dict.fromkeys(
                        [
                            *existing.question_citation_markers,
                            *with_identity.question_citation_markers,
                        ]
                    )
                ),
                "answer_citation_markers": list(
                    dict.fromkeys(
                        [
                            *existing.answer_citation_markers,
                            *with_identity.answer_citation_markers,
                        ]
                    )
                ),
            }
        )
    return tuple(unique.values())


def explicitly_labeled_candidates(
    topics: Iterable[Topic],
) -> tuple[ExtractedQuestion, ...]:
    """Find high-signal inline exercises and worked examples additively.

    Bare prose such as "for example" is intentionally excluded. A worked
    example is only classified when its bounded source span contains an
    explicit printed Solution or Answer.
    """

    items: list[ExtractedQuestion] = []
    for topic in topics:
        evidence = topic.evidence_text
        matches = list(EXPLICIT_ITEM.finditer(evidence))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(evidence)
            raw = evidence[match.start() : end].strip()
            preceding_markers = re.findall(
                r"\[N\d+:P\d+\]", evidence[: match.start()]
            )
            preceding = preceding_markers[-1] if preceding_markers else None
            label = " ".join(match.group("label").split())
            kind: Literal["exercise", "worked_example"] = (
                "worked_example"
                if re.match(r"(?i)(?:worked example|example\s+\d)", label)
                else "exercise"
            )
            solution = SOLUTION_LABEL.search(raw)
            if kind == "worked_example" and solution is None:
                continue

            if solution:
                prompt_end = solution.start()
                answer_prefix = ""
                preceding_solution_markers = list(
                    re.finditer(r"\[N\d+:P\d+\]", raw[: solution.start()])
                )
                if preceding_solution_markers:
                    last_marker = preceding_solution_markers[-1]
                    if not raw[last_marker.end() : solution.start()].strip():
                        prompt_end = last_marker.start()
                        answer_prefix = last_marker.group(0) + "\n"
                prompt_raw = raw[:prompt_end]
                answer_raw = answer_prefix + raw[solution.end() :]
            else:
                prompt_raw = raw
                answer_raw = ""
            question = _clean_source_question(prompt_raw)
            printed_answer = _clean_source_question(answer_raw) or None
            if not question:
                continue
            question_markers = _ordered_span_markers(
                prompt_raw, preceding=preceding
            )
            answer_markers = (
                _ordered_span_markers(
                    answer_raw,
                    preceding=(
                        question_markers[-1]
                        if question_markers and not _markers(answer_raw)
                        else None
                    ),
                )
                if printed_answer
                else []
            )
            if not question_markers:
                continue
            items.append(
                ExtractedQuestion(
                    question=question,
                    printed_answer=printed_answer,
                    citation_marker=question_markers[0],
                    question_citation_markers=question_markers,
                    answer_citation_markers=answer_markers,
                    source_label=label,
                    item_kind=kind,
                    placement="inline",
                    discovery_method="explicit_label",
                )
            )
    return tuple(items)


def numbered_question_candidates(
    topics: Iterable[Topic],
) -> tuple[ExtractedQuestion, ...]:
    """Split explicit question sections at top-level printed numbers.

    This operates on canonical text, not model output.  Consequently a long
    exercise can span page markers, contain a table, and exceed 1,000
    characters without being shortened or emitted a second time by the next
    evidence batch.
    """

    questions: list[ExtractedQuestion] = []
    for topic in topics:
        evidence = topic.evidence_text
        matches = list(NUMBERED_QUESTION.finditer(evidence))
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(evidence)
            )
            raw_question = evidence[match.start() : end].strip()
            # _markers() is a set.  Recover source order for citations and take
            # the final marker before the number as the question's first page.
            preceding = re.findall(r"\[N\d+:P\d+\]", evidence[: match.start()])
            ordered = list(preceding[-1:] if preceding else [])
            marker_matches = list(re.finditer(r"\[N\d+:P\d+\]", raw_question))
            for marker_index, marker_match in enumerate(marker_matches):
                marker_end = (
                    marker_matches[marker_index + 1].start()
                    if marker_index + 1 < len(marker_matches)
                    else len(raw_question)
                )
                marker_body = raw_question[marker_match.end() : marker_end]
                # A page marker followed only by a running header belongs to
                # the next exercise, not this one.
                if _clean_source_question(marker_body):
                    ordered.append(marker_match.group(0))
            markers = list(dict.fromkeys(ordered))
            if not markers:
                continue
            question = _clean_source_question(raw_question)
            if not question:
                continue
            number = match.group("number")
            questions.append(
                ExtractedQuestion(
                    question=question,
                    citation_marker=markers[0],
                    question_citation_markers=markers,
                    source_label=f"Exercise {number}",
                    item_kind="exercise",
                    placement="end_of_chapter",
                    discovery_method="numbered_section",
                )
            )
    return tuple(questions)


def _question_scan(inventory: ScopeInventory) -> QuestionScan:
    explicit_topics = question_section_topics(inventory.topics)
    if explicit_topics:
        numbered = numbered_question_candidates(explicit_topics)
        if numbered:
            return QuestionScan(numbered, explicit_topics, True)
        return QuestionScan((), explicit_topics, False)
    return QuestionScan((), inventory.topics, False)


def _markers(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\[N\d+:P\d+\]", text))


def _query_terms(text: str) -> set[str]:
    return {
        word.casefold()
        for word in WORDS.findall(text)
        if len(word) >= 3 and word.casefold() not in STOP_WORDS
    }


def answer_evidence(
    question: str,
    question_marker: str,
    topics: Iterable[Topic],
    *,
    token_budget: int = ANSWER_EVIDENCE_TOKENS,
) -> tuple[str, frozenset[str]]:
    """Select bounded in-chapter evidence with a deterministic lexical baseline."""

    segments = list(evidence_segments(topics, token_budget=token_budget))
    terms = _query_terms(question)
    ranked: list[tuple[int, int, int, str]] = []
    for position, segment in enumerate(segments):
        segment_terms = _query_terms(segment)
        marker_bonus = 2 if question_marker in _markers(segment) else 0
        ranked.append((marker_bonus, len(terms & segment_terms), -position, segment))
    ranked.sort(reverse=True)

    selected: list[str] = []
    used = 0
    for _, overlap, _, segment in ranked:
        # Always keep the question page. Elsewhere, require at least one shared
        # term so generic chapter prose does not crowd out relevant evidence.
        if question_marker not in _markers(segment) and overlap == 0:
            continue
        cost = _token_count(segment)
        if selected and used + cost > token_budget:
            continue
        selected.append(segment)
        used += cost
        if used >= token_budget:
            break

    text = "\n\n".join(selected)
    return text, _markers(text)


def _extraction_metrics(
    inventory: ScopeInventory,
    tally: ValidationTally,
    *,
    source_items: Iterable[ExtractedQuestion] = (),
    covered_source_item_keys: Iterable[str] = (),
    repair_attempted: bool = False,
    notice: str | None = None,
) -> DeckMetrics:
    """Metrics compare stored cards with the pre-answer question inventory."""

    items = tuple(source_items)
    covered = frozenset(covered_source_item_keys)
    labels = tuple(item.source_label or "Source item" for item in items)
    kind_counts: dict[str, int] = {}
    placement_counts: dict[str, int] = {}
    for item in items:
        kind_counts[item.item_kind] = kind_counts.get(item.item_kind, 0) + 1
        placement_counts[item.placement] = placement_counts.get(item.placement, 0) + 1

    return DeckMetrics(
        topics_total=len(inventory.topics),
        # Extracted questions are not intended to cover every chapter topic.
        topics_required=0,
        topics_covered=0,
        source_questions_total=len(labels),
        source_questions_covered=sum(
            bool(item.source_item_key and item.source_item_key in covered)
            for item in items
        ),
        uncovered_question_labels=[
            item.source_label or "Source item"
            for item in items
            if not item.source_item_key or item.source_item_key not in covered
        ],
        source_items_total=len(items),
        source_items_covered=sum(
            bool(item.source_item_key and item.source_item_key in covered)
            for item in items
        ),
        source_item_kind_counts=dict(sorted(kind_counts.items())),
        source_item_placement_counts=dict(sorted(placement_counts.items())),
        uncovered_source_items=[
            {
                "key": item.source_item_key or "",
                "label": item.source_label or "Source item",
                "kind": item.item_kind,
                "placement": item.placement,
            }
            for item in items
            if not item.source_item_key or item.source_item_key not in covered
        ],
        cards_generated=tally.generated,
        cards_kept=tally.kept,
        cards_dropped_uncited=tally.dropped.get(DROP_UNCITED, 0),
        cards_dropped_out_of_scope=tally.dropped.get(DROP_OUT_OF_SCOPE, 0),
        cards_dropped_duplicate=tally.dropped.get(DROP_DUPLICATE, 0),
        cards_dropped_malformed=tally.dropped.get(DROP_MALFORMED, 0),
        cards_with_interview_angle=tally.with_angle,
        card_type_counts=dict(sorted(tally.card_types.items())),
        priority_counts=dict(sorted(tally.priorities.items())),
        repair_attempted=repair_attempted,
        notice=notice,
    )


def _subparts(question: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            f"({match.group(1).lower()})" for match in SUBPART.finditer(question)
        )
    )


def _answer_gap(question: str, answer: str) -> str | None:
    """Return why a generated answer is not a usable complete exercise answer."""

    has_reproducible_code = bool(
        "```" in answer
        or re.search(r"\b(?:pd|np|plt|sns)\.", answer)
        or re.search(r"\bload_data\s*\(", answer)
    )
    if INSUFFICIENT_ANSWER.search(answer) and not has_reproducible_code:
        return "the answer is an insufficiency placeholder"
    missing = [label for label in _subparts(question) if label not in answer.casefold()]
    if missing:
        return f"the answer omits subparts {', '.join(missing)}"
    return None


def _alias_answer_evidence(text: str) -> tuple[str, dict[str, str]]:
    """Give the model short opaque citation ids it cannot recombine.

    Models occasionally copy a real node id and a real page number into a pair
    that never existed (for example N3267 with P45).  Sequential evidence ids
    remove that failure mode; the application maps them back to canonical
    markers before validation and storage.
    """

    originals = list(dict.fromkeys(re.findall(r"\[N\d+:P\d+\]", text)))
    aliases = {f"[E{index}]": marker for index, marker in enumerate(originals, 1)}
    aliased = text
    for alias, marker in aliases.items():
        aliased = aliased.replace(marker, alias)
    return aliased, aliases


def _resolve_answer_aliases(text: str, aliases: dict[str, str]) -> str:
    """Replace internal evidence ids before any answer text reaches storage."""

    resolved = text
    for alias, marker in aliases.items():
        resolved = resolved.replace(alias, marker)
    return resolved


def _valid_citations(markers: Iterable[str], marker_to_topic: dict[str, Topic]) -> list:
    citations = []
    for marker in dict.fromkeys(item.strip() for item in markers if item.strip()):
        if marker not in marker_to_topic:
            return []
        citation = parse_marker(marker)
        if citation is None:
            return []
        citations.append(citation)
    return citations


@traced("decks.extraction.extract_and_generate_deck", flow="cards")
def extract_and_generate_deck(
    inventory: ScopeInventory,
    *,
    connection: Connection,
    owner_id: str,
    book_id: int | None = None,
    progress: ProgressCallback | None = None,
) -> GeneratedDeck:
    """Extract printed questions and build cited answers for one book scope."""

    marker_to_topic = {
        marker: topic for topic in inventory.topics for marker in topic.allowed_markers
    }
    explicit_topics = question_section_topics(inventory.topics)
    explicit_keys = {topic.key for topic in explicit_topics}
    if explicit_topics:
        source_topics = _source_pdf_question_topics(
            connection,
            owner_id=owner_id,
            book_id=book_id,
            topics=explicit_topics,
        )
        for source_topic in source_topics:
            for marker in source_topic.allowed_markers:
                marker_to_topic[marker] = source_topic
        numbered = numbered_question_candidates(source_topics)
        residual_topics = tuple(
            topic for topic in inventory.topics if topic.key not in explicit_keys
        )
        detected = [
            *numbered,
            *explicitly_labeled_candidates(residual_topics),
        ]
        fallback_topics = source_topics
    else:
        detected = list(explicitly_labeled_candidates(inventory.topics))
        fallback_topics = inventory.topics

    batches = evidence_batches(fallback_topics)
    if not batches:
        raise DeckGenerationError(
            "this scope has no citable text to scan for questions"
        )

    extracted_questions: list[ExtractedQuestion] = detected
    detected_markers = {
        marker
        for item in detected
        for marker in (
            item.question_citation_markers or [item.citation_marker]
        )
    }
    example_topics = tuple(
        topic
        for topic in structured_example_topics(inventory.topics)
        if topic.key not in explicit_keys
        and not (topic.allowed_markers & detected_markers)
    )
    if example_topics:
        example_extractor = _question_extraction_model()
        for example_index, example_topic in enumerate(example_topics, start=1):
            topic_items: list[ExtractedQuestion] = []
            example_batches = evidence_batches((example_topic,))
            for batch_index, evidence in enumerate(example_batches, start=1):
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "This excerpt contains an explicitly headed worked example. "
                            "Extract the named example as one complete review item. A named "
                            "dataset or case analysis with author-run code, calculations, "
                            "results, or interpretation counts as a worked example even when "
                            "the book does not phrase its setup as a question or print a "
                            "'Solution' label. In that case, formulate a faithful review "
                            "question from the named analysis goal without adding facts, and "
                            "copy the author's complete code/reasoning/findings into "
                            "printed_answer. Set item_kind to worked_example and placement to "
                            "inline. Copy separate exact page markers into "
                            "question_citation_markers and answer_citation_markers. Ignore "
                            "bare illustrative anecdotes. Preserve tables, code, equations, "
                            "and every labelled step without truncation."
                        ),
                    },
                    {"role": "user", "content": f"Example excerpt:\n\n{evidence}"},
                ]
                try:
                    result: ExtractedQuestionList = example_extractor.invoke(messages)
                except Exception as exc:
                    logger.exception(
                        "worked-example extraction batch failed",
                        extra={
                            "scope_key": inventory.scope_key,
                            "example": example_index,
                            "batch": batch_index,
                        },
                    )
                    raise DeckGenerationError(
                        "worked-example extraction failed for "
                        f"{example_topic.label}: {exc}"
                    ) from exc
                supplied_markers = _markers(evidence)
                for item in result.questions:
                    emitted_markers = {
                        item.citation_marker.strip(),
                        *(marker.strip() for marker in item.question_citation_markers),
                        *(marker.strip() for marker in item.answer_citation_markers),
                    }
                    if not emitted_markers <= supplied_markers:
                        raise DeckGenerationError(
                            "worked-example extraction returned an out-of-scope citation"
                        )
                    if not item.printed_answer or not item.answer_citation_markers:
                        continue
                    item.item_kind = "worked_example"
                    item.placement = "inline"
                    item.discovery_method = "model_fallback"
                    if item.source_label is None:
                        item.source_label = example_topic.label.split("::")[-1].strip()
                    topic_items.append(item)
            if not topic_items:
                raise DeckGenerationError(
                    "the structurally labelled worked example produced no complete card: "
                    f"{example_topic.label}"
                )
            extracted_questions.extend(topic_items)

    # The legacy model fallback remains only for chapters with no explicit,
    # high-signal source item. One detected category never suppresses another:
    # end exercises and inline labelled material were collected additively.
    if not extracted_questions:
        extractor = _question_extraction_model()
        for batch_index, evidence in enumerate(batches, start=1):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Extract only explicitly source-authored exercises, review/study "
                        "questions, labelled problem directives, or worked examples with a "
                        "printed Solution/Answer. Ignore rhetorical questions and bare prose "
                        "phrases such as 'for example'. Preserve every setup sentence, data "
                        "row, code fragment, printed solution, "
                        "and labelled subpart; never abbreviate with an ellipsis and never "
                        "truncate. A top-level numbered exercise is one question even when it "
                        "spans pages. Copy every page marker spanned by the question into "
                        "question_citation_markers. Set printed_answer only when the excerpt "
                        "explicitly contains a solution; never answer from model knowledge."
                    ),
                },
                {"role": "user", "content": f"Book excerpt:\n\n{evidence}"},
            ]
            try:
                result: ExtractedQuestionList = extractor.invoke(messages)
            except Exception as exc:
                logger.exception(
                    "book-question extraction batch failed",
                    extra={"scope_key": inventory.scope_key, "batch": batch_index},
                )
                raise DeckGenerationError(
                    f"question extraction failed in batch {batch_index} of {len(batches)}: {exc}"
                ) from exc
            supplied_markers = _markers(evidence)
            for item in result.questions:
                item.discovery_method = "model_fallback"
                item.placement = (
                    "end_of_chapter" if explicit_topics else "inline"
                )
                emitted_markers = {
                    item.citation_marker.strip(),
                    *(marker.strip() for marker in item.question_citation_markers),
                    *(marker.strip() for marker in item.answer_citation_markers),
                }
                if not emitted_markers <= supplied_markers:
                    raise DeckGenerationError(
                        "question extraction returned a citation outside its supplied evidence"
                    )
            extracted_questions.extend(result.questions)

    source_items = _source_items(extracted_questions)

    if not source_items:
        tally = ValidationTally()
        return GeneratedDeck(
            inventory=inventory,
            cards=(),
            metrics=_extraction_metrics(
                inventory,
                tally,
                notice="No explicitly labelled exercises or worked examples found",
            ),
            model_name=model_name(),
            prompt_version=EXTRACTION_PROMPT_VERSION,
        )

    cards: list[DeckCard] = []
    tally = ValidationTally()
    rag_model = None
    covered_source_item_keys: list[str] = []
    repair_attempted = False

    if progress is not None:
        progress(0, len(source_items))

    for question_index, item in enumerate(source_items, start=1):
        tally.generated += 1
        question = item.question.strip()
        front_key = normalized_front(question)
        if not front_key:
            tally.drop(DROP_MALFORMED)
            continue

        question_markers = list(
            dict.fromkeys(
                marker.strip()
                for marker in (item.question_citation_markers or [item.citation_marker])
                if marker.strip()
            )
        )
        question_marker = item.citation_marker.strip()
        if question_marker not in question_markers:
            question_markers.insert(0, question_marker)
        topic = marker_to_topic.get(question_marker)
        question_citation = parse_marker(question_marker)
        if topic is None or question_citation is None:
            tally.drop(DROP_OUT_OF_SCOPE if question_citation else DROP_UNCITED)
            continue
        question_citations = _valid_citations(question_markers, marker_to_topic)
        if not question_citations:
            tally.drop(DROP_OUT_OF_SCOPE)
            continue

        answer_source: AnswerSource
        if item.printed_answer and item.printed_answer.strip():
            answer_text = item.printed_answer.strip()
            answer_issue = _answer_gap(question, answer_text)
            if answer_issue is not None:
                raise DeckGenerationError(
                    f"printed solution validation failed for "
                    f"{item.source_label or question_index}: {answer_issue}"
                )
            if (
                item.item_kind == "worked_example"
                and not item.answer_citation_markers
            ):
                raise DeckGenerationError(
                    f"worked example {item.source_label or question_index} "
                    "has no separate printed-solution citation"
                )
            answer_markers = item.answer_citation_markers or [question_marker]
            answer_citations = _valid_citations(answer_markers, marker_to_topic)
            if not answer_citations:
                tally.drop(DROP_OUT_OF_SCOPE)
                continue
            citations = _valid_citations(
                [*question_markers, *answer_markers], marker_to_topic
            )
            back = CardBack(
                answer=answer_text,
                say_it_aloud=answer_text[:400],
            )
            answer_source = "printed_in_book"
        else:
            evidence, allowed_answer_markers = answer_evidence(
                question, question_marker, inventory.topics
            )
            if not evidence or not allowed_answer_markers:
                tally.drop(DROP_MALFORMED)
                continue
            if rag_model is None:
                rag_model = _rag_answer_model()
            aliased_evidence, citation_aliases = _alias_answer_evidence(evidence)
            answer_system = (
                "Answer the complete printed book exercise strictly from the supplied chapter "
                "evidence. Address every labelled subpart in order and repeat each label, such "
                "as (a), in the answer. For a coding or data-analysis exercise whose computed "
                "outputs are not printed, give a complete reproducible worked solution with "
                "code for every part and state which observations the code establishes; do "
                "not return an 'insufficient evidence' placeholder and do not invent numeric "
                "results. Copy one or more exact [E#] evidence markers supporting the answer; "
                "never construct a marker yourself. Label "
                "answer_source printed_in_book only when the evidence explicitly supplies the "
                "solution; otherwise use synthesized_from_book."
            )
            answer_messages = [
                {
                    "role": "system",
                    "content": answer_system,
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\nChapter evidence:\n{aliased_evidence}"
                    ),
                },
            ]
            rag_output = None
            answer_issue = None
            resolved_answer_markers: list[str] = []
            for attempt in range(2):
                try:
                    rag_output = rag_model.invoke(answer_messages)
                except Exception as exc:
                    logger.exception(
                        "answer generation for extracted question failed",
                        extra={"scope_key": inventory.scope_key},
                    )
                    raise DeckGenerationError(
                        f"answer generation failed for extracted question: {exc}"
                    ) from exc
                gap = _answer_gap(question, rag_output.answer)
                resolved_answer_markers = []
                invalid_markers: list[str] = []
                for raw_marker in rag_output.citation_markers:
                    marker = raw_marker.strip()
                    resolved = citation_aliases.get(marker, marker)
                    if resolved not in allowed_answer_markers:
                        invalid_markers.append(marker)
                    else:
                        resolved_answer_markers.append(resolved)
                answer_issue = gap
                if invalid_markers:
                    answer_issue = (
                        "the answer uses citation markers that were not supplied: "
                        f"{', '.join(invalid_markers)}"
                    )
                unresolved_inline = [
                    marker
                    for marker in re.findall(r"\[E\d+\]", rag_output.answer)
                    if marker not in citation_aliases
                ]
                if unresolved_inline:
                    answer_issue = (
                        "the answer uses citation markers that were not supplied: "
                        f"{', '.join(dict.fromkeys(unresolved_inline))}"
                    )
                if answer_issue is None:
                    break
                if attempt == 0:
                    repair_attempted = True
                    answer_messages.extend(
                        [
                            {
                                "role": "assistant",
                                "content": rag_output.model_dump_json(),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Repair the answer because {answer_issue}. Return a complete "
                                    "part-by-part worked answer under the same citation rules."
                                ),
                            },
                        ]
                    )
            if rag_output is None or answer_issue is not None:
                raise DeckGenerationError(
                    f"answer validation failed for {item.source_label or question_index}: "
                    f"{answer_issue}"
                )
            citations = _valid_citations(
                [*question_markers, *resolved_answer_markers], marker_to_topic
            )
            if not citations:
                raise DeckGenerationError(
                    "answer generation returned no valid in-scope citation"
                )
            back = CardBack(
                answer=_resolve_answer_aliases(
                    rag_output.answer.strip(), citation_aliases
                ),
                key_points=[
                    _resolve_answer_aliases(point.strip(), citation_aliases)
                    for point in rag_output.key_points
                    if point.strip()
                ],
                say_it_aloud=_resolve_answer_aliases(
                    rag_output.say_it_aloud.strip(), citation_aliases
                ),
            )
            has_explicit_printed_solution = bool(
                re.search(r"(?im)^\s*(?:solution|answer)\b", evidence)
            )
            answer_source = (
                "printed_in_book"
                if rag_output.answer_source == "printed_in_book"
                and has_explicit_printed_solution
                else "rag_generated"
            )
            answer_citations = _valid_citations(
                resolved_answer_markers, marker_to_topic
            )
            if not answer_citations:
                raise DeckGenerationError(
                    "answer generation returned no separately valid answer citation"
                )

        card = DeckCard(
            topic_key=topic.key,
            card_index=len(cards),
            card_type="qa",
            front=question,
            back=back,
            citations=citations,
            interview_priority=3,
            difficulty=item.difficulty,
            answer_source=answer_source,
            source_item_key=item.source_item_key,
            source_item_kind=item.item_kind,
            source_item_placement=item.placement,
            source_label=item.source_label,
            source_discovery_method=item.discovery_method,
            question_citations=question_citations,
            answer_citations=answer_citations,
        )
        cards.append(card)
        tally.keep(card)
        assert item.source_item_key is not None
        covered_source_item_keys.append(item.source_item_key)
        if progress is not None:
            progress(question_index, len(source_items))

    if not cards:
        raise DeckGenerationError(
            "questions were detected, but none had valid in-scope citations and answers"
        )
    covered_keys = frozenset(covered_source_item_keys)
    uncovered = [
        item.source_label or item.source_item_key or "Source item"
        for item in source_items
        if not item.source_item_key or item.source_item_key not in covered_keys
    ]
    if uncovered:
        raise DeckGenerationError(
            "source-question deck is incomplete; missing " + ", ".join(uncovered)
        )

    return GeneratedDeck(
        inventory=inventory,
        cards=tuple(cards),
        metrics=_extraction_metrics(
            inventory,
            tally,
            source_items=source_items,
            covered_source_item_keys=covered_source_item_keys,
            repair_attempted=repair_attempted,
        ),
        model_name=model_name(),
        prompt_version=EXTRACTION_PROMPT_VERSION,
    )
