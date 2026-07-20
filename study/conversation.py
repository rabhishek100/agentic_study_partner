"""Minimal conversational coordinator over existing study operations."""

from pathlib import Path
from uuid import uuid4

from retrieval.search import RetrievalMode

from .analyze import AnalysisModel, analyze_turn
from .contracts import (
    ConversationMessage,
    ConversationState,
    StateUpdate,
    SufficiencyDecision,
    TurnAnalysis,
    TurnResult,
)
from .query import ChatModel, execute_query, openrouter_model


def new_conversation_state(
    *,
    book_id: int | None = None,
    conversation_id: str | None = None,
) -> ConversationState:
    """Create one in-memory UI/API conversation."""

    return ConversationState(
        conversation_id=conversation_id or str(uuid4()),
        book_id=book_id,
    )


def _state_for_book(
    state: ConversationState | None,
    *,
    book_id: int | None,
) -> ConversationState:
    if state is None:
        return new_conversation_state(book_id=book_id)
    if (
        book_id is not None
        and state.book_id is not None
        and state.book_id != book_id
    ):
        return new_conversation_state(book_id=book_id)
    updated = state.model_copy(deep=True)
    if updated.book_id is None:
        updated.book_id = book_id
    return updated


def _canonical_hierarchy_query(analysis: TurnAnalysis) -> str:
    scope = analysis.resolved_scope
    if scope is None:
        raise ValueError("hierarchy analysis requires a resolved scope")
    if analysis.route == "hierarchy_list":
        return f"What sections are present in {scope.display_path}?"
    return f"Summarize {scope.display_path}."


def _clarification_result(
    question: str,
    analysis: TurnAnalysis,
) -> TurnResult:
    return TurnResult(
        question=question,
        answer=analysis.clarification_question
        or "Could you clarify what you are referring to?",
        route="clarify",
        history_dependency=analysis.history_dependency,
        standalone_query=None,
        scope_behavior="clarify",
        state_update=StateUpdate(pending_clarification="set"),
        outcome="clarify",
    )


def _transform_previous_answer(
    question: str,
    *,
    state: ConversationState,
    model: ChatModel,
) -> TurnResult:
    reply = model.invoke(
        [
            (
                "system",
                "Transform the prior answer exactly as requested. Do not add "
                "new facts. Preserve its citation markers and qualification.",
            ),
            (
                "human",
                f"Request:\n{question}\n\nPrior answer:\n"
                f"{state.previous_answer}",
            ),
        ]
    )
    return TurnResult(
        question=question,
        answer=str(reply.content),
        route="prior_answer_transform",
        history_dependency="dependent",
        standalone_query=None,
        scope_behavior="reuse_prior_answer",
        resolved_scope=state.active_scope,
        evidence=list(state.previous_evidence),
        citations=list(state.previous_citations),
        sufficiency=SufficiencyDecision(
            status="sufficient",
            reason="The response only transforms the previously grounded answer.",
        ),
        outcome="answer",
    )


def _execute_analysis(
    question: str,
    analysis: TurnAnalysis,
    *,
    state: ConversationState,
    database_path: str | Path,
    source_path: str | Path,
    chroma_path: str | Path,
    retrieval_mode: RetrievalMode,
    generation_model: ChatModel | None,
) -> TurnResult:
    if analysis.route == "clarify":
        return _clarification_result(question, analysis)

    if analysis.route == "prior_answer_transform" and state.previous_answer:
        if generation_model is None:
            generation_model = openrouter_model()
        return _transform_previous_answer(
            question,
            state=state,
            model=generation_model,
        )

    if analysis.route in {"hierarchy_summary", "hierarchy_list"}:
        execution_question = _canonical_hierarchy_query(analysis)
    else:
        # Abstention is an evidence decision. In the initial conversational
        # flow, retrieve first and let grounded QA report insufficiency.
        execution_question = analysis.standalone_query or question

    result = execute_query(
        execution_question,
        database_path=str(database_path),
        source_path=str(source_path),
        chroma_path=str(chroma_path),
        book_id=state.book_id,
        retrieval_mode=retrieval_mode,
        model=generation_model,
    )
    if result.route == "retrieval_qa":
        return result.model_copy(
            update={
                "question": question,
                "history_dependency": analysis.history_dependency,
                "standalone_query": execution_question,
                "scope_behavior": "global",
                "resolved_scope": None,
            }
        )
    return result.model_copy(
        update={
            "question": question,
            "history_dependency": analysis.history_dependency,
            "standalone_query": analysis.standalone_query,
        }
    )


def _apply_result(
    state: ConversationState,
    *,
    question: str,
    result: TurnResult,
) -> tuple[TurnResult, ConversationState]:
    updated = state.model_copy(deep=True)
    active_update = "retain" if updated.active_scope else "none"
    clarification_update = "none"

    if (
        result.route in {"hierarchy_summary", "hierarchy_list"}
        and result.resolved_scope is not None
    ):
        updated.active_scope = result.resolved_scope
        updated.book_id = result.resolved_scope.book_id
        active_update = "set_active_scope"

    if result.route == "clarify":
        updated.pending_clarification = question
        clarification_update = "set"
    elif updated.pending_clarification is not None:
        updated.pending_clarification = None
        clarification_update = "clear"

    turn_number = sum(
        message.role == "user" for message in updated.messages
    ) + 1
    turn_id = f"{updated.conversation_id}-t{turn_number}"
    updated.messages.extend(
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
    updated.previous_route = result.route
    if result.outcome == "answer":
        updated.previous_answer = result.answer
        updated.previous_evidence = list(result.evidence)
        updated.previous_citations = list(result.citations)

    applied_update = StateUpdate(
        active_scope=active_update,
        pending_clarification=clarification_update,
    )
    return (
        result.model_copy(update={"state_update": applied_update}),
        updated,
    )


def execute_conversation_turn(
    question: str,
    state: ConversationState | None = None,
    *,
    database_path: str | Path = "data/retrieval.sqlite3",
    source_path: str | Path = "data/books.sqlite3",
    chroma_path: str | Path = "data/chroma",
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    analysis_model: AnalysisModel | None = None,
    generation_model: ChatModel | None = None,
) -> tuple[TurnResult, ConversationState]:
    """Understand, execute, and record one conversational book turn."""

    current = _state_for_book(state, book_id=book_id)
    analysis = analyze_turn(
        question,
        current,
        source_path,
        model=analysis_model,
    )
    result = _execute_analysis(
        question,
        analysis,
        state=current,
        database_path=database_path,
        source_path=source_path,
        chroma_path=chroma_path,
        retrieval_mode=retrieval_mode,
        generation_model=generation_model,
    )
    return _apply_result(current, question=question, result=result)
