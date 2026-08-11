"""Extract questions printed in books and ground any missing answers.

Question extraction is deliberately separate from topic-card generation.  A
book question is source material, not a prompt-inspired card, and its answer
provenance must remain visible all the way to the review UI.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import fitz
import httpx
import tiktoken
from psycopg import Connection
from pydantic import BaseModel, Field

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

EXTRACTION_PROMPT_VERSION = "v3_faithful_book_questions"
ANSWER_EVIDENCE_TOKENS = GENERATION_BATCH_TOKENS
SEGMENT_OVERLAP_TOKENS = 160
MARKER_LINE = re.compile(r"(?m)(?=^\[N\d+:P\d+\]\s*$)")
MARKER_AT_START = re.compile(r"^\[N\d+:P\d+\]")
MARKER_ONLY_LINE = re.compile(r"(?m)^[ \t]*\[N\d+:P\d+\][ \t]*\n?")
NUMBERED_QUESTION = re.compile(r"(?m)^[ \t]*(?P<number>\d{1,3})[.)][ \t]+(?=\S)")
QUESTION_SECTION = re.compile(
    r"\b(?:exercises?|problems?|review questions?|study questions?|"
    r"self[- ]assessment|check your understanding)\b",
    re.IGNORECASE,
)
SUBPART = re.compile(r"(?<!\w)\(([a-z]|[ivx]{1,4}|\d{1,2})\)", re.IGNORECASE)
INSUFFICIENT_ANSWER = re.compile(
    r"\b(?:insufficient evidence|not enough evidence|cannot (?:answer|determine)|"
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
        max_length=4_000,
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


def _visual_page_text(page: fitz.Page) -> str:
    """Reconstruct readable lines from source-PDF word coordinates.

    Canonical table extraction is intentionally lossless but can flatten cells
    into an ambiguous token stream.  The original PDF's word coordinates let
    us restore rows deterministically without asking a model to guess them.
    """

    words = sorted(
        page.get_text("words"),
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
                   viewer_storage_bucket, viewer_storage_path
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
            bucket = row["viewer_storage_bucket"] or row["source_storage_bucket"]
            object_path = row["viewer_storage_path"] or row["source_storage_path"]
            if not bucket or not object_path:
                return topics
            from ingestion.storage_objects import signed_object_url

            url = signed_object_url(bucket, object_path, expires_in=300)
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
    question_labels: Iterable[str] = (),
    covered_question_labels: Iterable[str] = (),
    repair_attempted: bool = False,
    notice: str | None = None,
) -> DeckMetrics:
    """Metrics compare stored cards with the pre-answer question inventory."""

    labels = tuple(question_labels)
    covered = frozenset(covered_question_labels)

    return DeckMetrics(
        topics_total=len(inventory.topics),
        # Extracted questions are not intended to cover every chapter topic.
        topics_required=0,
        topics_covered=0,
        source_questions_total=len(labels),
        source_questions_covered=sum(label in covered for label in labels),
        uncovered_question_labels=[label for label in labels if label not in covered],
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
    if explicit_topics:
        source_topics = _source_pdf_question_topics(
            connection,
            owner_id=owner_id,
            book_id=book_id,
            topics=explicit_topics,
        )
        numbered = numbered_question_candidates(source_topics)
        scan = QuestionScan(numbered, source_topics, bool(numbered))
    else:
        scan = _question_scan(inventory)
    batches = evidence_batches(scan.evidence_topics)
    if not batches:
        raise DeckGenerationError(
            "this scope has no citable text to scan for questions"
        )

    extracted_questions: list[ExtractedQuestion] = list(scan.questions)
    if not scan.used_numbered_boundaries:
        extractor = _question_extraction_model()
        for batch_index, evidence in enumerate(batches, start=1):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Extract only source-authored exercises, review/study questions, or "
                        "explicitly labelled problem directives from the supplied excerpt. "
                        "Ignore rhetorical questions in explanatory prose and tutorial/lab "
                        "discussion. Preserve every setup sentence, data row, code fragment, "
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

    for index, item in enumerate(extracted_questions, start=1):
        if item.source_label is None:
            item.source_label = f"Question {index}"

    if not extracted_questions:
        tally = ValidationTally()
        return GeneratedDeck(
            inventory=inventory,
            cards=(),
            metrics=_extraction_metrics(
                inventory, tally, notice="No questions printed in this chapter"
            ),
            model_name=model_name(),
            prompt_version=EXTRACTION_PROMPT_VERSION,
        )

    cards: list[DeckCard] = []
    tally = ValidationTally()
    seen_fronts: set[str] = set()
    rag_model = None
    question_labels = [
        item.source_label or f"Question {index}"
        for index, item in enumerate(extracted_questions, start=1)
    ]
    covered_question_labels: list[str] = []
    repair_attempted = False

    if progress is not None:
        progress(0, len(extracted_questions))

    for question_index, item in enumerate(extracted_questions, start=1):
        tally.generated += 1
        question = item.question.strip()
        front_key = normalized_front(question)
        if not front_key or front_key in seen_fronts:
            tally.drop(DROP_DUPLICATE)
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

        answer_source: AnswerSource
        if item.printed_answer and item.printed_answer.strip():
            answer_text = item.printed_answer.strip()
            answer_markers = item.answer_citation_markers or [question_marker]
            citations = _valid_citations(
                [*question_markers, *answer_markers], marker_to_topic
            )
            if not citations:
                tally.drop(DROP_OUT_OF_SCOPE)
                continue
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
                answer=rag_output.answer.strip(),
                key_points=[
                    point.strip() for point in rag_output.key_points if point.strip()
                ],
                say_it_aloud=rag_output.say_it_aloud.strip(),
            )
            answer_source = (
                "printed_in_book"
                if rag_output.answer_source == "printed_in_book"
                else "rag_generated"
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
        )
        seen_fronts.add(front_key)
        cards.append(card)
        tally.keep(card)
        covered_question_labels.append(
            item.source_label or f"Question {question_index}"
        )
        if progress is not None:
            progress(question_index, len(extracted_questions))

    if not cards:
        raise DeckGenerationError(
            "questions were detected, but none had valid in-scope citations and answers"
        )
    uncovered = [
        label for label in question_labels if label not in covered_question_labels
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
            question_labels=question_labels,
            covered_question_labels=covered_question_labels,
            repair_attempted=repair_attempted,
        ),
        model_name=model_name(),
        prompt_version=EXTRACTION_PROMPT_VERSION,
    )
