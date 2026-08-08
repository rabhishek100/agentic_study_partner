"""Minimal LangGraph coordinator for one observable study turn."""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from retrieval.search import RetrievalMode

from .analyze import AnalysisModel, analyze_turn
from .contracts import (
    ConversationState,
    PromptProfile,
    ResponseDepth,
    TurnDecision,
    TurnResult,
)
from .conversation import execute_decision, record_turn
from .query import ChatModel
from .side_context import SideContext
from .streaming import TokenCallback


class StudyGraphInput(TypedDict):
    question: str
    conversation: ConversationState


class StudyGraphState(StudyGraphInput):
    decision: NotRequired[TurnDecision]
    result: NotRequired[TurnResult]


class StudyGraphOutput(TypedDict):
    conversation: ConversationState
    result: TurnResult


@dataclass(frozen=True)
class StudyGraphContext:
    owner_id: str | UUID
    database_url: str | None = None
    retrieval_mode: RetrievalMode = "hybrid"
    analysis_model: AnalysisModel | None = None
    generation_model: ChatModel | None = None
    token_callback: TokenCallback | None = None
    prompt_profile: PromptProfile | None = None
    response_depth: ResponseDepth = "interview"
    # Optional per-turn narrowing supplied by explicit @book mentions. The
    # conversation's library selection remains unchanged for later turns.
    turn_book_ids: tuple[int, ...] | None = None
    # Present when this turn belongs to a side chat: the passages the reader
    # anchored it to, plus the evidence those passages cited.
    side_context: SideContext | None = None


def plan_turn(
    state: StudyGraphState,
    runtime: Runtime[StudyGraphContext],
) -> dict:
    conversation = state["conversation"]
    if runtime.context.turn_book_ids:
        conversation = conversation.model_copy(
            update={"book_ids": list(runtime.context.turn_book_ids)}
        )
    side_context = runtime.context.side_context
    return {
        "decision": analyze_turn(
            state["question"],
            conversation,
            runtime.context.database_url,
            owner_id=runtime.context.owner_id,
            model=runtime.context.analysis_model,
            anchored_quotes=side_context.anchored_quotes if side_context else (),
        )
    }


ExecutionNode = Literal[
    "execute_hierarchy",
    "execute_retrieval",
    "transform_answer",
    "clarify",
]


def route_turn(state: StudyGraphState) -> ExecutionNode:
    route = state["decision"].route
    if route in {"hierarchy_summary", "hierarchy_list"}:
        return "execute_hierarchy"
    if route == "retrieval_qa":
        return "execute_retrieval"
    if route == "prior_answer_transform":
        return "transform_answer"
    return "clarify"


def execute_route(
    state: StudyGraphState,
    runtime: Runtime[StudyGraphContext],
) -> dict:
    return {
        "result": execute_decision(
            state["question"],
            state["decision"],
            state["conversation"],
            database_url=runtime.context.database_url,
            owner_id=runtime.context.owner_id,
            retrieval_mode=runtime.context.retrieval_mode,
            model=runtime.context.generation_model,
            token_callback=runtime.context.token_callback,
            prompt_profile=runtime.context.prompt_profile,
            response_depth=runtime.context.response_depth,
            turn_book_ids=runtime.context.turn_book_ids,
            side_context=runtime.context.side_context,
        )
    }


def update_state(state: StudyGraphState) -> dict:
    return {
        "conversation": record_turn(
            state["conversation"],
            state["question"],
            state["result"],
        )
    }


def build_study_graph():
    builder = StateGraph(
        StudyGraphState,
        input_schema=StudyGraphInput,
        output_schema=StudyGraphOutput,
        context_schema=StudyGraphContext,
    )
    builder.add_node("plan_turn", plan_turn)
    for node in (
        "execute_hierarchy",
        "execute_retrieval",
        "transform_answer",
        "clarify",
    ):
        builder.add_node(node, execute_route)
        builder.add_edge(node, "update_state")
    builder.add_node("update_state", update_state)
    builder.add_edge(START, "plan_turn")
    builder.add_conditional_edges("plan_turn", route_turn)
    builder.add_edge("update_state", END)
    return builder.compile()


study_turn_graph = build_study_graph()
