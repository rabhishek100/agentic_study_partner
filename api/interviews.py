"""FastAPI boundary for adaptive interview sessions and ephemeral media."""

from __future__ import annotations

import logging
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, model_validator
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from interviews import store
from interviews.contracts import (
    DURATION_OPTIONS,
    ContractModel,
    FormatChoice,
    InterviewMetrics,
    InterviewPreflight,
    InterviewSession,
    ScreenObservation,
    SessionReport,
    TargetLevel,
)
from interviews.evaluation import interviewer_reaction
from interviews.models import InterviewModelError
from interviews.planning import InterviewSourceError
from interviews.screen import (
    MAXIMUM_SCREEN_BYTES,
    ScreenCheckpointError,
    analyze_screen_checkpoint,
)
from interviews.service import (
    CreateInterview,
    answer_interview,
    create_interview,
    finish_interview,
    inspect_source,
    load_session_inventory,
    report_for,
    start_interview,
)
from interviews.speech import SpeechError, synthesize_interviewer_speech
from storage.database import connection as database_connection
from study.dictation import (
    MAXIMUM_QUESTION_BYTES,
    DictationError,
    audio_extension,
    transcribe_spoken_question_result,
)


router = APIRouter(prefix="/api/interviews", tags=["interviews"])
logger = logging.getLogger("study_partner.api.interviews")


class InterviewSetupRequest(ContractModel):
    source_kind: Literal["book", "video"]
    book_id: int | None = Field(default=None, gt=0)
    node_id: int | None = Field(default=None, gt=0)
    video_id: UUID | None = None
    maximum_duration_minutes: int = 30
    target_level: TargetLevel = "mid"
    feedback_mode: Literal["realistic", "guided"] = "realistic"
    interview_format: FormatChoice = "auto"

    @model_validator(mode="after")
    def exact_source_scope(self) -> "InterviewSetupRequest":
        if self.maximum_duration_minutes not in DURATION_OPTIONS:
            raise ValueError("unsupported interview duration")
        if self.source_kind == "book":
            if self.book_id is None or self.node_id is None:
                raise ValueError("a book interview requires a chapter")
            if self.video_id is not None:
                raise ValueError("a book interview cannot include a video")
        else:
            if self.video_id is None:
                raise ValueError("a video interview requires a lecture")
            if self.book_id is not None or self.node_id is not None:
                raise ValueError("a video interview cannot include a book chapter")
        return self

    def service_request(self) -> CreateInterview:
        return CreateInterview(
            source_kind=self.source_kind,
            book_id=self.book_id,
            node_id=self.node_id,
            video_id=self.video_id,
            maximum_duration_minutes=self.maximum_duration_minutes,
            target_level=self.target_level,
            feedback_mode=self.feedback_mode,
            format_choice=self.interview_format,
        )


class AnswerRequest(ContractModel):
    answer_text: str = Field(min_length=1, max_length=12_000)
    transcript_corrected: bool = False


class InterviewListResponse(ContractModel):
    sessions: list[InterviewSession]


class InterviewTranscriptionResponse(ContractModel):
    text: str
    cost_usd: float = Field(ge=0)


NOT_FOUND = HTTPException(status_code=404, detail="interview session not found")


def _public(session: InterviewSession) -> InterviewSession:
    """Expose a live reaction while hiding realistic-mode rubric details."""

    settled = session.status in {"completed", "abandoned"}
    realistic_live = not settled and session.feedback_mode == "realistic"
    turns = []
    for turn in session.turns:
        question = turn.question
        if not settled:
            question = question.model_copy(
                update={
                    "expected_points": [],
                    "suggested_answer": "",
                    "citation_markers": [],
                    "interviewer_note": "",
                }
            )
        hide_feedback = realistic_live and turn.evaluation is not None
        observation = turn.screen_observation
        if realistic_live and observation is not None:
            observation = ScreenObservation(summary="Screen checkpoint received.")
        reaction = interviewer_reaction(turn.evaluation) if turn.evaluation else ""
        public_turn = turn.model_copy(
            update={
                "question": question,
                "screen_observation": observation,
                "interviewer_reaction": reaction,
            }
        )
        if hide_feedback:
            public_turn = public_turn.model_copy(
                update={"evaluation": None, "citations": [], "web_sources": []}
            )
        turns.append(public_turn)
    metrics = (
        InterviewMetrics()
        if not settled and session.feedback_mode == "realistic"
        else session.metrics
    )
    checkpoint = session.checkpoint
    if realistic_live:
        checkpoint = checkpoint.model_copy(
            update={
                "topics": [
                    topic.model_copy(update={"label": f"Hidden topic {index + 1}"})
                    for index, topic in enumerate(checkpoint.topics)
                ],
                "screen_observation": (
                    ScreenObservation(summary="Screen checkpoint received.")
                    if checkpoint.screen_observation is not None
                    else None
                ),
            }
        )
    return session.model_copy(
        update={"turns": turns, "metrics": metrics, "checkpoint": checkpoint}
    )


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, store.InterviewNotFoundError):
        return NOT_FOUND
    if isinstance(error, (InterviewSourceError, ValueError)):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, store.InterviewStateError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, InterviewModelError):
        return HTTPException(status_code=502, detail=str(error))
    if isinstance(error, ScreenCheckpointError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="interview operation failed")


