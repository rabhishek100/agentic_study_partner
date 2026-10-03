"""Inspectable plan → retrieve → check → retry workflow for course questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from psycopg import Connection

from observability import traced
from study.streaming import TokenCallback
from video.analyze import AnalysisModel, analyze_turn
from video.answers import VideoAnswerDependencies
from video.contracts import VideoMessage, VideoTurnDecision
from video.conversation import evaluate_sufficiency
from video.course_answers import synthesize_course_answer
from video.course_contracts import (
    CourseConversationState,
    CourseTurnResult,
)
from video.course_retrieval import CourseRetrieval, retrieve_course_evidence


@dataclass(frozen=True)
class CourseTurnContext:
    connection: Connection
    owner_id: str | UUID
    course_id: str | UUID
    course_title: str
    video_ids: list[UUID]
    dependencies: VideoAnswerDependencies
    analysis_model: AnalysisModel | None = None
    token_callback: TokenCallback | None = None


class CourseGraphInput(TypedDict):
    question: str
    conversation: CourseConversationState


class CourseGraphState(CourseGraphInput):
    decision: NotRequired[VideoTurnDecision]
    retrieval: NotRequired[CourseRetrieval]
    attempts: NotRequired[int]
    sufficient: NotRequired[bool]
    sufficiency_reason: NotRequired[str]
    result: NotRequired[CourseTurnResult]


def plan_turn(state: CourseGraphState, runtime: Runtime[CourseTurnContext]) -> dict:
    # The video router only reads the conversation protocol (messages,
    # previous answer/evidence, clarification), which course state shares.
    decision = analyze_turn(
        state["question"], state["conversation"], model=runtime.context.analysis_model
    )
    return {"decision": decision, "attempts": 0}


def route_after_plan(
    state: CourseGraphState,
) -> Literal["clarify", "transform_prior", "retrieve"]:
    if state["decision"].route == "clarify":
        return "clarify"
    if state["decision"].route == "prior_answer_transform":
        return "transform_prior"
    return "retrieve"


def retrieve(state: CourseGraphState, runtime: Runtime[CourseTurnContext]) -> dict:
    attempts = state.get("attempts", 0) + 1
    result = retrieve_course_evidence(
        runtime.context.connection,
        owner_id=runtime.context.owner_id,
        course_id=runtime.context.course_id,
        video_ids=runtime.context.video_ids,
        query=state["decision"].standalone_query or state["question"],
        limit=20 if attempts > 1 else 12,
        per_lecture_limit=6 if attempts > 1 else 4,
        text_embedder=runtime.context.dependencies.text_embedder,
        image_embedder=runtime.context.dependencies.image_embedder,
    )
    return {"retrieval": result, "attempts": attempts}


def check(state: CourseGraphState) -> dict:
    sufficient, reason = evaluate_sufficiency(
        state["question"], list(state["retrieval"].evidence)
    )
    return {"sufficient": sufficient, "sufficiency_reason": reason}


def route_after_check(state: CourseGraphState) -> Literal["synthesize", "retrieve"]:
    return "synthesize" if state["sufficient"] or state["attempts"] >= 2 else "retrieve"


def synthesize(state: CourseGraphState, runtime: Runtime[CourseTurnContext]) -> dict:
    retrieval = state["retrieval"]
    draft = synthesize_course_answer(
        runtime.context.connection,
        owner_id=runtime.context.owner_id,
        question=state["question"],
        course_title=runtime.context.course_title,
        evidence=list(retrieval.evidence),
        state=state["conversation"],
        dependencies=runtime.context.dependencies,
        token_callback=runtime.context.token_callback,
    )
    decision = state["decision"]
    return {
        "result": CourseTurnResult(
            question=state["question"],
            answer=draft.answer,
            route="evidence_qa",
            history_dependency=decision.history_dependency,
            standalone_query=decision.standalone_query or state["question"],
            evidence=list(retrieval.evidence),
            citations=list(draft.citations),
            outcome=draft.outcome,
            retrieval_attempts=state["attempts"],
            sufficiency_reason=state.get("sufficiency_reason"),
            routing_reason=decision.reason,
            cost_usd=draft.cost_usd,
            trace_id=_current_trace_id(),
            excluded_video_ids=[str(item) for item in retrieval.excluded_video_ids],
            warnings=(
                [
                    f"{len(retrieval.excluded_video_ids)} selected lecture(s) "
                    "were not published and were excluded."
                ]
                if retrieval.excluded_video_ids
                else []
            ),
        )
    }


def transform_prior(
    state: CourseGraphState, runtime: Runtime[CourseTurnContext]
) -> dict:
    conversation = state["conversation"]
    decision = state["decision"]
    draft = synthesize_course_answer(
        runtime.context.connection,
        owner_id=runtime.context.owner_id,
        question=(
            f"{state['question']}\n\nReshape the previous answer only. Add no "
            "facts and keep every citation marker."
        ),
        course_title=runtime.context.course_title,
        evidence=list(conversation.previous_evidence),
        state=conversation,
        dependencies=runtime.context.dependencies,
        token_callback=runtime.context.token_callback,
    )
    return {
        "result": CourseTurnResult(
            question=state["question"],
            answer=draft.answer,
            route="prior_answer_transform",
            history_dependency=decision.history_dependency,
            standalone_query=decision.standalone_query,
            evidence=list(conversation.previous_evidence),
            citations=list(draft.citations) or list(conversation.previous_citations),
            outcome=draft.outcome,
            retrieval_attempts=0,
            routing_reason=decision.reason,
            cost_usd=draft.cost_usd,
            trace_id=_current_trace_id(),
        )
    }


def clarify(state: CourseGraphState, runtime: Runtime[CourseTurnContext]) -> dict:
    decision = state["decision"]
    return {
        "result": CourseTurnResult(
            question=state["question"],
            answer=decision.clarification_question or "Could you clarify?",
            route="clarify",
            history_dependency=decision.history_dependency,
            outcome="clarify",
            retrieval_attempts=0,
            routing_reason=decision.reason,
            trace_id=_current_trace_id(),
        )
    }


def record(state: CourseGraphState) -> dict:
    conversation = state["conversation"].model_copy(deep=True)
    result = state["result"]
    turn = sum(item.role == "user" for item in conversation.messages) + 1
    turn_id = f"{conversation.conversation_id}-t{turn}"
    conversation.messages.extend(
        [
            VideoMessage(role="user", content=state["question"], turn_id=turn_id),
            VideoMessage(role="assistant", content=result.answer, turn_id=turn_id),
        ]
    )
    conversation.previous_answer = result.answer
    conversation.previous_evidence = list(result.evidence)
    conversation.previous_citations = list(result.citations)
    conversation.previous_route = result.route
    conversation.pending_clarification = (
        state["question"] if result.outcome == "clarify" else None
    )
    return {"conversation": conversation}


def build_graph():
    graph = StateGraph(CourseGraphState, context_schema=CourseTurnContext)
    graph.add_node("plan", plan_turn)
    graph.add_node("retrieve", retrieve)
    graph.add_node("check", check)
    graph.add_node("synthesize", synthesize)
    graph.add_node("transform_prior", transform_prior)
    graph.add_node("clarify", clarify)
    graph.add_node("record", record)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_after_plan)
    graph.add_edge("retrieve", "check")
    graph.add_conditional_edges("check", route_after_check)
    graph.add_edge("synthesize", "record")
    graph.add_edge("transform_prior", "record")
    graph.add_edge("clarify", "record")
    graph.add_edge("record", END)
    return graph.compile()


course_turn_graph = build_graph()


def new_course_conversation_state(
    *, course_id: str | UUID, conversation_id: str | UUID | None = None
) -> CourseConversationState:
    return CourseConversationState(
        conversation_id=str(conversation_id or uuid4()),
        course_id=str(course_id),
    )


@traced("video.course_conversation.execute_course_turn", flow="course_study")
def execute_course_turn(
    connection: Connection,
    question: str,
    state: CourseConversationState,
    *,
    owner_id: str | UUID,
    course_id: str | UUID,
    course_title: str,
    video_ids: list[UUID],
    dependencies: VideoAnswerDependencies | None = None,
    analysis_model: AnalysisModel | None = None,
    token_callback: TokenCallback | None = None,
) -> tuple[CourseTurnResult, CourseConversationState, dict[UUID, UUID]]:
    output = course_turn_graph.invoke(
        {"question": question.strip(), "conversation": state},
        config={
            "run_name": "course_turn",
            "tags": ["video", "course", "conversation", "rag"],
            "metadata": {
                "thread_id": state.conversation_id,
                "conversation_id": state.conversation_id,
                "course_id": str(course_id),
                "video_ids": [str(item) for item in video_ids],
            },
        },
        context=CourseTurnContext(
            connection=connection,
            owner_id=owner_id,
            course_id=course_id,
            course_title=course_title,
            video_ids=video_ids,
            dependencies=dependencies or VideoAnswerDependencies(),
            analysis_model=analysis_model,
            token_callback=token_callback,
        ),
    )
    retrieval = output.get("retrieval")
    versions = retrieval.versions if retrieval is not None else {
        UUID(item.video_id): UUID(item.ingestion_version_id)
        for item in output["result"].evidence
    }
    return output["result"], output["conversation"], versions


def _current_trace_id() -> str | None:
    try:
        from langsmith.run_helpers import get_current_run_tree

        tree = get_current_run_tree()
    except Exception:
        return None
    if tree is None:
        return None
    identifier: Any = getattr(tree, "trace_id", None) or getattr(tree, "id", None)
    return str(identifier) if identifier else None
