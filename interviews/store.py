"""Owner-scoped persistence and clock transitions for interview sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id

from .contracts import (
    AnswerEvaluation,
    InterviewCheckpoint,
    InterviewCitation,
    InterviewClarification,
    InterviewMetrics,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
    PythonCodingAnswer,
    ScreenObservation,
    WebSource,
)


class InterviewNotFoundError(LookupError):
    pass


class InterviewStateError(RuntimeError):
    pass


SESSION_COLUMNS = """
    id, source_kind, book_id, node_id, video_id, scope_key, title,
    source_title, interview_format, format_source, feedback_mode, target_level,
    maximum_duration_minutes, estimated_min_minutes, estimated_max_minutes,
    status, elapsed_seconds, active_since, started_at, completed_at, state_json,
    metrics_json, total_cost_usd, created_at, updated_at
"""


def _elapsed(row: dict[str, Any]) -> int:
    elapsed = int(row["elapsed_seconds"] or 0)
    if row["status"] == "active" and row["active_since"] is not None:
        elapsed += max(
            0,
            int((datetime.now(timezone.utc) - row["active_since"]).total_seconds()),
        )
    return elapsed


def _turn(row: dict[str, Any]) -> InterviewTurn:
    question = InterviewQuestion.model_validate(row["question_json"])
    # Older sessions may contain the emergency wording "source-grounded
    # point". Normalize it in memory so the displayed question, narration,
    # evaluation, and report all use the same reasoning-oriented scope.
    from .question_generation import (
        repair_legacy_recall_fallback,
        repair_nonvisual_work_sample,
        repair_numbered_fallback,
    )

    question = repair_nonvisual_work_sample(
        repair_numbered_fallback(repair_legacy_recall_fallback(question))
    )
    evaluation = (
        AnswerEvaluation.model_validate(row["evaluation_json"])
        if row["evaluation_json"]
        else None
    )
    return InterviewTurn(
        turn_index=row["turn_index"],
        question=question,
        answer_text=row["answer_text"],
        coding_answer=(
            PythonCodingAnswer.model_validate(row["coding_answer_json"])
            if row["coding_answer_json"]
            else None
        ),
        transcript_corrected=bool(row["transcript_corrected"]),
        evaluation=evaluation,
        citations=[
            InterviewCitation.model_validate(value)
            for value in row["citations_json"] or []
        ],
        web_sources=[WebSource.model_validate(value) for value in row["web_sources_json"] or []],
        screen_observation=(
            ScreenObservation.model_validate(row["screen_observation_json"])
            if row["screen_observation_json"]
            else None
        ),
        hints_used=row["hints_used"],
        available_coding_hints=(
            len(question.coding_exercise.hints) if question.coding_exercise else 0
        ),
        cost_usd=float(row["cost_usd"] or 0),
        created_at=row["created_at"],
        answered_at=row["answered_at"],
    )


def load_turns(
    connection: Connection, session_id: str | UUID, *, owner_id: str | UUID
) -> list[InterviewTurn]:
    rows = connection.execute(
        """
        select turn_index, question_json, answer_text, coding_answer_json,
               transcript_corrected,
               evaluation_json, citations_json, web_sources_json,
               screen_observation_json, hints_used, cost_usd, created_at,
               answered_at
        from public.interview_turns
        where session_id = %s and owner_id = %s
        order by turn_index
        """,
        (UUID(str(session_id)), parse_owner_id(owner_id)),
    ).fetchall()
    return [_turn(row) for row in rows]


def _session(
    connection: Connection,
    row: dict[str, Any],
    *,
    owner_id: str | UUID,
    include_turns: bool,
) -> InterviewSession:
    turns = load_turns(connection, row["id"], owner_id=owner_id) if include_turns else []
    return InterviewSession(
        session_id=str(row["id"]),
        source_kind=row["source_kind"],
        book_id=row["book_id"],
        node_id=row["node_id"],
        video_id=str(row["video_id"]) if row["video_id"] else None,
        scope_key=row["scope_key"],
        title=row["title"],
        source_title=row["source_title"],
        interview_format=row["interview_format"],
        format_source=row["format_source"],
        feedback_mode=row["feedback_mode"],
        target_level=row["target_level"],
        maximum_duration_minutes=row["maximum_duration_minutes"],
        estimated_min_minutes=row["estimated_min_minutes"],
        estimated_max_minutes=row["estimated_max_minutes"],
        status=row["status"],
        elapsed_seconds=_elapsed(row),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        checkpoint=InterviewCheckpoint.model_validate(row["state_json"] or {}),
        metrics=InterviewMetrics.model_validate(row["metrics_json"] or {}),
        total_cost_usd=float(row["total_cost_usd"] or 0),
        turns=turns,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_session(
    connection: Connection,
    *,
    owner_id: str | UUID,
    source_kind: str,
    book_id: int | None,
    node_id: int | None,
    video_id: str | UUID | None,
    ingestion_version_id: str | UUID | None,
    scope_key: str,
    title: str,
    source_title: str,
    interview_format: str,
    format_source: str,
    feedback_mode: str,
    target_level: str,
    maximum_duration_minutes: int,
    estimated_min_minutes: int,
    estimated_max_minutes: int,
    checkpoint: InterviewCheckpoint,
    generation_model: str,
    prompt_version: str,
) -> InterviewSession:
    owner = parse_owner_id(owner_id)
    row = connection.execute(
        f"""
        insert into public.interview_sessions (
            owner_id, source_kind, book_id, node_id, video_id,
            ingestion_version_id, scope_key, title, source_title,
            interview_format, format_source, feedback_mode, target_level,
            maximum_duration_minutes, estimated_min_minutes,
            estimated_max_minutes, state_json, generation_model, prompt_version
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s)
        returning {SESSION_COLUMNS}
        """,
        (
            owner,
            source_kind,
            book_id,
            node_id,
            UUID(str(video_id)) if video_id else None,
            UUID(str(ingestion_version_id)) if ingestion_version_id else None,
            scope_key,
            title,
            source_title,
            interview_format,
            format_source,
            feedback_mode,
            target_level,
            maximum_duration_minutes,
            estimated_min_minutes,
            estimated_max_minutes,
            Jsonb(checkpoint.model_dump(mode="json")),
            generation_model,
            prompt_version,
        ),
    ).fetchone()
    return _session(connection, row, owner_id=owner, include_turns=True)


def load_session(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    include_turns: bool = True,
    for_update: bool = False,
) -> InterviewSession:
    row = connection.execute(
        f"""
        select {SESSION_COLUMNS}
        from public.interview_sessions
        where id = %s and owner_id = %s
        {"for update" if for_update else ""}
        """,
        (UUID(str(session_id)), parse_owner_id(owner_id)),
    ).fetchone()
    if row is None:
        raise InterviewNotFoundError("interview session not found")
    return _session(connection, row, owner_id=owner_id, include_turns=include_turns)


def list_sessions(
    connection: Connection, *, owner_id: str | UUID, limit: int = 50
) -> list[InterviewSession]:
    rows = connection.execute(
        f"""
        select {SESSION_COLUMNS}
        from public.interview_sessions
        where owner_id = %s
        order by updated_at desc
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()
    return [
        _session(connection, row, owner_id=owner_id, include_turns=False)
        for row in rows
    ]