@router.post("/preflight", response_model=InterviewPreflight)
async def interview_preflight(
    request: InterviewSetupRequest,
    owner_id: UUID = Depends(current_owner),
) -> InterviewPreflight:
    try:
        service_request = request.service_request()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    def inspect():
        with database_connection(readonly=True) as connection:
            return inspect_source(connection, owner_id=owner_id, request=service_request)

    try:
        return await run_in_threadpool(inspect)
    except Exception as error:
        raise _translate(error) from error


@router.post("", response_model=InterviewSession, status_code=201)
async def create(
    request: InterviewSetupRequest,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    try:
        service_request = request.service_request()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    def make():
        with database_connection() as connection:
            return create_interview(
                connection, owner_id=owner_id, request=service_request
            )

    try:
        return _public(await run_in_threadpool(make))
    except Exception as error:
        raise _translate(error) from error


@router.get("", response_model=InterviewListResponse)
async def list_interviews(owner_id: UUID = Depends(current_owner)) -> InterviewListResponse:
    def load():
        with database_connection(readonly=True) as connection:
            return store.list_sessions(connection, owner_id=owner_id)

    return InterviewListResponse(
        sessions=[_public(session) for session in await run_in_threadpool(load)]
    )


@router.get("/{session_id}", response_model=InterviewSession)
async def get_interview(
    session_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    def load():
        with database_connection(readonly=True) as connection:
            return store.load_session(connection, session_id, owner_id=owner_id)

    try:
        return _public(await run_in_threadpool(load))
    except store.InterviewNotFoundError as error:
        raise NOT_FOUND from error


@router.post("/{session_id}/start", response_model=InterviewSession)
@router.post("/{session_id}/resume", response_model=InterviewSession)
async def start(
    session_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    def run():
        with database_connection() as connection:
            return start_interview(connection, session_id, owner_id=owner_id)

    try:
        return _public(await run_in_threadpool(run))
    except Exception as error:
        raise _translate(error) from error


@router.post("/{session_id}/pause", response_model=InterviewSession)
async def pause(
    session_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    def run():
        with database_connection() as connection:
            return store.pause_session(connection, session_id, owner_id=owner_id)

    try:
        return _public(await run_in_threadpool(run))
    except Exception as error:
        raise _translate(error) from error


@router.post("/{session_id}/answers", response_model=InterviewSession)
async def answer(
    session_id: UUID,
    request: AnswerRequest,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    def run():
        with database_connection() as connection:
            return answer_interview(
                connection,
                session_id,
                owner_id=owner_id,
                answer_text=request.answer_text,
                transcript_corrected=request.transcript_corrected,
            )

    try:
        return _public(await run_in_threadpool(run))
    except Exception as error:
        logger.exception("Interview answer failed", extra={"session_id": str(session_id)})
        raise _translate(error) from error


@router.post("/{session_id}/finish", response_model=InterviewSession)
async def finish(
    session_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    def run():
        with database_connection() as connection:
            return finish_interview(connection, session_id, owner_id=owner_id)

    try:
        return _public(await run_in_threadpool(run))
    except Exception as error:
        raise _translate(error) from error


@router.get("/{session_id}/report", response_model=SessionReport)
async def report(
    session_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> SessionReport:
    def load():
        with database_connection(readonly=True) as connection:
            session = store.load_session(connection, session_id, owner_id=owner_id)
            if session.status not in {"completed", "abandoned"}:
                raise store.InterviewStateError("finish the interview before opening its report")
            return report_for(session)

    try:
        return await run_in_threadpool(load)
    except Exception as error:
        raise _translate(error) from error


@router.post("/{session_id}/screen-checkpoints", response_model=InterviewSession)
async def screen_checkpoint(
    session_id: UUID,
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> InterviewSession:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAXIMUM_SCREEN_BYTES:
            raise HTTPException(status_code=413, detail="screen checkpoint is too large")

    def analyze():
        with database_connection() as connection:
            session = store.load_session(connection, session_id, owner_id=owner_id)
            if session.status != "active":
                raise store.InterviewStateError("screen checkpoints require an active interview")
            current = next(
                (turn for turn in reversed(session.turns) if turn.answer_text is None), None
            )
            if current is None:
                raise store.InterviewStateError("there is no active question")
            from interviews.planning import topic_by_key

            inventory = load_session_inventory(connection, session, owner_id)
            topic = topic_by_key(inventory, current.question.topic_key)
            observation, cost = analyze_screen_checkpoint(
                bytes(payload),
                media_type=media_type,
                question=current.question,
                topic=topic,
            )
            checkpoint = session.checkpoint.model_copy(
                update={"screen_observation": observation}
            )
            store.attach_screen_observation(
                connection,
                session_id,
                owner_id=owner_id,
                turn_index=current.turn_index,
                observation=observation,
                checkpoint=checkpoint,
                cost_usd=cost,
            )
            return store.load_session(connection, session_id, owner_id=owner_id)

    try:
        return _public(await run_in_threadpool(analyze))
    except Exception as error:
        raise _translate(error) from error


@router.post(
    "/{session_id}/transcriptions",
    response_model=InterviewTranscriptionResponse,
)
async def transcribe_answer(
    session_id: UUID,
    request: Request,
    owner_id: UUID = Depends(current_owner),
) -> InterviewTranscriptionResponse:
    """Transcribe one ephemeral answer clip with the low-cost interview model."""

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if audio_extension(media_type) is None:
        raise HTTPException(status_code=415, detail="answer must be recorded audio")
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAXIMUM_QUESTION_BYTES:
            raise HTTPException(status_code=413, detail="that answer recording is too long")
    if not payload:
        raise HTTPException(status_code=422, detail="the answer recording was empty")

    def transcribe():
        with database_connection() as connection:
            session = store.load_session(connection, session_id, owner_id=owner_id)
            if session.status != "active":
                raise store.InterviewStateError(
                    "answer transcription requires an active interview"
                )
            result = transcribe_spoken_question_result(
                bytes(payload),
                media_type=media_type,
                model=(
                    os.getenv("OPENROUTER_INTERVIEW_STT_MODEL")
                    or "openai/whisper-large-v3-turbo"
                ),
            )
            store.add_cost(
                connection,
                session_id,
                owner_id=owner_id,
                cost_usd=result.cost_usd,
            )
            return result

    try:
        result = await run_in_threadpool(transcribe)
    except DictationError as error:
        raise HTTPException(
            status_code=502,
            detail="answer transcription is unavailable; type your answer",
        ) from error
    except Exception as error:
        raise _translate(error) from error
    if not result.text:
        raise HTTPException(status_code=422, detail="no speech was recorded")
    return InterviewTranscriptionResponse(
        text=result.text,
        cost_usd=result.cost_usd,
    )


@router.get("/{session_id}/turns/{turn_index}/speech")
async def speech(
    session_id: UUID,
    turn_index: int,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    def synthesize():
        with database_connection() as connection:
            session = store.load_session(connection, session_id, owner_id=owner_id)
            turn = next(
                (item for item in session.turns if item.turn_index == turn_index),
                None,
            )
            if turn is None:
                raise store.InterviewStateError("interview question not found")
            audio = synthesize_interviewer_speech(turn.question.text)
            store.add_cost(
                connection,
                session_id,
                owner_id=owner_id,
                cost_usd=audio.cost_usd,
            )
            return audio

    try:
        audio = await run_in_threadpool(synthesize)
    except SpeechError as error:
        raise HTTPException(
            status_code=502,
            detail="interviewer voice is unavailable",
        ) from error
    except Exception as error:
        raise _translate(error) from error
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Interview-TTS-Model": audio.model,
            "X-Interview-TTS-Voice": audio.voice,
        },
    )


@router.get("/{session_id}/turns/{turn_index}/reaction-speech")
async def reaction_speech(
    session_id: UUID,
    turn_index: int,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    """Speak the settled answer reaction before the next question begins."""

    def synthesize():
        with database_connection() as connection:
            session = store.load_session(connection, session_id, owner_id=owner_id)
            turn = next(
                (item for item in session.turns if item.turn_index == turn_index),
                None,
            )
            if turn is None or turn.evaluation is None:
                raise store.InterviewStateError("interview reaction not found")
            audio = synthesize_interviewer_speech(
                interviewer_reaction(turn.evaluation)
            )
            store.add_cost(
                connection,
                session_id,
                owner_id=owner_id,
                cost_usd=audio.cost_usd,
            )
            return audio

    try:
        audio = await run_in_threadpool(synthesize)
    except SpeechError as error:
        raise HTTPException(
            status_code=502,
            detail="interviewer reaction voice is unavailable",
        ) from error
    except Exception as error:
        raise _translate(error) from error
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Interview-TTS-Model": audio.model,
            "X-Interview-TTS-Voice": audio.voice,
        },
    )
