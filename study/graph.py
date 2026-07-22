"""Minimal LangGraph coordinator for one observable study turn."""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from retrieval.search import RetrievalMode

from .analyze import AnalysisModel, analyze_turn
from .contracts import ConversationState, TurnDecision, TurnResult
from .conversation import execute_decision, record_turn
from .query import ChatModel
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
    database_url: str | None = None
    retrieval_mode: RetrievalMode = "hybrid"
    analysis_model: AnalysisModel | None = None
    generation_model: ChatModel | None = None
    token_callback: TokenCallback | None = None


def plan_turn(
    state: StudyGraphState,
    runtime: Runtime[StudyGraphContext],
) -> dict:
    return {
        "decision": analyze_turn(
            state["question"],
            state["conversation"],
            runtime.context.database_url,
            model=runtime.context.analysis_model,
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
            retrieval_mode=runtime.context.retrieval_mode,
            model=runtime.context.generation_model,
            token_callback=runtime.context.token_callback,
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
