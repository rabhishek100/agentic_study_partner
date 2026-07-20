"""Execute one conversational turn over summaries and book retrieval."""

from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from retrieval.search import RetrievalMode

from .analyze import AnalysisModel
from .contracts import (
    ConversationMessage,
    ConversationState,
    TurnDecision,
    TurnResult,
)
from .query import ChatModel, execute_query, openrouter_model


def new_conversation_state(
    *,
    book_id: int | None = None,
    conversation_id: str | None = None,
) -> ConversationState:
    return ConversationState(
        conversation_id=conversation_id or str(uuid4()),
        book_id=book_id,
    )


def _select_state(
    state: ConversationState | None,
    book_id: int | None,
) -> ConversationState:
    if state is None or (
        book_id is not None
        and state.book_id is not None
        and state.book_id != book_id
    ):
        return new_conversation_state(book_id=book_id)
    state = state.model_copy(deep=True)
    state.book_id = state.book_id or book_id
    return state


def _hierarchy_query(decision: TurnDecision) -> str:
    scope = decision.resolved_scope
    if scope is None:
        raise ValueError("hierarchy decision has no scope")
    if decision.route == "hierarchy_list":
        if scope.kind == "book":
            return "What chapters does this book have?"
        return f"What sections are present in {scope.display_path}?"
    return f"Summarize {scope.display_path}."


def _transform(
    question: str,
    state: ConversationState,
    model: ChatModel | None,
) -> TurnResult:
    model = model or openrouter_model()
    response = model.invoke(
        [
            (
                "system",
                "Transform the prior answer as requested. Add no facts and "
                "preserve its citation markers and qualifications.",
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
        answer=str(response.content),
        route="prior_answer_transform",
        history_dependency="dependent",
        resolved_scope=state.active_scope,
        evidence=list(state.previous_evidence),
        citations=list(state.previous_citations),
        outcome="answer",
    )


def execute_decision(
    question: str,
    decision: TurnDecision,
    state: ConversationState,
    *,
    database_path: str | Path,
    source_path: str | Path,
    chroma_path: str | Path,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
) -> TurnResult:
    if decision.route == "clarify":
        return TurnResult(
            question=question,
            answer=decision.clarification_question or "Could you clarify?",
            route="clarify",
            history_dependency=decision.history_dependency,
            outcome="clarify",
        )
    if decision.route == "prior_answer_transform":
        return _transform(question, state, model)

    execution_question = (
        _hierarchy_query(decision)
        if decision.route in {"hierarchy_summary", "hierarchy_list"}
        else decision.standalone_query or question
    )
    result = execute_query(
        execution_question,
        database_path=str(database_path),
        source_path=str(source_path),
        chroma_path=str(chroma_path),
        book_id=state.book_id,
        retrieval_mode=retrieval_mode,
        model=model,
    )
    updates = {
        "question": question,
        "history_dependency": decision.history_dependency,
        "standalone_query": execution_question,
    }
    if result.route == "retrieval_qa":
        updates["resolved_scope"] = None
    return result.model_copy(update=updates)


def record_turn(
    state: ConversationState,
    question: str,
    result: TurnResult,
) -> ConversationState:
    state = state.model_copy(deep=True)
    if (
        result.route in {"hierarchy_summary", "hierarchy_list"}
        and result.resolved_scope
    ):
        state.active_scope = result.resolved_scope
        state.book_id = result.resolved_scope.book_id
    state.pending_clarification = (
        question if result.route == "clarify" else None
    )

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
    if result.outcome == "answer":
        state.previous_answer = result.answer
        state.previous_evidence = list(result.evidence)
        state.previous_citations = list(result.citations)
    return state


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
    load_dotenv()
    current = _select_state(state, book_id)
    from .graph import StudyGraphContext, study_turn_graph

    output = study_turn_graph.invoke(
        {"question": question, "conversation": current},
        config={
            "run_name": "study_turn",
            "tags": ["conversation", "rag"],
            "metadata": {
                "thread_id": current.conversation_id,
                "conversation_id": current.conversation_id,
                "book_id": current.book_id,
                "retrieval_mode": retrieval_mode,
            },
        },
        context=StudyGraphContext(
            database_path=database_path,
            source_path=source_path,
            chroma_path=chroma_path,
            retrieval_mode=retrieval_mode,
            analysis_model=analysis_model,
            generation_model=generation_model,
        ),
    )
    return output["result"], output["conversation"]
