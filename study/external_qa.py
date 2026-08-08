"""Execution logic for non-book / non-video queries via model knowledge or web search."""

from datetime import datetime, timezone
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from retrieval.web_search import assess_model_knowledge_sufficiency, search_web_sources
from study.contracts import (
    ConversationState,
    HistoryDependency,
    ResponseDepth,
    TurnResult,
)
from study.query import ChatModel, openrouter_model
from study.streaming import TokenCallback, invoke_with_streaming

logger = logging.getLogger("study_partner.external_qa")


def execute_external_qa(
    question: str,
    conversation: ConversationState,
    *,
    model: ChatModel | None = None,
    token_callback: TokenCallback | None = None,
    response_depth: ResponseDepth = "interview",
    history_dependency: HistoryDependency = "independent",
    standalone_query: str | None = None,
    routing_reason: str | None = None,
) -> TurnResult:
    """Answer an out-of-domain query using LLM model knowledge or live web search."""

    chat_model = model or openrouter_model()
    query_text = standalone_query or question

    # 1. Determine whether live web search is explicitly needed or if model knowledge is sufficient.
    WEB_SEARCH_TRIGGER = re.compile(
        r"\b(?:latest|recent|news|today|current\s+events|live\s+data|search\s+web)\b",
        re.IGNORECASE,
    )
    needs_web_search = bool(WEB_SEARCH_TRIGGER.search(query_text))

    current_date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    if not needs_web_search:
        logger.info(f"Answering out-of-domain question directly from LLM model knowledge: {question}")
        system_prompt = (
            f"Today's date is {current_date_str}.\n"
            "You are an expert technical study partner. The requested topic was not found in "
            "the user's ingested book and video library.\n\n"
            "Answer the question thoroughly, accurately, and clearly using general AI model knowledge.\n"
            "You MUST start your answer with this exact markdown header line:\n"
            "ℹ️ **General Model Knowledge**: *This answer is derived from general AI model knowledge, "
            "as this topic was not found in your ingested study library.*\n\n"
            "Provide a well-structured, educational explanation."
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=question),
        ]

        res = invoke_with_streaming(chat_model, messages, token_callback=token_callback)
        answer_text = str(getattr(res, "content", res))

        return TurnResult(
            question=question,
            answer=answer_text,
            route="external_qa",
            history_dependency=history_dependency,
            standalone_query=query_text,
            outcome="answer",
            source_type="model_knowledge",
            routing_reason=routing_reason or "Book/video evidence was unavailable or insufficient; answered via model knowledge.",
            response_depth=response_depth,
            evidence=[],
            citations=[],
            web_sources=[],
        )

    # 2. Otherwise execute web search
    logger.info(f"Answering out-of-domain question via Web Search: {question}")
    web_sources = search_web_sources(query_text, max_results=5)

    if not web_sources:
        # Fallback if web search returns zero results
        system_prompt = (
            f"Today's date is {current_date_str}.\n"
            "You are an expert technical study partner. The requested topic was not found in "
            "the user's ingested library, and web search returned no results.\n\n"
            "Answer the question to the best of your ability using general AI model knowledge.\n"
            "You MUST start your answer with this exact markdown header line:\n"
            "ℹ️ **General Model Knowledge**: *This answer is derived from general AI model knowledge, "
            "as this topic was not found in your ingested study library.*\n"
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=question),
        ]
        res = invoke_with_streaming(chat_model, messages, token_callback=token_callback)
        answer_text = str(getattr(res, "content", res))

        return TurnResult(
            question=question,
            answer=answer_text,
            route="external_qa",
            history_dependency=history_dependency,
            standalone_query=query_text,
            outcome="answer",
            source_type="model_knowledge",
            routing_reason=routing_reason or "Web search returned 0 results; answered via model knowledge.",
            response_depth=response_depth,
            evidence=[],
            citations=[],
            web_sources=[],
        )

    # Format web sources into context
    web_context_blocks = []
    for ws in web_sources:
        web_context_blocks.append(
            f"[Web {ws.rank}] Title: {ws.title}\nURL: {ws.url}\nSnippet: {ws.snippet}"
        )
    formatted_web_context = "\n\n".join(web_context_blocks)

    system_prompt = (
        f"Today's date is {current_date_str}.\n"
        "You are an expert technical study partner. The requested topic was not found in "
        "the user's ingested book and video library. Web search results have been retrieved below.\n\n"
        "You MUST start your answer with this exact markdown header line:\n"
        "🌐 **Web Search Results**: *This topic was not found in your ingested study library. "
        "The answer below is synthesized from web search results.*\n\n"
        "Cite your web sources inline using markdown links with markers, e.g. [Web 1: Title](url).\n\n"
        f"Web Search Context:\n{formatted_web_context}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ]

    res = invoke_with_streaming(chat_model, messages, token_callback=token_callback)
    answer_text = str(getattr(res, "content", res))

    return TurnResult(
        question=question,
        answer=answer_text,
        route="external_qa",
        history_dependency=history_dependency,
        standalone_query=query_text,
        outcome="answer",
        source_type="web_search",
        routing_reason=routing_reason or "Book/video evidence was unavailable or insufficient; answered via web search.",
        response_depth=response_depth,
        evidence=[],
        citations=[],
        web_sources=web_sources,
    )