def start_or_resume(
    connection: Connection, session_id: str | UUID, *, owner_id: str | UUID
) -> InterviewSession:
    owner = parse_owner_id(owner_id)
    with connection.transaction():
        session = load_session(
            connection, session_id, owner_id=owner, include_turns=False, for_update=True
        )
        if session.status not in {"ready", "paused"}:
            raise InterviewStateError("only a ready or paused interview can be started")
        connection.execute(
            """
            update public.interview_sessions
            set status = 'active', active_since = now(),
                started_at = coalesce(started_at, now()), updated_at = now()
            where id = %s and owner_id = %s
            """,
            (UUID(str(session_id)), owner),
        )
    return load_session(connection, session_id, owner_id=owner)


def pause_session(
    connection: Connection, session_id: str | UUID, *, owner_id: str | UUID
) -> InterviewSession:
    owner = parse_owner_id(owner_id)
    with connection.transaction():
        session = load_session(
            connection, session_id, owner_id=owner, include_turns=False, for_update=True
        )
        if session.status != "active":
            raise InterviewStateError("only an active interview can be paused")
        connection.execute(
            """
            update public.interview_sessions
            set status = 'paused',
                elapsed_seconds = elapsed_seconds
                    + greatest(0, extract(epoch from (now() - active_since))::integer),
                active_since = null, updated_at = now()
            where id = %s and owner_id = %s
            """,
            (UUID(str(session_id)), owner),
        )
    return load_session(connection, session_id, owner_id=owner)


