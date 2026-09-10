"""Owner-scoped LiveKit admission and persisted interviewer utterances.

The SDK import is lazy so the default HTTP transport needs no voice extra.
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from interviews.contracts import ContractModel, InterviewSession
from interviews.evaluation import interviewer_reaction
from interviews.speech import spoken_question_text
from interviews.store import InterviewStateError


AGENT_NAME = "interview-voice"
RPC_METHOD = "interview.voice"
EVENT_TOPIC = "interview.voice"


class VoiceUnavailable(RuntimeError):
    pass


class VoiceConnection(ContractModel):
    server_url: str
    participant_token: str
    room_name: str
    participant_identity: str


class VoiceBinding(ContractModel):
    session_id: UUID
    owner_id: UUID
    participant_identity: str
    room_name: str


class VoiceCommand(ContractModel):
    action: Literal["listen", "stop_listening", "speak", "stop_speaking", "flush"]
    epoch: int = Field(default=0, ge=0)
    request_id: str = Field(default="", max_length=80)
    turn_index: int = Field(default=0, ge=0)
    utterance: Literal["question", "reaction", "clarification"] = "question"
    clarification_index: int | None = Field(default=None, ge=0)


def create_voice_connection(session: InterviewSession, owner_id: UUID) -> VoiceConnection:
    if os.getenv("INTERVIEW_LIVEKIT_ENABLED", "").lower() != "true":
        raise VoiceUnavailable("LiveKit interview voice is disabled")
    settings = {name: os.getenv(name, "").strip() for name in (
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    )}
    if not all(settings.values()):
        raise VoiceUnavailable("LiveKit interview voice is not configured")
    # A completed session may reconnect solely to finish its final reaction.
    # The worker still rejects capture and question playback unless active.
    if session.status == "ready":
        raise InterviewStateError("start the interview before connecting voice")
    try:
        from livekit import api
    except ImportError as error:
        raise VoiceUnavailable("install the voice extra to enable LiveKit") from error

    # Each connection gets its own room; tabs cannot share a mic or speech worker.
    nonce = uuid4().hex
    binding = VoiceBinding(
        session_id=session.session_id,
        owner_id=owner_id,
        participant_identity=f"candidate-{nonce}",
        room_name=f"interview-{session.session_id}-{nonce}",
    )
    token = (
        api.AccessToken(settings["LIVEKIT_API_KEY"], settings["LIVEKIT_API_SECRET"])
        .with_identity(binding.participant_identity)
        .with_ttl(timedelta(minutes=10))
        .with_grants(api.VideoGrants(
            room_join=True, room=binding.room_name,
            can_publish=True, can_publish_sources=["microphone"],
            can_subscribe=True, can_publish_data=True,
            can_update_own_metadata=False,
        ))
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(
            agent_name=AGENT_NAME, metadata=binding.model_dump_json(),
        )], empty_timeout=60, departure_timeout=20))
        .to_jwt()
    )
    return VoiceConnection(
        server_url=settings["LIVEKIT_URL"], participant_token=token,
        room_name=binding.room_name, participant_identity=binding.participant_identity,
    )


def saved_utterance(session: InterviewSession, command: VoiceCommand) -> str:
    """Only narrate the active question or latest answer's public transition."""
    turn = next((t for t in session.turns if t.turn_index == command.turn_index), None)
    if turn is None:
        raise InterviewStateError("interview turn not found")
    pending = next((t for t in reversed(session.turns) if t.answer_text is None), None)
    if command.utterance == "reaction":
        settled = [t for t in session.turns if t.evaluation is not None]
        if not settled or turn != settled[-1] or turn.evaluation is None:
            raise InterviewStateError("only the latest answer reaction can be spoken")
        return interviewer_reaction(
            turn.evaluation, mode=session.feedback_mode, turn_index=turn.turn_index,
        )
    if session.status != "active" or turn != pending:
        raise InterviewStateError("only the active question can be spoken")
    if command.utterance == "question":
        return spoken_question_text(turn.question)
    index = command.clarification_index
    if index is None or index >= len(turn.question.clarifications):
        raise InterviewStateError("saved clarification not found")
    return turn.question.clarifications[index].interviewer_response


def voice_event(kind: str, **values: object) -> bytes:
    return json.dumps({"type": kind, **values}).encode()
