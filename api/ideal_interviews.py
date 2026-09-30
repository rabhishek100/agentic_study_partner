"""FastAPI boundary for complete, listen-only chapter interview flows."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from interviews.contracts import ContractModel, FormatChoice, TargetLevel
from interviews.ideal_contracts import IdealInterviewFlow, IdealInterviewList
from interviews.ideal_livekit import (
    IdealVoiceConnection,
    IdealVoiceUnavailable,
    create_voice_connection,
)
from interviews.ideal_service import CreateIdealInterview, create_ideal_interview
from interviews import ideal_store
from interviews.models import InterviewModelError
from interviews.planning import InterviewSourceError
from storage.database import connection as database_connection


router = APIRouter(prefix="/api/ideal-interviews", tags=["ideal-interviews"])
logger = logging.getLogger("study_partner.api.ideal_interviews")


class CreateIdealInterviewRequest(ContractModel):
    book_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    target_level: TargetLevel = "mid"
    interview_format: FormatChoice = "auto"


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, ideal_store.IdealInterviewNotFoundError):
        return HTTPException(status_code=404, detail="ideal interview flow not found")
    if isinstance(error, (InterviewSourceError, ValueError)):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, InterviewModelError):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=500, detail="ideal interview operation failed")


@router.post("", response_model=IdealInterviewFlow, status_code=201)
async def create(
    request: CreateIdealInterviewRequest,
    owner_id: UUID = Depends(current_owner),
) -> IdealInterviewFlow:
    """Generate and save an ideal interview flow for the requested source scope."""

    def run():
        with database_connection() as connection:
            return create_ideal_interview(
                connection,
                owner_id=owner_id,
                request=CreateIdealInterview(
                    book_id=request.book_id,
                    node_id=request.node_id,
                    target_level=request.target_level,
                    format_choice=request.interview_format,
                ),
            )

    try:
        return await run_in_threadpool(run)
    except Exception as error:
        logger.exception("Ideal interview generation failed")
        raise _translate(error) from error


@router.get("", response_model=IdealInterviewList)
async def list_ideal_interviews(
    owner_id: UUID = Depends(current_owner),
) -> IdealInterviewList:
    """List the signed-in user’s saved ideal interview flows."""

    def load():
        with database_connection(readonly=True) as connection:
            return ideal_store.list_flows(connection, owner_id=owner_id)

    return IdealInterviewList(flows=await run_in_threadpool(load))


@router.get("/{flow_id}", response_model=IdealInterviewFlow)
async def get_flow(
    flow_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> IdealInterviewFlow:
    """Load an owned ideal interview flow and its generated exchanges."""

    def load():
        with database_connection(readonly=True) as connection:
            return ideal_store.load_flow(connection, flow_id, owner_id=owner_id)

    try:
        return await run_in_threadpool(load)
    except Exception as error:
        raise _translate(error) from error


@router.post("/{flow_id}/voice-connection", response_model=IdealVoiceConnection)
async def voice_connection(
    flow_id: UUID,
    response: Response,
    owner_id: UUID = Depends(current_owner),
) -> IdealVoiceConnection:
    """Create an optional voice playback connection for an owned ideal interview flow."""

    def connect():
        with database_connection(readonly=True) as connection:
            flow = ideal_store.load_flow(connection, flow_id, owner_id=owner_id)
        return create_voice_connection(flow, owner_id)

    response.headers["Cache-Control"] = "no-store"
    try:
        return await run_in_threadpool(connect)
    except IdealVoiceUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise _translate(error) from error