def insert_question(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    question: InterviewQuestion,
    screen_observation: ScreenObservation | None = None,
    hints_used: int = 0,
    cost_usd: float = 0,
) -> InterviewTurn:
    owner = parse_owner_id(owner_id)
    row = connection.execute(
        """
        insert into public.interview_turns (
            owner_id, session_id, turn_index, topic_key, question_kind,
            question_text, question_json, screen_observation_json,
            hints_used, cost_usd
        )
        select %s, %s, coalesce(max(turn_index) + 1, 0), %s, %s, %s,
               %s, %s, %s, %s
        from public.interview_turns
        where session_id = %s and owner_id = %s
        returning turn_index, question_json, answer_text, coding_answer_json,
                  transcript_corrected,
                  evaluation_json, citations_json, web_sources_json,
                  screen_observation_json, hints_used, cost_usd, created_at,
                  answered_at
        """,
        (
            owner,
            UUID(str(session_id)),
            question.topic_key,
            question.kind,
            question.text,
            Jsonb(question.model_dump(mode="json")),
            Jsonb(screen_observation.model_dump(mode="json")) if screen_observation else None,
            hints_used,
            cost_usd,
            UUID(str(session_id)),
            owner,
        ),
    ).fetchone()
    connection.execute(
        """
        update public.interview_sessions
        set total_cost_usd = total_cost_usd + %s, updated_at = now()
        where id = %s and owner_id = %s
        """,
        (cost_usd, UUID(str(session_id)), owner),
    )
    return _turn(row)


def settle_turn(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    turn_index: int,
    answer_text: str,
    coding_answer: PythonCodingAnswer | None,
    transcript_corrected: bool,
    evaluation: AnswerEvaluation,
    citations: list[InterviewCitation],
    web_sources: list[WebSource],
    checkpoint: InterviewCheckpoint,
    metrics: InterviewMetrics,
    evaluation_cost_usd: float,
) -> InterviewTurn:
    owner = parse_owner_id(owner_id)
    with connection.transaction():
        row = connection.execute(
            """
            update public.interview_turns
            set answer_text = %s, coding_answer_json = %s, transcript_corrected = %s,
                classification = %s, scores_json = %s, evaluation_json = %s,
                citations_json = %s, web_sources_json = %s,
                cost_usd = cost_usd + %s, answered_at = now()
            where session_id = %s and owner_id = %s and turn_index = %s
              and answer_text is null
            returning turn_index, question_json, answer_text, coding_answer_json,
                      transcript_corrected,
                      evaluation_json, citations_json, web_sources_json,
                      screen_observation_json, hints_used, cost_usd, created_at,
                      answered_at
            """,
            (
                answer_text.strip(),
                Jsonb(coding_answer.model_dump(mode="json")) if coding_answer else None,
                transcript_corrected,
                evaluation.classification,
                Jsonb(evaluation.scores.model_dump(mode="json")),
                Jsonb(evaluation.model_dump(mode="json")),
                Jsonb([item.model_dump(mode="json") for item in citations]),
                Jsonb([item.model_dump(mode="json") for item in web_sources]),
                evaluation_cost_usd,
                UUID(str(session_id)),
                owner,
                turn_index,
            ),
        ).fetchone()
        if row is None:
            raise InterviewStateError("that interview turn is already settled or missing")
        connection.execute(
            """
            update public.interview_sessions
            set state_json = %s, metrics_json = %s,
                total_cost_usd = total_cost_usd + %s, updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                Jsonb(checkpoint.model_dump(mode="json")),
                Jsonb(metrics.model_dump(mode="json")),
                evaluation_cost_usd,
                UUID(str(session_id)),
                owner,
            ),
        )
    return _turn(row)


def use_coding_hint(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    turn_index: int,
    hints_used: int,
    checkpoint: InterviewCheckpoint,
) -> None:
    owner = parse_owner_id(owner_id)
    with connection.transaction():
        result = connection.execute(
            """
            update public.interview_turns
            set hints_used = %s
            where session_id = %s and owner_id = %s and turn_index = %s
              and answer_text is null and hints_used < %s
            """,
            (
                hints_used,
                UUID(str(session_id)),
                owner,
                turn_index,
                hints_used,
            ),
        )
        if result.rowcount != 1:
            raise InterviewStateError("that coding hint is already visible or unavailable")
        connection.execute(
            """
            update public.interview_sessions
            set state_json = %s, updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                Jsonb(checkpoint.model_dump(mode="json")),
                UUID(str(session_id)),
                owner,
            ),
        )


