"""Extract questions and printed/RAG answers from PDF book text into flashcard decks."""

from __future__ import annotations

import logging
import os
from typing import Literal

from pydantic import BaseModel, Field
from psycopg import Connection

from study.content import load_scope_content
from study.scope import ScopeNode

from .contracts import (
    AnswerSource,
    CardBack,
    DeckCard,
    DeckCitation,
    DeckMetrics,
    GeneratedCard,
)
from .generate import DEFAULT_GENERATION_MODEL, GeneratedDeck, card_model, model_name
from .topics import ScopeInventory, Topic
from .validate import ValidationTally, build_metrics, parse_marker, validate_card

logger = logging.getLogger("study_partner.decks.extraction")


class ExtractedQuestion(BaseModel):
    """Structured question extracted directly from PDF text."""

    question: str = Field(description="The exact or cleaned question prompt extracted from the book.")
    printed_answer: str | None = Field(
        default=None,
        description="The printed answer or solution extracted from the text, or null if no printed answer exists.",
    )
    citation_marker: str = Field(
        description="The exact citation marker from the text where the question appears, e.g. [N123:P45]."
    )
    difficulty: Literal["foundational", "intermediate", "advanced"] = "intermediate"


class ExtractedQuestionList(BaseModel):
    """List of extracted questions returned by structured LLM parsing."""

    questions: list[ExtractedQuestion] = Field(default_factory=list)


class RAGAnswerOutput(BaseModel):
    """Grounded answer generated for an extracted question missing a printed answer."""

    answer: str = Field(description="The complete, accurate answer grounded in the chapter text.")
    key_points: list[str] = Field(default_factory=list, description="Key summary points for review.")
    say_it_aloud: str = Field(description="A concise 1-2 sentence summary to say out loud.")


def _question_extraction_model():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required to extract book questions")

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_DECK_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_GENERATION_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=0.1,
    )
    return model.with_structured_output(ExtractedQuestionList, method="json_schema")


def _rag_answer_model():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required to generate RAG answers")

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_DECK_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_GENERATION_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=0.2,
    )
    return model.with_structured_output(RAGAnswerOutput, method="json_schema")


def extract_and_generate_deck(
    inventory: ScopeInventory,
    *,
    connection: Connection,
    owner_id: str,
) -> GeneratedDeck:
    """Extract printed questions from book text and construct a flashcard deck."""

    # Compile chapter text with citation markers
    evidence_parts: list[str] = []
    marker_to_topic: dict[str, Topic] = {}

    for topic in inventory.topics:
        evidence_parts.append(topic.evidence_text)
        for marker in topic.allowed_markers:
            marker_to_topic[marker] = topic

    combined_text = "\n\n".join(evidence_parts)

    extractor = _question_extraction_model()
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert technical editor. Scan the provided book chapter text for explicit "
                "exercise questions, review questions, study questions, or end-of-chapter questions. "
                "Extract each question along with any printed answer/solution present in the text. "
                "Always attribute each question to an exact citation marker present in the text, "
                "such as [N123:P45]."
            ),
        },
        {
            "role": "user",
            "content": f"Extract all explicit exercise and review questions from this chapter text:\n\n{combined_text}",
        },
    ]

    try:
        extracted: ExtractedQuestionList = extractor.invoke(messages)
    except Exception as exc:
        logger.error("Failed to extract questions from chapter: %s", exc)
        extracted = ExtractedQuestionList(questions=[])

    if not extracted.questions:
        tally = ValidationTally()
        metrics = build_metrics(inventory, tally, kept_cards=[])
        metrics.notice = "No printed questions found in this chapter"
        return GeneratedDeck(
            inventory=inventory,
            cards=(),
            metrics=metrics,
            model_name=model_name(),
            prompt_version="v1_book_extracted",
        )

    cards: list[DeckCard] = []
    tally = ValidationTally()
    rag_model = None

    for idx, item in enumerate(extracted.questions):
        tally.generated += 1
        marker = item.citation_marker.strip()
        citation = parse_marker(marker)
        if citation is None and inventory.topics:
            # Fallback to first available marker in inventory if model emitted slightly malformed marker
            first_topic = inventory.topics[0]
            first_marker = next(iter(first_topic.allowed_markers), None)
            if first_marker:
                marker = first_marker
                citation = parse_marker(marker)

        if citation is None:
            tally.drop("uncited")
            continue

        topic = marker_to_topic.get(marker) or (inventory.topics[0] if inventory.topics else None)
        if topic is None:
            tally.drop("out_of_scope")
            continue

        if item.printed_answer and item.printed_answer.strip():
            answer_text = item.printed_answer.strip()
            back = CardBack(
                answer=answer_text,
                say_it_aloud=answer_text[:350],
            )
            answer_source: AnswerSource = "printed_in_book"
        else:
            # Generate RAG answer grounded in chapter text
            if rag_model is None:
                rag_model = _rag_answer_model()
            answer_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a technical study tutor. Answer the given question based strictly on the "
                        "provided chapter text. Provide a clear answer, key summary points, and a concise "
                        "1-2 sentence say_it_aloud summary."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {item.question}\n\nChapter Text:\n{combined_text}",
                },
            ]
            try:
                rag_output: RAGAnswerOutput = rag_model.invoke(answer_messages)
                back = CardBack(
                    answer=rag_output.answer,
                    key_points=rag_output.key_points,
                    say_it_aloud=rag_output.say_it_aloud,
                )
            except Exception as exc:
                logger.warning("RAG answer generation failed for question: %s", exc)
                back = CardBack(
                    answer="Refer to the chapter text for details.",
                    say_it_aloud="Refer to the chapter text for details.",
                )
            answer_source = "rag_generated"

        card = DeckCard(
            topic_key=topic.key,
            card_index=len(cards),
            card_type="qa",
            front=item.question.strip(),
            back=back,
            citations=[citation],
            interview_priority=3,
            difficulty=item.difficulty,
            answer_source=answer_source,
        )

        cards.append(card)
        tally.keep(card)

    covered_keys = {card.topic_key for card in cards}
    metrics = build_metrics(
        tally,
        topics=inventory.topics,
        covered_keys=covered_keys,
        repair_attempted=False,
    )
    return GeneratedDeck(
        inventory=inventory,
        cards=tuple(cards),
        metrics=metrics,
        model_name=model_name(),
        prompt_version="v1_book_extracted",
    )
