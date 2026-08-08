"""Execute one conversational turn over summaries and book retrieval."""

import re
from collections.abc import Sequence
from uuid import UUID, uuid4

from dotenv import load_dotenv

from retrieval.search import RetrievalMode

from .analyze import AnalysisModel
from .contracts import (
    ConversationMessage,
    ConversationState,
    PromptProfile,
    ResponseDepth,
    TurnDecision,
    TurnResult,
)
from .prompts import (
    DEFAULT_PROMPT_PROFILE,
    build_answer_messages,
    profile_version,
    resolve_answer_archetype,
    resolve_response_depth,
)
from .query import ChatModel, execute_query, openrouter_model
from .side_context import SideContext
from .streaming import TokenCallback, invoke_with_streaming


def new_conversation_state(
    *,
    book_ids: Sequence[int] | None = None,
    conversation_id: str | None = None,
) -> ConversationState:
    return ConversationState(
        conversation_id=conversation_id or str(uuid4()),
        book_ids=sorted({int(identifier) for identifier in book_ids or ()}),
    )


def _select_state(
    state: ConversationState | None,
    book_ids: Sequence[int] | None,
) -> ConversationState:
    """Continue the conversation, or start a new one if its scope changed.

    Answers already given were grounded in the previous selection, so a
    different set of books is a different conversation rather than a filter
    applied retroactively to one already under way.
    """

    requested = sorted({int(identifier) for identifier in book_ids or ()})
    if state is None or (requested and state.book_ids and state.book_ids != requested):
        return new_conversation_state(book_ids=requested)
    state = state.model_copy(deep=True)
    state.book_ids = state.book_ids or requested
    return state


def _hierarchy_query(decision: TurnDecision) -> str:
    scope = decision.resolved_scope
    if scope is None:
        raise ValueError("hierarchy decision has no scope")
    if decision.route == "hierarchy_list":
        if scope.kind == "book":
            return "What chapters does this book have?"
        return f"What sections are present in {scope.display_path}?"
    if scope.kind == "chapter":
        return f"Summarize {scope.display_path}."
    parts = scope.display_path.split(" :: ")
    chapter = re.sub(r"^Chapter\s+", "", parts[0], flags=re.IGNORECASE)
    return f"Summarize section {parts[-1]} in chapter {chapter}."


def _transform(
    question: str,
    state: ConversationState,
    model: ChatModel | None,
    prompt_profile: PromptProfile,
    response_depth: ResponseDepth,
    routing_reason: str,
    token_callback: TokenCallback | None = None,
) -> TurnResult:
    model = model or openrouter_model()
    response = invoke_with_streaming(
        model,
        build_answer_messages(
            profile=prompt_profile,
            question=question,
            evidence=state.previous_answer or "",
            archetype="answer_transform",
            depth=response_depth,
            request_context="Transform the previous answer rather than retrieving again.",
            additional_grounding=(
                "Transform the prior answer as requested. Add no facts and "
                "preserve every citation marker and qualification."
            ),
        ),
        token_callback=token_callback,
    )
    return TurnResult(
        question=question,
        answer=str(response.content),
        route="prior_answer_transform",
        history_dependency="dependent",
        resolved_scope=state.active_scope,
        evidence=list(state.previous_evidence),
        citations=list(state.previous_citations),
        outcome="answer",
        answer_archetype="answer_transform",
        response_depth=response_depth,
        routing_reason=routing_reason,
        prompt_profile_version=profile_version(prompt_profile),
    )


