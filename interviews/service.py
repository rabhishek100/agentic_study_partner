"""Application service for creating and advancing interviews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from .contracts import (
    FormatChoice,
    InterviewClarification,
    InterviewClarificationDraft,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
    PythonCodingAnswer,
    SessionReport,
    TargetLevel,
)
from .evaluation import aggregate_metrics, resolve_citations
from .graph import AnswerGraphContext, answer_graph
from .models import invoke_structured, model_name, structured_model
from .planning import (
    InterviewSourceError,
    initial_checkpoint,
    load_source,
    next_topic,
    preflight,
    topic_by_key,
)
from .prompts import build_candidate_clarification_messages, prompt_version
from .question_generation import generate_question
from . import store


@dataclass(frozen=True)
class CreateInterview:
    source_kind: str
    book_id: int | None = None
    node_id: int | None = None
    video_id: str | UUID | None = None
    maximum_duration_minutes: int = 30
    target_level: TargetLevel = "mid"
    feedback_mode: str = "realistic"
    format_choice: FormatChoice = "auto"


def inspect_source(
    connection: Connection,
    *,
    owner_id: str | UUID,
    request: CreateInterview,
):
    source = load_source(
        connection,
        owner_id=owner_id,
        source_kind=request.source_kind,
        book_id=request.book_id,
        node_id=request.node_id,
        video_id=request.video_id,
    )
    return preflight(
        source,
        target_level=request.target_level,
        format_choice=request.format_choice,
    )


def create_interview(
    connection: Connection,
    *,
    owner_id: str | UUID,
    request: CreateInterview,
) -> InterviewSession:
    source = load_source(
        connection,
        owner_id=owner_id,
        source_kind=request.source_kind,
        book_id=request.book_id,
        node_id=request.node_id,
        video_id=request.video_id,
    )
    preview = preflight(
        source,
        target_level=request.target_level,
        format_choice=request.format_choice,
    )
    return store.create_session(
        connection,
        owner_id=owner_id,
        source_kind=request.source_kind,
        book_id=request.book_id,
        node_id=request.node_id,
        video_id=request.video_id,
        ingestion_version_id=source.ingestion_version_id,
        scope_key=preview.scope_key,
        title=f"Interview · {preview.title}",
        source_title=preview.source_title,
        interview_format=preview.selected_format,
        format_source=preview.format_source,
        feedback_mode=request.feedback_mode,
        target_level=request.target_level,
        maximum_duration_minutes=request.maximum_duration_minutes,
        estimated_min_minutes=preview.estimated_min_minutes,
        estimated_max_minutes=preview.estimated_max_minutes,
        checkpoint=initial_checkpoint(
            source.inventory,
            maximum_duration_minutes=request.maximum_duration_minutes,
            target_level=request.target_level,
        ),
        generation_model=model_name(),
        prompt_version=prompt_version(),
    )


def load_session_inventory(
    connection: Connection,
    session: InterviewSession,
    owner_id: str | UUID,
):
    return load_source(
        connection,
        owner_id=owner_id,
        source_kind=session.source_kind,
        book_id=session.book_id,
        node_id=session.node_id,
        video_id=session.video_id,
    ).inventory


def _generate_question(
    *,
    session: InterviewSession,
    inventory,
    topic,
    model: Any | None = None,
) -> tuple[InterviewQuestion, float]:
    planned_count = len(session.checkpoint.required_topics)
    return generate_question(
        inventory=inventory,
        topic=topic,
        interview_format=session.interview_format,
        target_level=session.target_level,
        kind="primary",
        recent_questions=[turn.question for turn in session.turns],
        purpose=(
            "This is the first-pass chapter coverage plan. Ask the most central "
            "reasoning question supported by this topic, not a narrow detail. "
            f"This topic is one of {planned_count} planned chapter areas."
        ),
        model=model,
    )


def start_interview(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    question_model: Any | None = None,
) -> InterviewSession:
    # Build the first question before starting the clock. If the provider is
    # unavailable, a ready/paused session remains resumable and accrues no time.
    session = store.load_session(connection, session_id, owner_id=owner_id)
    if session.status not in {"ready", "paused"}:
        raise store.InterviewStateError(
            "only a ready or paused interview can be started"
        )
    pending = next(
        (turn for turn in reversed(session.turns) if turn.answer_text is None), None
    )
    if pending is not None:
        return store.start_or_resume(connection, session_id, owner_id=owner_id)
    inventory = load_session_inventory(connection, session, owner_id)
    topic = next_topic(inventory, session.checkpoint)
    if topic is None:
        session.checkpoint.closing_reason = "All planned chapter areas were covered."
        return store.complete_session(
            connection,
            session_id,
            owner_id=owner_id,
            checkpoint=session.checkpoint,
            metrics=session.metrics,
        )
    question, cost = _generate_question(
        session=session, inventory=inventory, topic=topic, model=question_model
    )
    store.start_or_resume(connection, session_id, owner_id=owner_id)
    session.checkpoint.active_topic_key = topic.key
    store.save_checkpoint(
        connection, session_id, owner_id=owner_id, checkpoint=session.checkpoint
    )
    store.insert_question(
        connection,
        session_id,
        owner_id=owner_id,
        question=question,
        cost_usd=cost,
    )
    return store.load_session(connection, session_id, owner_id=owner_id)


def answer_interview(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    answer_text: str,
    coding_answer: PythonCodingAnswer | None = None,
    transcript_corrected: bool = False,
    evaluation_model: Any | None = None,
    question_model: Any | None = None,
) -> InterviewSession:
    session = store.load_session(connection, session_id, owner_id=owner_id)
    if session.status != "active":
        raise store.InterviewStateError("the interview must be active to answer")
    current = next(
        (turn for turn in reversed(session.turns) if turn.answer_text is None), None
    )
    if current is None:
        raise store.InterviewStateError("the interview has no unanswered question")
    expects_code = current.question.coding_exercise is not None
    if expects_code and coding_answer is None:
        raise ValueError("this coding question requires a submitted Python artifact")
    if not expects_code and coding_answer is not None:
        raise ValueError("this question does not accept a coding artifact")
    inventory = load_session_inventory(connection, session, owner_id)

    output = answer_graph.invoke(
        {
            "session": session,
            "inventory": inventory,
            "current_turn": current,
            "answer_text": answer_text.strip(),
            "coding_answer": coding_answer,
        },
        config={
            "run_name": "interview_answer_turn",
            "tags": ["interview", "adaptive", "rag"],
            "metadata": {
                "thread_id": session.session_id,
                "session_id": session.session_id,
                "scope_key": session.scope_key,
            },
        },
        context=AnswerGraphContext(
            evaluation_model=evaluation_model,
            question_model=question_model,
        ),
    )
    topic = topic_by_key(inventory, current.question.topic_key)
    evaluation = output["evaluation"]
    citations = resolve_citations(evaluation.citation_markers, topic)
    settled_preview = current.model_copy(
        update={
            "answer_text": answer_text.strip(),
            "coding_answer": coding_answer,
            "transcript_corrected": transcript_corrected,
            "evaluation": evaluation,
            "citations": citations,
            "web_sources": output["web_sources"],
            "cost_usd": current.cost_usd + output["evaluation_cost_usd"],
        }
    )
    previous = [turn for turn in session.turns if turn.evaluation is not None]
    metrics = aggregate_metrics([*previous, settled_preview], output["checkpoint"])

    with connection.transaction():
        store.settle_turn(
            connection,
            session_id,
            owner_id=owner_id,
            turn_index=current.turn_index,
            answer_text=answer_text,
            coding_answer=coding_answer,
            transcript_corrected=transcript_corrected,
            evaluation=evaluation,
            citations=citations,
            web_sources=output["web_sources"],
            checkpoint=output["checkpoint"],
            metrics=metrics,
            evaluation_cost_usd=output["evaluation_cost_usd"],
        )
        if output["next_question"] is not None:
            next_state = next(
                item
                for item in output["checkpoint"].topics
                if item.key == output["next_question"].topic_key
            )
            store.insert_question(
                connection,
                session_id,
                owner_id=owner_id,
                question=output["next_question"],
                hints_used=next_state.hints_used,
                cost_usd=output["question_cost_usd"],
            )
        else:
            store.complete_session(
                connection,
                session_id,
                owner_id=owner_id,
                checkpoint=output["checkpoint"],
                metrics=metrics,
            )
    return store.load_session(connection, session_id, owner_id=owner_id)


def reveal_coding_hint(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> InterviewSession:
    """Reveal one pre-generated hint in guided mode and charge independence."""

    session = store.load_session(connection, session_id, owner_id=owner_id)
    if session.status != "active":
        raise store.InterviewStateError("the interview must be active to use a hint")
    if session.feedback_mode != "guided":
        raise store.InterviewStateError(
            "coding hints are available only in guided interview mode"
        )
    current = next(
        (turn for turn in reversed(session.turns) if turn.answer_text is None), None
    )
    if current is None or current.question.coding_exercise is None:
        raise store.InterviewStateError("the active question is not a coding exercise")
    available = len(current.question.coding_exercise.hints)
    if current.hints_used >= available:
        raise store.InterviewStateError("all coding hints for this question are visible")

    checkpoint = session.checkpoint.model_copy(deep=True)
    topic_state = next(
        item for item in checkpoint.topics if item.key == current.question.topic_key
    )
    next_count = current.hints_used + 1
    topic_state.hints_used = min(2, topic_state.hints_used + 1)
    store.use_coding_hint(
        connection,
        session_id,
        owner_id=owner_id,
        turn_index=current.turn_index,
        hints_used=next_count,
        checkpoint=checkpoint,
    )
    return store.load_session(connection, session_id, owner_id=owner_id)


def clarify_interview_question(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    candidate_question: str,
    model: Any | None = None,
) -> InterviewSession:
    """Answer a candidate's task clarification without settling the turn."""

    session = store.load_session(connection, session_id, owner_id=owner_id)
    if session.status != "active":
        raise store.InterviewStateError(
            "the interview must be active to ask a clarification"
        )
    current = next(
        (turn for turn in reversed(session.turns) if turn.answer_text is None), None
    )
    if current is None:
        raise store.InterviewStateError("the interview has no unanswered question")
    if len(current.question.clarifications) >= 4:
        raise store.InterviewStateError(
            "this question has reached the clarification limit; answer or move on"
        )

    cleaned = " ".join(candidate_question.split())
    if not cleaned:
        raise ValueError("a clarification question is required")
    inventory = load_session_inventory(connection, session, owner_id)
    topic = topic_by_key(inventory, current.question.topic_key)
    draft, cost = invoke_structured(
        model or structured_model(InterviewClarificationDraft),
        build_candidate_clarification_messages(
            inventory=inventory,
            topic=topic,
            question=current.question,
            candidate_question=cleaned,
        ),
        InterviewClarificationDraft,
    )
    clarification = InterviewClarification(
        candidate_question=cleaned,
        interviewer_response=" ".join(draft.interviewer_response.split()),
    )
    store.append_question_clarification(
        connection,
        session_id,
        owner_id=owner_id,
        turn_index=current.turn_index,
        question=current.question,
        clarification=clarification,
        cost_usd=cost,
    )
    return store.load_session(connection, session_id, owner_id=owner_id)


