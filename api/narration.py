"""Reading an answer aloud: spoken figure descriptions, and synthesised speech.

Two endpoints, deliberately generic rather than one pair per chat surface.
The narration script is assembled in the client — it is the client that holds
the answer text on every surface, and turning stored prose into something a
person can take in is what the interface already does for the eye. What has to
happen on the server is what needs the corpus and the provider key: describing
a figure, and speaking a passage.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from api.documentation import (
    binary_responses,
)
from narration import cache
from narration.figures import (
    MAXIMUM_FIGURES_PER_REQUEST,
    FigureRequest,
    spoken_descriptions,
)
from narration.synthesis import (
    MAXIMUM_TTS_CHARACTERS,
    SpeechError,
    configured_model,
    configured_voice,
    synthesize_speech,
)
from narration.livekit_voice import (
    VoiceConnection,
    VoiceUnavailable,
    create_voice_connection,
)
from storage.conversations import load_conversation
from storage.database import connection as database_connection


logger = logging.getLogger("study_partner.api.narration")

router = APIRouter(prefix="/api/narration", tags=["narration"])


class FigureIdentity(BaseModel):
    book_id: int = Field(ge=1)
    block_id: int = Field(ge=1)


class FigureNarrationRequest(BaseModel):
    figures: list[FigureIdentity] = Field(
        default_factory=list, max_length=MAXIMUM_FIGURES_PER_REQUEST
    )


class FigureNarrationResponse(BaseModel):
    """Descriptions by block id, keyed as strings because JSON keys are strings.

    A figure with no entry has no description to speak — it was judged
    decorative at ingest, or the provider is down. The client still announces
    the figure; it just has nothing to say about it, which is the honest
    outcome and not an error.
    """

    descriptions: dict[str, str]


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAXIMUM_TTS_CHARACTERS)


@router.post("/voice-connections/{conversation_id}", response_model=VoiceConnection)
async def narration_voice_connection(
    conversation_id: UUID,
    owner_id: UUID = Depends(current_owner),
) -> VoiceConnection:
    """Mint a microphone-only room token after conversation ownership checks."""

    def create() -> VoiceConnection:
        with database_connection(readonly=True) as connection:
            conversation = load_conversation(
                connection, conversation_id, owner_id=owner_id
            )
        if conversation is None or conversation["parent_conversation_id"] is not None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return create_voice_connection(conversation_id, owner_id)

    try:
        return await run_in_threadpool(create)
    except VoiceUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/figures", response_model=FigureNarrationResponse)
async def figure_narration(
    request: FigureNarrationRequest,
    owner_id: UUID = Depends(current_owner),
) -> FigureNarrationResponse:
    """Return available spoken descriptions for owned figure blocks, keyed by block ID. A missing entry means no description is available."""

    def run() -> dict[int, str]:
        with database_connection() as connection:
            return spoken_descriptions(
                connection,
                owner_id=owner_id,
                figures=[
                    FigureRequest(book_id=figure.book_id, block_id=figure.block_id)
                    for figure in request.figures
                ],
            )

    described = await run_in_threadpool(run)
    return FigureNarrationResponse(
        descriptions={str(block_id): text for block_id, text in described.items()}
    )


@router.post(
    "/speech",
    response_class=Response,
    responses=binary_responses("audio/*", description="Synthesized read-aloud audio"),
)
async def speech(
    request: SpeechRequest,
    owner_id: UUID = Depends(current_owner),
) -> Response:
    """Speak one chunk of a narration script.

    Chunks arrive one at a time rather than as a whole answer because the wait
    a listener notices is the wait for the first sound. The client plays chunk
    one while chunk two is still being synthesised.
    """

    spoken = " ".join(request.text.split())
    if not spoken:
        raise HTTPException(status_code=422, detail="there is nothing to speak")

    model = configured_model("reading")
    voice = configured_voice("reading")
    key = cache.cache_key(spoken, model=model, voice=voice)

    def cached() -> cache.CachedAudio | None:
        with database_connection() as connection:
            return cache.load(connection, owner_id=owner_id, content_hash=key)

    hit = await run_in_threadpool(cached)
    if hit is not None:
        return _audio_response(hit.content, hit.media_type, model, voice, cached=True)

    def synthesize() -> tuple[bytes, str]:
        audio = synthesize_speech(spoken, purpose="reading", model=model, voice=voice)
        with database_connection() as connection:
            cache.store(
                connection,
                owner_id=owner_id,
                content_hash=key,
                audio=audio.content,
                media_type=audio.media_type,
                model_name=audio.model,
                voice=audio.voice,
                character_count=len(spoken),
                cost_usd=audio.cost_usd,
            )
        return audio.content, audio.media_type

    try:
        content, media_type = await run_in_threadpool(synthesize)
    except SpeechError as error:
        logger.warning("narration speech failed: %s", error)
        raise HTTPException(
            status_code=502, detail="the reading voice is unavailable"
        ) from error
    return _audio_response(content, media_type, model, voice, cached=False)


def _audio_response(
    content: bytes, media_type: str, model: str, voice: str, *, cached: bool
) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            # Private and revalidated: the audio is one owner's, and the cache
            # that matters is the server's, which is shared across sessions
            # and devices in a way a browser cache is not.
            "Cache-Control": "private, no-store",
            "X-Narration-Model": model,
            "X-Narration-Voice": voice,
            "X-Narration-Cache": "hit" if cached else "miss",
        },
    )
