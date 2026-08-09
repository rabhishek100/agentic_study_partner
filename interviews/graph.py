"""One inspectable LangGraph step for a candidate answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from decks.topics import ScopeInventory, Topic
from retrieval.web_search import search_web_sources

from .contracts import (
    AnswerEvaluation,
    InterviewCheckpoint,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
    WebSource,
)
from .evaluation import InterviewValidationError, sanitize_evaluation
from .models import invoke_structured, structured_model
from .planning import next_topic, topic_by_key
from .prompts import build_evaluation_messages
from .question_generation import (
    MAX_QUESTIONS_PER_TOPIC,
    generate_question,
    validate_question_progression,
)

DURATION_SOFT_STOP_SECONDS = 30


class AnswerGraphInput(TypedDict):
    session: InterviewSession
    inventory: ScopeInventory
    current_turn: InterviewTurn
    answer_text: str


class AnswerGraphState(AnswerGraphInput):
    evaluation: NotRequired[AnswerEvaluation]
    web_sources: NotRequired[list[WebSource]]
    checkpoint: NotRequired[InterviewCheckpoint]
    next_question: NotRequired[InterviewQuestion | None]
    evaluation_cost_usd: NotRequired[float]
    question_cost_usd: NotRequired[float]
    finish_reason: NotRequired[str | None]


class AnswerGraphOutput(TypedDict):
    evaluation: AnswerEvaluation
    web_sources: list[WebSource]
    checkpoint: InterviewCheckpoint
    next_question: InterviewQuestion | None
    evaluation_cost_usd: float
    question_cost_usd: float
    finish_reason: str | None


@dataclass(frozen=True)
class AnswerGraphContext:
    evaluation_model: object | None = None
    question_model: object | None = None


def _active_topic(state: AnswerGraphState) -> Topic:
    return topic_by_key(
        state["inventory"], state["current_turn"].question.topic_key
    )


def evaluate_answer(
    state: AnswerGraphState, runtime: Runtime[AnswerGraphContext]
) -> dict:
    session = state["session"]
    checkpoint = session.checkpoint
    topic = _active_topic(state)
    topic_state = next(item for item in checkpoint.topics if item.key == topic.key)
    client = runtime.context.evaluation_model or structured_model(AnswerEvaluation)
    evaluation, cost = invoke_structured(
        client,
        build_evaluation_messages(
            inventory=state["inventory"],
            topic=topic,
            question=state["current_turn"].question,
            answer=state["answer_text"],
            mode=session.feedback_mode,
            target_level=session.target_level,
            attempts=topic_state.attempts + 1,
            hints_used=topic_state.hints_used,
            screen_observation=state["current_turn"].screen_observation,
        ),
        AnswerEvaluation,
    )
    return {
        "evaluation": sanitize_evaluation(
            evaluation,
            question=state["current_turn"].question,
            topic=topic,
        ),
        "evaluation_cost_usd": cost,
        "web_sources": [],
    }


def verification_route(state: AnswerGraphState) -> str:
    return (
        "verify_extension"
        if state["evaluation"].needs_external_verification
        else "adapt"
    )


def verify_extension(
    state: AnswerGraphState, runtime: Runtime[AnswerGraphContext]
) -> dict:
    evaluation = state["evaluation"]
    found = search_web_sources(evaluation.external_query or state["answer_text"], 4)
    web_sources = [
        WebSource(
            title=item.title,
            url=item.url,
            snippet=item.snippet,
            rank=item.rank or index,
        )
        for index, item in enumerate(found, 1)
    ]
    if not web_sources:
        # No receipt, no score change. Keep the extension explicitly
        # provisional rather than treating model knowledge as verification.
        return {
            "web_sources": [],
            "evaluation": evaluation.model_copy(
                update={
                    "needs_external_verification": False,
                    "classification": "partially_correct",
                    "extension_summary": (
                        (evaluation.extension_summary or "Extension")
                        + " (not externally verified)"
                    ),
                }
            ),
        }
    session = state["session"]
    topic = _active_topic(state)
    topic_state = next(item for item in session.checkpoint.topics if item.key == topic.key)
    client = runtime.context.evaluation_model or structured_model(AnswerEvaluation)
    checked, cost = invoke_structured(
        client,
        build_evaluation_messages(
            inventory=state["inventory"],
            topic=topic,
            question=state["current_turn"].question,
            answer=state["answer_text"],
            mode=session.feedback_mode,
            target_level=session.target_level,
            attempts=topic_state.attempts + 1,
            hints_used=topic_state.hints_used,
            screen_observation=state["current_turn"].screen_observation,
            web_sources=web_sources,
        ),
        AnswerEvaluation,
    )
    return {
        "web_sources": web_sources,
        "evaluation": sanitize_evaluation(
            checked,
            question=state["current_turn"].question,
            topic=topic,
        ).model_copy(update={"needs_external_verification": False}),
        "evaluation_cost_usd": state.get("evaluation_cost_usd", 0) + cost,
    }


def adapt(state: AnswerGraphState) -> dict:
    session = state["session"]
    checkpoint = session.checkpoint.model_copy(deep=True)
    evaluation = state["evaluation"]
    topic = _active_topic(state)
    topic_state = next(item for item in checkpoint.topics if item.key == topic.key)
    topic_state.attempts += 1
    topic_state.best_score = max(
        topic_state.best_score, evaluation.scores.weighted_score
    )

    strong = (
        evaluation.scores.technical_correctness >= 4
        and evaluation.scores.depth_completeness >= 4
    )
    # One primary question plus at most one diagnostic follow-up. A weak topic
    # is recorded for revision, not allowed to consume the rest of the session.
    if (
        evaluation.topic_complete
        or strong
        or topic_state.attempts >= MAX_QUESTIONS_PER_TOPIC
    ):
        topic_state.completed = True
        checkpoint.strong_streak = checkpoint.strong_streak + 1 if strong else 0
    else:
        checkpoint.strong_streak = 0

    checkpoint.questions_asked += 1
    checkpoint.screen_observation = None
    checkpoint.active_topic_key = topic.key
    return {"checkpoint": checkpoint}


def next_route(state: AnswerGraphState) -> str:
    session = state["session"]
    checkpoint = state["checkpoint"]
    if session.elapsed_seconds >= (
        session.maximum_duration_minutes * 60 - DURATION_SOFT_STOP_SECONDS
    ):
        return "finish"
    current = next(
        item
        for item in checkpoint.topics
        if item.key == state["current_turn"].question.topic_key
    )
    if current.completed and next_topic(state["inventory"], checkpoint) is None:
        return "finish"
    return "compose_next"


def finish(state: AnswerGraphState) -> dict:
    session = state["session"]
    reason = (
        "The interview closed at the selected duration ceiling."
        if session.elapsed_seconds
        >= session.maximum_duration_minutes * 60 - DURATION_SOFT_STOP_SECONDS
        else "All substantive source topics were covered."
    )
    checkpoint = state["checkpoint"].model_copy(
        update={"closing_reason": reason, "active_topic_key": None}
    )
    return {
        "checkpoint": checkpoint,
        "next_question": None,
        "question_cost_usd": 0.0,
        "finish_reason": reason,
    }


def compose_next(
    state: AnswerGraphState, runtime: Runtime[AnswerGraphContext]
) -> dict:
    checkpoint = state["checkpoint"]
    current_topic = _active_topic(state)
    current_state = next(item for item in checkpoint.topics if item.key == current_topic.key)
    session = state["session"]
    evaluation = state["evaluation"]
    recent_questions = [turn.question for turn in session.turns]
    if (
        not recent_questions
        or recent_questions[-1].text != state["current_turn"].question.text
    ):
        recent_questions.append(state["current_turn"].question)

    if not current_state.completed:
        topic = current_topic
        if current_state.attempts == 1 and evaluation.needs_clarifying_probe:
            question = InterviewQuestion(
                topic_key=topic.key,
                topic_label=topic.label,
                kind="clarifying",
                text=evaluation.clarifying_probe or "Could you make that more specific?",
                expected_points=state["current_turn"].question.expected_points,
                suggested_answer=state["current_turn"].question.suggested_answer,
                citation_markers=state["current_turn"].question.citation_markers,
                difficulty=session.target_level,
                interviewer_note="Clarify before applying the rubric.",
            )
            try:
                question = validate_question_progression(question, recent_questions)
                cost = 0.0
            except InterviewValidationError:
                current_state.hints_used = 1
                question, cost = generate_question(
                    inventory=state["inventory"],
                    topic=topic,
                    interview_format=session.interview_format,
                    target_level=session.target_level,
                    kind="hint",
                    recent_questions=recent_questions,
                    prior_question=state["current_turn"].question,
                    candidate_answer=state["answer_text"],
                    purpose=(
                        "Ask one final diagnostic question from a different angle. "
                        "Do not restate the previous question; the interview advances "
                        "to the next topic after this answer."
                    ),
                    model=runtime.context.question_model,
                )
        else:
            current_state.hints_used = 1
            question, cost = generate_question(
                inventory=state["inventory"],
                topic=topic,
                interview_format=session.interview_format,
                target_level=session.target_level,
                kind="hint",
                recent_questions=recent_questions,
                prior_question=state["current_turn"].question,
                candidate_answer=state["answer_text"],
                purpose=(
                    "Give one concise hint, then ask one final focused question "
                    "from a materially different angle. The interview advances to "
                    "the next topic after this answer."
                ),
                model=runtime.context.question_model,
            )
    else:
        topic = next_topic(state["inventory"], checkpoint)
        assert topic is not None
        checkpoint.active_topic_key = topic.key
        purpose = None
        if checkpoint.strong_streak >= 2:
            purpose = (
                "The candidate answered the last two topics strongly. Keep this topic "
                "grounded in its evidence, but ask for a deeper edge case, failure "
                "mode, or trade-off appropriate to the target level."
            )
        question, cost = generate_question(
            inventory=state["inventory"],
            topic=topic,
            interview_format=session.interview_format,
            target_level=session.target_level,
            kind="primary",
            recent_questions=recent_questions,
            purpose=purpose,
            model=runtime.context.question_model,
        )

    return {
        "checkpoint": checkpoint,
        "next_question": question,
        "question_cost_usd": cost,
        "finish_reason": None,
    }


def build_answer_graph():
    builder = StateGraph(
        AnswerGraphState,
        input_schema=AnswerGraphInput,
        output_schema=AnswerGraphOutput,
        context_schema=AnswerGraphContext,
    )
    builder.add_node("evaluate", evaluate_answer)
    builder.add_node("verify_extension", verify_extension)
    builder.add_node("adapt", adapt)
    builder.add_node("finish", finish)
    builder.add_node("compose_next", compose_next)
    builder.add_edge(START, "evaluate")
    builder.add_conditional_edges("evaluate", verification_route)
    builder.add_edge("verify_extension", "adapt")
    builder.add_conditional_edges("adapt", next_route)
    builder.add_edge("finish", END)
    builder.add_edge("compose_next", END)
    return builder.compile()


answer_graph = build_answer_graph()