def finish_interview(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> InterviewSession:
    session = store.load_session(connection, session_id, owner_id=owner_id)
    session.checkpoint.closing_reason = "The candidate ended the interview early."
    metrics = aggregate_metrics(session.turns, session.checkpoint)
    return store.complete_session(
        connection,
        session_id,
        owner_id=owner_id,
        checkpoint=session.checkpoint,
        metrics=metrics,
    )


def report_for(session: InterviewSession) -> SessionReport:
    required = session.checkpoint.required_topics
    missed = [topic.label for topic in required if not topic.completed]
    answered = [turn for turn in session.turns if turn.evaluation is not None]
    cited = sum(bool(turn.citations or turn.web_sources) for turn in answered)
    if not answered:
        confidence = "No answers were evaluated."
    elif cited == len(answered):
        confidence = "High: every evaluated turn has source or external provenance."
    else:
        confidence = (
            f"Moderate: {cited} of {len(answered)} evaluated turns carry "
            "resolvable provenance."
        )
    steps = [
        f"Revisit {label}." for label in session.metrics.revision_topics[:5]
    ]
    if missed:
        steps.append("Run a shorter follow-up interview over the missed topics.")
    return SessionReport(
        session=session,
        missed_topics=missed,
        evidence_confidence=confidence,
        suggested_next_steps=steps,
    )
