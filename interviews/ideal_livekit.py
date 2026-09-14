"""LiveKit admission and commands for listen-only ideal interview playback."""

from __future__ import annotations

from datetime import timedelta
import json
import os
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from .contracts import ContractModel
from .ideal_contracts import IdealInterviewFlow


AGENT_NAME = "ideal-interview-voice"
RPC_METHOD = "ideal-interview.voice"
EVENT_TOPIC = "ideal-interview.voice"


class IdealVoiceUnavailable(RuntimeError):
    pass


class IdealVoiceConnection(ContractModel):
    server_url: str
    participant_token: str
    room_name: str
    participant_identity: str


class IdealVoiceBinding(ContractModel):
    flow_id: UUID
    owner_id: UUID
    participant_identity: str
    room_name: str


class IdealVoiceCommand(ContractModel):
    action: Literal["play", "stop"]
    request_id: str = Field(default="", max_length=80)
    start_exchange: int = Field(default=0, ge=0)
    start_speaker: Literal["interviewer", "candidate"] = "interviewer"


def create_voice_connection(
    flow: IdealInterviewFlow, owner_id: UUID
) -> IdealVoiceConnection:
    if os.getenv("INTERVIEW_LIVEKIT_ENABLED", "").lower() != "true":
        raise IdealVoiceUnavailable("LiveKit interview voice is disabled")
    settings = {
        name: os.getenv(name, "").strip()
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    }
    if not all(settings.values()):
        raise IdealVoiceUnavailable("LiveKit interview voice is not configured")
    try:
        from livekit import api
    except ImportError as error:
        raise IdealVoiceUnavailable("install the voice extra to enable LiveKit") from error

    nonce = uuid4().hex
    binding = IdealVoiceBinding(
        flow_id=UUID(flow.flow_id),
        owner_id=owner_id,
        participant_identity=f"listener-{nonce}",
        room_name=f"ideal-interview-{flow.flow_id}-{nonce}",
    )
    token = (
        api.AccessToken(settings["LIVEKIT_API_KEY"], settings["LIVEKIT_API_SECRET"])
        .with_identity(binding.participant_identity)
        .with_ttl(timedelta(minutes=90))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=binding.room_name,
                can_publish=False,
                can_subscribe=True,
                can_publish_data=True,
                can_update_own_metadata=False,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=AGENT_NAME,
                        metadata=binding.model_dump_json(),
                    )
                ],
                empty_timeout=60,
                departure_timeout=20,
            )
        )
        .to_jwt()
    )
    return IdealVoiceConnection(
        server_url=settings["LIVEKIT_URL"],
        participant_token=token,
        room_name=binding.room_name,
        participant_identity=binding.participant_identity,
    )


def pronunciation_text(text: str) -> str:
    """Apply an operator-reviewed technical-term -> IPA map for Cartesia."""

    raw = os.getenv("LIVEKIT_IDEAL_PRONUNCIATIONS_JSON", "").strip()
    if not raw:
        return text
    try:
        replacements = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("LIVEKIT_IDEAL_PRONUNCIATIONS_JSON must be valid JSON") from error
    if not isinstance(replacements, dict):
        raise ValueError("LIVEKIT_IDEAL_PRONUNCIATIONS_JSON must be an object")
    rendered = text
    for term, ipa in sorted(replacements.items(), key=lambda pair: -len(pair[0])):
        if not isinstance(term, str) or not isinstance(ipa, str):
            raise ValueError("pronunciation entries must map text to IPA strings")
        rendered = rendered.replace(term, f"<<{ipa}>>")
    return rendered


def voice_event(kind: str, **values: object) -> bytes:
    return json.dumps({"type": kind, **values}).encode()
