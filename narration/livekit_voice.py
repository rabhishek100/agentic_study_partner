"""Owner-scoped LiveKit admission for read-aloud interruptions."""

from __future__ import annotations

from datetime import timedelta
import json
import os
import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


AGENT_NAME = "narration-voice"
RPC_METHOD = "narration.voice"
EVENT_TOPIC = "narration.voice"


class VoiceUnavailable(RuntimeError):
    pass


class VoiceConnection(BaseModel):
    server_url: str
    participant_token: str
    room_name: str
    participant_identity: str


class VoiceBinding(BaseModel):
    conversation_id: UUID
    owner_id: UUID
    participant_identity: str
    room_name: str


class VoiceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["listen", "stop_listening", "flush", "speak", "stop_speaking"]
    epoch: int = Field(default=0, ge=0)
    request_id: str = Field(default="", max_length=80)
    side_chat_id: UUID | None = None
    turn_index: int | None = Field(default=None, ge=0)


def create_voice_connection(conversation_id: UUID, owner_id: UUID) -> VoiceConnection:
    if os.getenv("NARRATION_LIVEKIT_ENABLED", "").lower() != "true":
        raise VoiceUnavailable("Live voice questions are disabled")
    settings = {
        name: os.getenv(name, "").strip()
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    }
    if not all(settings.values()):
        raise VoiceUnavailable("Live voice questions are not configured")
    try:
        from livekit import api
    except ImportError as error:
        raise VoiceUnavailable("install the voice extra to enable LiveKit") from error

    nonce = uuid4().hex
    binding = VoiceBinding(
        conversation_id=conversation_id,
        owner_id=owner_id,
        participant_identity=f"reader-{nonce}",
        room_name=f"narration-{conversation_id}-{nonce}",
    )
    token = (
        api.AccessToken(settings["LIVEKIT_API_KEY"], settings["LIVEKIT_API_SECRET"])
        .with_identity(binding.participant_identity)
        .with_ttl(timedelta(minutes=30))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=binding.room_name,
                can_publish=True,
                can_publish_sources=["microphone"],
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
    return VoiceConnection(
        server_url=settings["LIVEKIT_URL"],
        participant_token=token,
        room_name=binding.room_name,
        participant_identity=binding.participant_identity,
    )


def voice_event(kind: str, **values: object) -> bytes:
    return json.dumps({"type": kind, **values}).encode()


def spoken_saved_answer(answer: str) -> str:
    """Make a persisted grounded answer tolerable to hear without adding claims."""

    text = re.sub(r"```[\s\S]*?```", " Code block, shown in the side chat. ", answer)
    text = re.sub(r"\[S\d+(?::[^\]]+)?\]", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?:\*\*|__)(.*?)(?:\*\*|__)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
    text = " ".join(text.split()).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)
