"""OpenRouter text-to-speech, shared by the interviewer and by read-aloud.

This was `interviews/speech.py` alone until read-aloud needed the same call.
The interviewer's wording stays there; what moved here is only the request,
because a second caller with its own model and voice should not fork the
error handling, the cost arithmetic, or the check that a 200 actually carried
audio — that last one exists because the endpoint answers a failed synthesis
with a JSON body and a success code.

Model and voice are resolved per purpose. Read-aloud narration and the
interviewer are heard in different situations, and the voice that suits a
brisk interviewer is not necessarily the one to listen to for four minutes,
so `OPENROUTER_READING_TTS_MODEL` / `_VOICE` can differ from the interview
pair without either changing the other.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Literal

import httpx
from observability import provider_post, traced, record_estimate


OPENROUTER_SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
DEFAULT_TTS_MODEL = "mistralai/voxtral-mini-tts-2603"
DEFAULT_TTS_VOICE = "en_paul_neutral"
DEFAULT_TTS_PRICE_PER_CHARACTER_USD = 0.000016

# The provider's own ceiling per request. Read-aloud sends much smaller chunks
# than this — see `CHUNK_CHARACTER_TARGET` in the client — because the wait for
# the first sound is the wait for the first chunk.
MAXIMUM_TTS_CHARACTERS = 2_500

Purpose = Literal["interview", "reading"]


class SpeechError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    media_type: str
    cost_usd: float
    model: str
    voice: str


def _setting(purpose: Purpose, name: str) -> str | None:
    """A purpose-specific override, falling back to the shared setting."""

    if purpose == "reading":
        specific = os.getenv(f"OPENROUTER_READING_TTS_{name}", "").strip()
        if specific:
            return specific
    shared = os.getenv(f"OPENROUTER_TTS_{name}", "").strip()
    return shared or None


def configured_model(purpose: Purpose = "interview") -> str:
    return _setting(purpose, "MODEL") or DEFAULT_TTS_MODEL


def configured_voice(purpose: Purpose = "interview") -> str:
    return _setting(purpose, "VOICE") or DEFAULT_TTS_VOICE


def price_per_character() -> float:
    return max(
        0.0,
        float(
            os.getenv(
                "OPENROUTER_TTS_PRICE_PER_CHARACTER_USD",
                str(DEFAULT_TTS_PRICE_PER_CHARACTER_USD),
            )
        ),
    )


@traced("narration.synthesis.synthesize_speech", flow="narration")
def synthesize_speech(
    text: str,
    *,
    purpose: Purpose = "interview",
    client: httpx.Client | None = None,
    model: str | None = None,
    voice: str | None = None,
) -> SpeechAudio:
    """Speak one passage. Raises `SpeechError` rather than returning silence."""

    spoken = " ".join(text.split())
    if not spoken:
        raise ValueError("speech text is empty")
    if len(spoken) > MAXIMUM_TTS_CHARACTERS:
        raise ValueError("speech text is too long")
    requested_model = model or configured_model(purpose)
    requested_voice = voice or configured_voice(purpose)
    payload: dict[str, Any] = {
        "model": requested_model,
        "voice": requested_voice,
        "input": spoken,
        "response_format": "mp3",
    }

    def post(sender: httpx.Client) -> httpx.Response:
        try:
            response = provider_post(sender, OPENROUTER_SPEECH_URL, json=payload)
        except httpx.RequestError as error:
            raise SpeechError("text-to-speech request did not reach OpenRouter") from error
        if not 200 <= response.status_code < 300:
            raise SpeechError(f"text-to-speech failed ({response.status_code})")
        return response

    if client is None:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise SpeechError("OPENROUTER_API_KEY is required for text-to-speech")
        timeout = float(os.getenv("OPENROUTER_TTS_TIMEOUT_SECONDS", "15"))
        with httpx.Client(
            headers={"Authorization": f"Bearer {key}"}, timeout=timeout
        ) as owned:
            response = post(owned)
    else:
        response = post(client)

    media_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
    if not response.content:
        raise SpeechError("text-to-speech returned empty audio")
    if not media_type.startswith("audio/"):
        raise SpeechError("text-to-speech returned an invalid audio response")
    cost_usd = round(len(spoken) * price_per_character(), 6)
    record_estimate(cost_usd)
    return SpeechAudio(
        content=response.content,
        media_type=media_type,
        cost_usd=cost_usd,
        model=requested_model,
        voice=requested_voice,
    )
