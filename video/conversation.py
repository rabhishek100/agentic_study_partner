"""One inspectable LangGraph turn over a single lecture.

The graph is deliberately explicit — plan, retrieve, check sufficiency, retry
once, synthesize, record — because the point of the workflow is that a reader
(or an interviewer) can follow what it decided and why. The same shape already
answers book questions; the video version adds the modality-aware sufficiency
check, which is where lecture questions fail differently: a question about
something drawn on the board can retrieve plenty of transcript and still have
no evidence of the drawing.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, NotRequired, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from psycopg import Connection

from study.streaming import TokenCallback
from video.analyze import AnalysisModel, analyze_turn
from video.answers import (
    RetrievedTurn,
    VideoAnswerDependencies,
    retrieve_turn_evidence,
    synthesize_answer,
)
from video.contracts import (
    VideoConversationState,
    VideoEvidenceRef,
    VideoMessage,
    VideoTurnDecision,
    VideoTurnResult,
)
from video.prompts import format_timestamp
from video.lecture import (
    NoTranscriptError,
    inventory_topics,
    load_chapters,
    load_lecture_scope,
    summarize_lecture,
)


DEFAULT_EVIDENCE_LIMIT = 8
BROADENED_EVIDENCE_LIMIT = 16
DEFAULT_TIMELINE_WINDOW_MS = 60_000
BROADENED_TIMELINE_WINDOW_MS = 180_000
# Words that ask about something seen rather than said. A lecture question
# using them needs visual or document evidence to be answerable at all.
VISUAL_QUESTION = re.compile(
    r"\b(diagram|drawing|draws?|drew|slide|figure|chart|graph|plot|screen|"
    r"shown|shows|showed|picture|image|board|whiteboard|wrote|writes|"
    r"visual|equation|code)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VideoTurnContext:
    connection: Connection
    owner_id: str | UUID
    video_id: str | UUID
    video_title: str
    dependencies: VideoAnswerDependencies
    analysis_model: AnalysisModel | None = None
    token_callback: TokenCallback | None = None
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT


class VideoGraphInput(TypedDict):
    question: str
    conversation: VideoConversationState


class VideoGraphState(VideoGraphInput):
    decision: NotRequired[VideoTurnDecision]
    retrieval: NotRequired[RetrievedTurn]
    attempts: NotRequired[int]
    sufficient: NotRequired[bool]
    sufficiency_reason: NotRequired[str]
    result: NotRequired[VideoTurnResult]


class VideoGraphOutput(TypedDict):
    conversation: VideoConversationState
    result: VideoTurnResult


def evaluate_sufficiency(
    question: str, evidence: list[VideoEvidenceRef]
) -> tuple[bool, str]:
    """Judge the retrieved set before paying to generate an answer from it."""

    if not evidence:
        return False, "No evidence matched the question."
    if VISUAL_QUESTION.search(question) and not any(
        item.is_visual or item.modality == "resource_page" for item in evidence
    ):
        return (
            False,
            "The question asks about something shown, but only spoken "
            "evidence matched.",
        )
    return True, f"{len(evidence)} evidence items across the lecture's modalities."


def plan_turn(state: VideoGraphState, runtime: Runtime[VideoTurnContext]) -> dict:
    return {
        "decision": analyze_turn(
            state["question"],
            state["conversation"],
            model=runtime.context.analysis_model,
        ),
        "attempts": 0,
    }


def route_after_plan(state: VideoGraphState) -> str:
    route = state["decision"].route
    if route == "clarify":
        return "clarify"
    if route == "prior_answer_transform":
        return "transform_prior"
    if route in {"lecture_summary", "topic_inventory"}:
        return "whole_lecture"
    return "retrieve_evidence"


def whole_lecture(
    state: VideoGraphState, runtime: Runtime[VideoTurnContext]
) -> dict:
    """Answer from the complete transcript rather than from a search of it.

    Retrieval is skipped entirely here, so there is no sufficiency check to
    make: the evidence is the whole lecture by construction, and the only way
    it can be insufficient is for the lecture to have no transcript at all.
    """

    context = runtime.context
    decision = state["decision"]
    question = state["question"]
    try:
        scope = load_lecture_scope(
            context.connection,
            owner_id=context.owner_id,
            video_id=context.video_id,
        )
    except NoTranscriptError as error:
        return {
            "result": VideoTurnResult(
                question=question,
                answer=f"Insufficient evidence: {error}.",
                route=decision.route,
                history_dependency=decision.history_dependency,
                standalone_query=decision.standalone_query,
                outcome="abstain",
                retrieval_attempts=0,
                routing_reason=decision.reason,
                trace_id=_current_trace_id(),
            )
        }

    chapters = load_chapters(
        context.connection,
        owner_id=context.owner_id,
        video_id=context.video_id,
    )
    produce = (
        summarize_lecture if decision.route == "lecture_summary" else inventory_topics
    )
    draft = produce(
        question=question,
        scope=scope,
        video_title=context.video_title,
        chapters=chapters,
        dependencies=context.dependencies,
        token_callback=context.token_callback,
    )
    # Only the windows the answer actually cited are carried as evidence. All
    # of them were supplied, but listing two dozen transcript windows as
    # "sources" would bury the handful a claim rests on.
    cited = {citation.evidence_rank for citation in draft.citations}
    return {
        "result": VideoTurnResult(
            question=question,
            answer=draft.answer,
            route=decision.route,
            history_dependency=decision.history_dependency,
            standalone_query=decision.standalone_query,
            evidence=[
                window for window in scope.windows if window.rank in cited
            ],
            citations=draft.citations,
            visual_cards=draft.visual_cards,
            outcome=draft.outcome,
            ingestion_version_id=str(scope.version_id),
            retrieval_attempts=0,
            sufficiency_reason=(
                f"The complete transcript, in {len(scope.windows)} windows "
                f"across {format_timestamp(scope.duration_ms)}."
            ),
            routing_reason=decision.reason,
            cost_usd=draft.cost_usd,
            trace_id=_current_trace_id(),
        )
    }


def retrieve_evidence(
    state: VideoGraphState, runtime: Runtime[VideoTurnContext]
) -> dict:
    context = runtime.context
    attempts = state.get("attempts", 0) + 1
    broadened = attempts > 1
    retrieved = retrieve_turn_evidence(
        context.connection,
        owner_id=context.owner_id,
        video_id=context.video_id,
        query=state["decision"].standalone_query or state["question"],
        limit=BROADENED_EVIDENCE_LIMIT if broadened else context.evidence_limit,
        timeline_window_ms=(
            BROADENED_TIMELINE_WINDOW_MS if broadened else DEFAULT_TIMELINE_WINDOW_MS
        ),
        dependencies=context.dependencies,
    )
    return {"retrieval": retrieved, "attempts": attempts}


def check_sufficiency(state: VideoGraphState) -> dict:
    sufficient, reason = evaluate_sufficiency(
        state["question"], state["retrieval"].evidence
    )
    return {"sufficient": sufficient, "sufficiency_reason": reason}


def route_after_sufficiency(state: VideoGraphState) -> str:
    # Exactly one broadened retry: a second miss means the lecture does not
    # contain it, and repeating the search would only cost more.
    if state["sufficient"] or state.get("attempts", 1) >= 2:
        return "synthesize"
    return "retrieve_evidence"


def synthesize(state: VideoGraphState, runtime: Runtime[VideoTurnContext]) -> dict:
    context = runtime.context
    decision = state["decision"]
    retrieved: RetrievedTurn = state["retrieval"]
    draft = synthesize_answer(
        context.connection,
        owner_id=context.owner_id,
        question=state["question"],
        evidence=retrieved.evidence,
        video_title=context.video_title,
        state=state["conversation"],
        dependencies=context.dependencies,
        token_callback=context.token_callback,
    )
    return {
        "result": VideoTurnResult(
            question=state["question"],
            answer=draft.answer,
            route="evidence_qa",
            history_dependency=decision.history_dependency,
            standalone_query=decision.standalone_query,
            evidence=retrieved.evidence,
            citations=draft.citations,
            visual_cards=draft.visual_cards,
            outcome=draft.outcome,
            ingestion_version_id=str(retrieved.version_id),
            retrieval_attempts=state.get("attempts", 1),
            sufficiency_reason=state.get("sufficiency_reason"),
            routing_reason=decision.reason,
            cost_usd=draft.cost_usd,
            trace_id=_current_trace_id(),
        )
    }


def transform_prior(
    state: VideoGraphState, runtime: Runtime[VideoTurnContext]
) -> dict:
    """Reshape the previous answer without letting new facts in."""

    conversation = state["conversation"]
    decision = state["decision"]
    draft = synthesize_answer(
        runtime.context.connection,
        owner_id=runtime.context.owner_id,
        question=(
            f"{state['question']}\n\nReshape the previous answer only. Add no "
            "facts and keep every citation marker."
        ),
        evidence=list(conversation.previous_evidence),
        video_title=runtime.context.video_title,
        state=conversation,
        dependencies=runtime.context.dependencies,
        token_callback=runtime.context.token_callback,
    )
    return {
        "result": VideoTurnResult(
            question=state["question"],
            answer=draft.answer,
            route="prior_answer_transform",
            history_dependency=decision.history_dependency,
            standalone_query=decision.standalone_query,
            evidence=list(conversation.previous_evidence),
            citations=draft.citations or list(conversation.previous_citations),
            visual_cards=draft.visual_cards,
            outcome=draft.outcome,
            retrieval_attempts=0,
            routing_reason=decision.reason,
            cost_usd=draft.cost_usd,
            trace_id=_current_trace_id(),
        )
    }


def clarify(state: VideoGraphState) -> dict:
    decision = state["decision"]
    return {
        "result": VideoTurnResult(
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


def record_turn_node(state: VideoGraphState) -> dict:
    return {
        "conversation": record_turn(
            state["conversation"], state["question"], state["result"]
        )
    }


def record_turn(
    state: VideoConversationState,
    question: str,
    result: VideoTurnResult,
) -> VideoConversationState:
    updated = state.model_copy(deep=True)
    turn = sum(message.role == "user" for message in updated.messages) + 1
    turn_id = f"{updated.conversation_id}-t{turn}"
    updated.messages.extend(
        [
            VideoMessage(role="user", content=question, turn_id=turn_id),
            VideoMessage(role="assistant", content=result.answer, turn_id=turn_id),
        ]
    )
    updated.pending_clarification = (
        question if result.route == "clarify" else None
    )
    updated.previous_route = result.route
    if result.outcome in {"answer", "abstain"}:
        updated.previous_answer = result.answer
        updated.previous_evidence = list(result.evidence)
        updated.previous_citations = list(result.citations)
    return updated


def build_video_turn_graph():
    builder = StateGraph(
        VideoGraphState,
        input_schema=VideoGraphInput,
        output_schema=VideoGraphOutput,
        context_schema=VideoTurnContext,
    )
    builder.add_node("plan_turn", plan_turn)
    builder.add_node("whole_lecture", whole_lecture)
    builder.add_node("retrieve_evidence", retrieve_evidence)
    builder.add_node("check_sufficiency", check_sufficiency)
    builder.add_node("synthesize", synthesize)
    builder.add_node("transform_prior", transform_prior)
    builder.add_node("clarify", clarify)
    builder.add_node("record_turn", record_turn_node)

    builder.add_edge(START, "plan_turn")
    builder.add_conditional_edges("plan_turn", route_after_plan)
    builder.add_edge("retrieve_evidence", "check_sufficiency")
    builder.add_conditional_edges("check_sufficiency", route_after_sufficiency)
    builder.add_edge("whole_lecture", "record_turn")
    builder.add_edge("synthesize", "record_turn")
    builder.add_edge("transform_prior", "record_turn")
    builder.add_edge("clarify", "record_turn")
    builder.add_edge("record_turn", END)
    return builder.compile()


video_turn_graph = build_video_turn_graph()


def new_video_conversation_state(
    *, video_id: str | UUID, conversation_id: str | UUID | None = None
) -> VideoConversationState:
    return VideoConversationState(
        conversation_id=str(conversation_id or uuid4()),
        video_id=str(video_id),
    )


def execute_video_turn(
    connection: Connection,
    question: str,
    state: VideoConversationState,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    video_title: str,
    dependencies: VideoAnswerDependencies | None = None,
    analysis_model: AnalysisModel | None = None,
    token_callback: TokenCallback | None = None,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
) -> tuple[VideoTurnResult, VideoConversationState]:
    """Run one traced turn and return the result with the updated state."""

    output = video_turn_graph.invoke(
        {"question": question.strip(), "conversation": state},
        config={
            "run_name": "video_turn",
            "tags": ["video", "conversation", "rag"],
            "metadata": {
                "thread_id": state.conversation_id,
                "conversation_id": state.conversation_id,
                "video_id": str(video_id),
            },
        },
        context=VideoTurnContext(
            connection=connection,
            owner_id=owner_id,
            video_id=video_id,
            video_title=video_title,
            dependencies=dependencies or VideoAnswerDependencies(),
            analysis_model=analysis_model,
            token_callback=token_callback,
            evidence_limit=evidence_limit,
        ),
    )
    return output["result"], output["conversation"]


def _current_trace_id() -> str | None:
    """Return the LangSmith run this turn belongs to, when tracing is on."""

    try:
        from langsmith.run_helpers import get_current_run_tree

        tree = get_current_run_tree()
    except Exception:  # noqa: BLE001 - tracing is optional, never load-bearing
        return None
    if tree is None:
        return None
    identifier: Any = getattr(tree, "trace_id", None) or getattr(tree, "id", None)
    return str(identifier) if identifier else None