def save_checkpoint(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    checkpoint: InterviewCheckpoint,
) -> None:
    connection.execute(
        """
        update public.interview_sessions
        set state_json = %s, updated_at = now()
        where id = %s and owner_id = %s
        """,
        (
            Jsonb(checkpoint.model_dump(mode="json")),
            UUID(str(session_id)),
            parse_owner_id(owner_id),
        ),
    )


def attach_screen_observation(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    turn_index: int,
    observation: ScreenObservation,
    checkpoint: InterviewCheckpoint,
    cost_usd: float,
) -> None:
    """Persist only the structured observation; the source image is ephemeral."""

    owner = parse_owner_id(owner_id)
    with connection.transaction():
        result = connection.execute(
            """
            update public.interview_turns
            set screen_observation_json = %s, cost_usd = cost_usd + %s
            where session_id = %s and owner_id = %s and turn_index = %s
              and answer_text is null
            """,
            (
                Jsonb(observation.model_dump(mode="json")),
                cost_usd,
                UUID(str(session_id)),
                owner,
                turn_index,
            ),
        )
        if result.rowcount != 1:
            raise InterviewStateError("screen checkpoints require an unanswered turn")
        connection.execute(
            """
            update public.interview_sessions
            set state_json = %s, total_cost_usd = total_cost_usd + %s,
                updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                Jsonb(checkpoint.model_dump(mode="json")),
                cost_usd,
                UUID(str(session_id)),
                owner,
            ),
        )


def append_question_clarification(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    turn_index: int,
    question: InterviewQuestion,
    clarification: InterviewClarification,
    cost_usd: float,
) -> None:
    """Persist a clarification inside the pending question's existing JSON."""

    owner = parse_owner_id(owner_id)
    updated_question = question.model_copy(
        update={"clarifications": [*question.clarifications, clarification]}
    )
    with connection.transaction():
        result = connection.execute(
            """
            update public.interview_turns
            set question_json = %s, cost_usd = cost_usd + %s
            where session_id = %s and owner_id = %s and turn_index = %s
              and answer_text is null
            """,
            (
                Jsonb(updated_question.model_dump(mode="json")),
                cost_usd,
                UUID(str(session_id)),
                owner,
                turn_index,
            ),
        )
        if result.rowcount != 1:
            raise InterviewStateError("clarification requires an unanswered turn")
        connection.execute(
            """
            update public.interview_sessions
            set total_cost_usd = total_cost_usd + %s, updated_at = now()
            where id = %s and owner_id = %s
            """,
            (cost_usd, UUID(str(session_id)), owner),
        )


def add_cost(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    cost_usd: float,
) -> None:
    if cost_usd <= 0:
        return
    connection.execute(
        """
        update public.interview_sessions
        set total_cost_usd = total_cost_usd + %s, updated_at = now()
        where id = %s and owner_id = %s
        """,
        (cost_usd, UUID(str(session_id)), parse_owner_id(owner_id)),
    )


def complete_session(
    connection: Connection,
    session_id: str | UUID,
    *,
    owner_id: str | UUID,
    checkpoint: InterviewCheckpoint,
    metrics: InterviewMetrics,
    abandoned: bool = False,
) -> InterviewSession:
    owner = parse_owner_id(owner_id)
    status = "abandoned" if abandoned else "completed"
    connection.execute(
        """
        update public.interview_sessions
        set status = %s,
            elapsed_seconds = elapsed_seconds + case
                when active_since is null then 0
                else greatest(0, extract(epoch from (now() - active_since))::integer)
            end,
            active_since = null, completed_at = now(),
            state_json = %s, metrics_json = %s, updated_at = now()
        where id = %s and owner_id = %s
          and status not in ('completed', 'abandoned')
        """,
        (
            status,
            Jsonb(checkpoint.model_dump(mode="json")),
            Jsonb(metrics.model_dump(mode="json")),
            UUID(str(session_id)),
            owner,
        ),
    )
    return load_session(connection, session_id, owner_id=owner)
