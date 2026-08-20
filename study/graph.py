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
    GroundingRung,
    PromptProfile,
    ResponseDepth,
    TurnDecision,
    TurnResult,
    WideningStep,
)
from .conversation import execute_decision, record_turn
from .grounding import GroundingPolicy, insufficiency, settled_rung
from .query import ChatModel
from .side_context import SideContext
from .streaming import TokenCallback


class StudyGraphInput(TypedDict):
    question: str
    conversation: ConversationState


class StudyGraphState(StudyGraphInput):
    decision: NotRequired[TurnDecision]
    result: NotRequired[TurnResult]
    # The grounding ladder's working state. Absent unless the turn runs under a
    # policy, which is what keeps every existing turn on exactly the path it
    # took before.
    rung: NotRequired[GroundingRung]
    widenings: NotRequired[list[WideningStep]]
    next_step: NotRequired[str]


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
    # Present when this turn is source-first: which source the reader has open,
    # what else they could consult, and whether the ladder may leave their
    # material at all. Without one the graph runs exactly as it did before —
    # the main chat's routing is measured against a frozen gold set and must
    # not move to serve a feature that does not need it.
    grounding_policy: GroundingPolicy | None = None


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
            anchored_locations=(
                side_context.anchored_locations if side_context else ()
            ),
        )
    }


ExecutionNode = Literal[
    "execute_library",
    "execute_hierarchy",
    "execute_retrieval",
    "transform_answer",
    "clarify",
    "execute_external",
]


def route_turn(state: StudyGraphState) -> ExecutionNode:
    route = state["decision"].route
    if route == "library_list":
        return "execute_library"
    if route in {"hierarchy_summary", "hierarchy_list"}:
        return "execute_hierarchy"
    if route == "retrieval_qa":
        return "execute_retrieval"
    if route == "prior_answer_transform":
        return "transform_answer"
    if route == "external_qa":
        return "execute_external"
    return "clarify"


def execute_route(
    state: StudyGraphState,
    runtime: Runtime[StudyGraphContext],
) -> dict:
    policy = runtime.context.grounding_policy
    rung = state.get("rung") or ("open_source" if policy else None)
    turn_book_ids = runtime.context.turn_book_ids
    if policy and rung and not turn_book_ids:
        # An explicit @mention is the reader asking for a wider search in so
        # many words, and it outranks the rung the ladder happens to be on.
        # Everything else searches what this rung is allowed to search.
        turn_book_ids = policy.books_for(rung)
    return {
        "rung": rung,
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
            turn_book_ids=turn_book_ids,
            side_context=runtime.context.side_context,
            # Under a policy the graph owns escalation entirely. Letting the
            # query layer fall through to external QA on its own would skip the
            # library rung and leave the jump unrecorded — which is how the
            # behaviour worked before this became a ladder.
            allow_external_fallback=policy is None,
        ),
    }


def check_sufficiency(
    state: StudyGraphState,
    runtime: Runtime[StudyGraphContext],
) -> dict:
    """Decide whether this rung answered, and record the widening if not.

    The verdict is the abstention the grounded answer already produces, so
    nothing here asks a model anything. What it adds is the bookkeeping: which
    rung was tried, what it said, and where the turn goes next.
    """

    policy = runtime.context.grounding_policy
    result = state["result"]
    rung: GroundingRung = state.get("rung") or "open_source"
    if policy is None:
        return {"next_step": "update_state"}

    reason = insufficiency(result)
    next_rung = policy.next_rung(rung) if reason else None
    if reason is None or next_rung is None:
        # Either the rung answered, or the ladder has run out — with the lock
        # on, that is an abstention that stands rather than a question quietly
        # answered from outside the reader's sources.
        return {"next_step": "update_state"}

    widenings = [
        *state.get("widenings", []),
        WideningStep(from_rung=rung, to_rung=next_rung, reason=reason),
    ]
    if next_rung == "model_knowledge":
        return {
            "rung": next_rung,
            "widenings": widenings,
            "next_step": "execute_external",
            # The same question, asked of a different kind of source. Routing
            # rather than a second execution path keeps one place where an
            # ungrounded answer is produced.
            "decision": state["decision"].model_copy(update={"route": "external_qa"}),
        }
    return {
        "rung": next_rung,
        "widenings": widenings,
        "next_step": "execute_retrieval",
    }


def after_sufficiency(state: StudyGraphState) -> str:
    return state.get("next_step") or "update_state"


def update_state(
    state: StudyGraphState,
    runtime: Runtime[StudyGraphContext],
) -> dict:
    """Record the turn, with the ladder it climbed stamped onto its result.

    Stamped here rather than in the executing node because the rung an answer
    *rests* on is a property of its citations, which only exist once it has
    been produced — an answer searched across the whole book but citing only
    the reader's anchored passage was answered by that passage.
    """

    result = state["result"]
    policy = runtime.context.grounding_policy
    if policy is not None:
        side_context = runtime.context.side_context
        result = result.model_copy(
            update={
                "grounding_rung": settled_rung(
                    result,
                    state.get("rung") or "open_source",
                    pinned_ids=(
                        side_context.pinned_chunk_ids if side_context else ()
                    ),
                ),
                "widenings": list(state.get("widenings", [])),
            }
        )
    return {
        "result": result,
        "conversation": record_turn(
            state["conversation"],
            state["question"],
            result,
        ),
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
        "execute_library",
        "execute_hierarchy",
        "execute_retrieval",
        "transform_answer",
        "clarify",
        "execute_external",
    ):
        builder.add_node(node, execute_route)
        if node != "execute_retrieval":
            builder.add_edge(node, "update_state")
    # Only retrieval widens. A clarification asked the reader something and a
    # hierarchy summary named its own scope; searching more sources would
    # answer a question neither of them posed.
    builder.add_node("check_sufficiency", check_sufficiency)
    builder.add_edge("execute_retrieval", "check_sufficiency")
    builder.add_conditional_edges(
        "check_sufficiency",
        after_sufficiency,
        # The retrieval edge is the retry: the same node, one rung wider.
        # Climbing is monotone and each rung is tried at most once, so the
        # cycle is bounded by the length of the ladder.
        ["execute_retrieval", "execute_external", "update_state"],
    )
    builder.add_node("update_state", update_state)
    builder.add_edge(START, "plan_turn")
    builder.add_conditional_edges("plan_turn", route_turn)
    builder.add_edge("update_state", END)
    return builder.compile()


study_turn_graph = build_study_graph()