def execute_decision(
    question: str,
    decision: TurnDecision,
    state: ConversationState,
    *,
    database_url: str | None,
    owner_id: str | UUID,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
    token_callback: TokenCallback | None = None,
    prompt_profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
    turn_book_ids: Sequence[int] | None = None,
    side_context: SideContext | None = None,
) -> TurnResult:
    profile = prompt_profile or DEFAULT_PROMPT_PROFILE
    resolved_depth = resolve_response_depth(question, response_depth)
    report = side_context.report if side_context else None
    if decision.route == "clarify":
        return TurnResult(
            question=question,
            answer=decision.clarification_question or "Could you clarify?",
            route="clarify",
            history_dependency=decision.history_dependency,
            outcome="clarify",
            response_depth=resolved_depth,
            routing_reason=decision.reason,
            prompt_profile_version=profile_version(profile),
            side_context=report,
        )
    if decision.route == "prior_answer_transform":
        transformed = _transform(
            question,
            state,
            model,
            profile,
            resolved_depth,
            decision.reason,
            token_callback=token_callback,
        )
        return transformed.model_copy(update={"side_context": report})

    is_hierarchy = decision.route in {"hierarchy_summary", "hierarchy_list"}
    execution_question = (
        _hierarchy_query(decision)
        if is_hierarchy
        else decision.standalone_query or question
    )
    # A hierarchy decision already names one book. `execute_query` re-derives
    # the scope from the rendered sentence, and that sentence carries no book,
    # so resolving it against the whole selection made "summarize chapter 1"
    # ambiguous across every book the reader had open - after the analyser had
    # already decided which one they meant.
    execution_book_ids = (
        (decision.resolved_scope.book_id,)
        if is_hierarchy and decision.resolved_scope
        else turn_book_ids or state.book_ids or None
    )
    result = execute_query(
        execution_question,
        database_url=database_url,
        book_ids=execution_book_ids,
        retrieval_mode=retrieval_mode,
        owner_id=owner_id,
        model=model,
        token_callback=token_callback,
        force_retrieval=decision.route == "retrieval_qa",
        prompt_profile=profile,
        response_depth=resolved_depth,
        routing_reason=decision.reason,
        answer_archetype=resolve_answer_archetype(question, decision.route),
        pinned_chunk_ids=side_context.pinned_chunk_ids if side_context else (),
        request_context=side_context.request_context if side_context else "",
    )
    updates = {
        "question": question,
        "history_dependency": decision.history_dependency,
        "standalone_query": execution_question,
        "side_context": report,
    }
    if result.route == "retrieval_qa":
        chapter_refs = set(
            re.findall(r"\bchapter\s+(\d+)\b", execution_question, re.IGNORECASE)
        )
        if len(chapter_refs) > 1:
            updates["resolved_scope"] = None
        elif state.active_scope and decision.history_dependency == "dependent":
            updates["resolved_scope"] = state.active_scope
        elif state.pending_clarification and decision.resolved_scope:
            updates["resolved_scope"] = decision.resolved_scope
        else:
            updates["resolved_scope"] = None
    return result.model_copy(update=updates)


def record_turn(
    state: ConversationState,
    question: str,
    result: TurnResult,
) -> ConversationState:
    state = state.model_copy(deep=True)
    if result.resolved_scope:
        # The resolved scope is recorded, but the conversation's book
        # selection is deliberately left alone: answering one turn from one
        # book must not silently narrow the rest of the conversation to it.
        state.active_scope = result.resolved_scope
    state.pending_clarification = question if result.route == "clarify" else None

    turn = sum(message.role == "user" for message in state.messages) + 1
    turn_id = f"{state.conversation_id}-t{turn}"
    state.messages.extend(
        [
            ConversationMessage(
                role="user",
                content=question,
                turn_id=turn_id,
            ),
            ConversationMessage(
                role="assistant",
                content=result.answer,
                turn_id=turn_id,
            ),
        ]
    )
    state.previous_route = result.route
    if result.outcome in {"answer", "abstain"}:
        state.previous_answer = result.answer
        state.previous_evidence = list(result.evidence)
        state.previous_citations = list(result.citations)
    return state


def execute_conversation_turn(
    question: str,
    state: ConversationState | None = None,
    *,
    owner_id: str | UUID,
    database_url: str | None = None,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
    turn_book_ids: Sequence[int] | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    analysis_model: AnalysisModel | None = None,
    generation_model: ChatModel | None = None,
    token_callback: TokenCallback | None = None,
    prompt_profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
    side_context: SideContext | None = None,
) -> tuple[TurnResult, ConversationState]:
    load_dotenv()
    # `book_id` remains for the CLI and evaluation entry points, which study
    # one book at a time.
    selection = (
        book_ids
        if book_ids is not None
        else ([book_id] if book_id is not None else None)
    )
    current = _select_state(state, selection)
    from .graph import StudyGraphContext, study_turn_graph

    output = study_turn_graph.invoke(
        {"question": question, "conversation": current},
        config={
            "run_name": "study_turn",
            "tags": ["conversation", "rag"],
            "metadata": {
                "thread_id": current.conversation_id,
                "conversation_id": current.conversation_id,
                "book_ids": current.book_ids,
                "retrieval_mode": retrieval_mode,
                # A side turn is traced with what its priority context
                # actually resolved to, so the inclusion decision is
                # reviewable after the fact rather than only in the answer.
                "side_chat": side_context is not None,
                **(
                    {
                        "side_chat_anchor_ids": list(
                            side_context.report.anchor_ids
                        ),
                        "side_chat_pinned_chunk_ids": list(
                            side_context.pinned_chunk_ids
                        ),
                        "side_chat_dropped_context": list(
                            side_context.report.dropped
                        ),
                    }
                    if side_context
                    else {}
                ),
            },
        },
        context=StudyGraphContext(
            owner_id=owner_id,
            database_url=database_url,
            retrieval_mode=retrieval_mode,
            analysis_model=analysis_model,
            generation_model=generation_model,
            token_callback=token_callback,
            prompt_profile=prompt_profile or DEFAULT_PROMPT_PROFILE,
            response_depth=response_depth,
            turn_book_ids=(
                tuple(sorted({int(identifier) for identifier in turn_book_ids}))
                if turn_book_ids
                else None
            ),
            side_context=side_context,
        ),
    )
    return output["result"], output["conversation"]
