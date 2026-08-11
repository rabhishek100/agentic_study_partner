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
from typing import Literal

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

EXTRACTION_PROMPT_VERSION = "v2_book_extracted"
ANSWER_EVIDENCE_TOKENS = GENERATION_BATCH_TOKENS
SEGMENT_OVERLAP_TOKENS = 160
MARKER_LINE = re.compile(r"(?m)(?=^\[N\d+:P\d+\]\s*$)")
MARKER_AT_START = re.compile(r"^\[N\d+:P\d+\]")
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
        max_length=1_000,
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
    answer_citation_markers: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Exact supplied markers supporting a printed answer.",
    )
    difficulty: Literal["foundational", "intermediate", "advanced"] = "intermediate"


class ExtractedQuestionList(BaseModel):
    """Questions found in one bounded evidence batch."""

    questions: list[ExtractedQuestion] = Field(default_factory=list, max_length=40)


class RAGAnswerOutput(BaseModel):
    """An answer whose supporting markers were copied from retrieved evidence."""

    answer: str = Field(min_length=1, max_length=4_000)
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
        ranked.append(
            (marker_bonus, len(terms & segment_terms), -position, segment)
        )
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
    inventory: ScopeInventory, tally: ValidationTally, *, notice: str | None = None
) -> DeckMetrics:
    """Metrics for source questions; topic coverage is intentionally not claimed."""

    return DeckMetrics(
        topics_total=len(inventory.topics),
        # Extracted questions are not intended to cover every chapter topic.
        topics_required=0,
        topics_covered=0,
        cards_generated=tally.generated,
        cards_kept=tally.kept,
        cards_dropped_uncited=tally.dropped.get(DROP_UNCITED, 0),
        cards_dropped_out_of_scope=tally.dropped.get(DROP_OUT_OF_SCOPE, 0),
        cards_dropped_duplicate=tally.dropped.get(DROP_DUPLICATE, 0),
        cards_dropped_malformed=tally.dropped.get(DROP_MALFORMED, 0),
        cards_with_interview_angle=tally.with_angle,
        card_type_counts=dict(sorted(tally.card_types.items())),
        priority_counts=dict(sorted(tally.priorities.items())),
        notice=notice,
    )


def _valid_citations(
    markers: Iterable[str], marker_to_topic: dict[str, Topic]
) -> list:
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
    progress: ProgressCallback | None = None,
) -> GeneratedDeck:
    """Extract printed questions and build cited answers for one book scope."""

    # Kept in the signature because this workflow is intentionally an
    # owner-scoped storage boundary even though v2 answers from loaded scope
    # evidence rather than issuing a second database query.
    del connection, owner_id

    marker_to_topic = {
        marker: topic for topic in inventory.topics for marker in topic.allowed_markers
    }
    batches = evidence_batches(inventory.topics)
    if not batches:
        raise DeckGenerationError("this scope has no citable text to scan for questions")

    extractor = _question_extraction_model()
    extracted_questions: list[ExtractedQuestion] = []
    for batch_index, evidence in enumerate(batches, start=1):
        messages = [
            {
                "role": "system",
                "content": (
                    "Extract only explicit questions printed in the supplied book excerpt: "
                    "exercises, review/study questions, or numbered problem directives. Do "
                    "not turn explanatory prose into new questions. Preserve multi-part "
                    "questions as one prompt when their subparts share setup or must be solved "
                    "together. Split a subpart into its own question only when the book labels "
                    "it explicitly and it can be answered independently. Copy exact citation "
                    "markers. Set printed_answer only when the supplied excerpt explicitly "
                    "contains the answer or solution; never answer from your own knowledge."
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
                *(marker.strip() for marker in item.answer_citation_markers),
            }
            if not emitted_markers <= supplied_markers:
                raise DeckGenerationError(
                    "question extraction returned a citation outside its supplied evidence"
                )
        extracted_questions.extend(result.questions)
        if progress is not None:
            progress(batch_index, len(batches))

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

    for item in extracted_questions:
        tally.generated += 1
        question = item.question.strip()
        front_key = normalized_front(question)
        if not front_key or front_key in seen_fronts:
            tally.drop(DROP_DUPLICATE)
            continue

        question_marker = item.citation_marker.strip()
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
                [question_marker, *answer_markers], marker_to_topic
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
            answer_messages = [
                {
                    "role": "system",
                    "content": (
                        "Answer the printed book question strictly from the supplied chapter "
                        "evidence. If the evidence is insufficient, say so in the answer. Copy "
                        "one or more exact citation markers that support the answer; do not "
                        "invent markers or rely on outside knowledge. Label answer_source "
                        "printed_in_book only if the evidence explicitly supplies the answer "
                        "or solution; otherwise label it synthesized_from_book."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nChapter evidence:\n{evidence}",
                },
            ]
            try:
                rag_output: RAGAnswerOutput = rag_model.invoke(answer_messages)
            except Exception as exc:
                logger.exception(
                    "answer generation for extracted question failed",
                    extra={"scope_key": inventory.scope_key},
                )
                raise DeckGenerationError(
                    f"answer generation failed for extracted question: {exc}"
                ) from exc
            if any(
                marker.strip() not in allowed_answer_markers
                for marker in rag_output.citation_markers
            ):
                raise DeckGenerationError(
                    "answer generation returned a citation outside its supplied evidence"
                )
            citations = _valid_citations(
                [question_marker, *rag_output.citation_markers], marker_to_topic
            )
            if not citations:
                raise DeckGenerationError(
                    "answer generation returned no valid in-scope citation"
                )
            back = CardBack(
                answer=rag_output.answer.strip(),
                key_points=[point.strip() for point in rag_output.key_points if point.strip()],
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

    if not cards:
        raise DeckGenerationError(
            "questions were detected, but none had valid in-scope citations and answers"
        )

    return GeneratedDeck(
        inventory=inventory,
        cards=tuple(cards),
        metrics=_extraction_metrics(inventory, tally),
        model_name=model_name(),
        prompt_version=EXTRACTION_PROMPT_VERSION,
    )
