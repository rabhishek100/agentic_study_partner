"""Low-cost OpenRouter text-to-speech for interviewer utterances."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


OPENROUTER_SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
DEFAULT_TTS_MODEL = "hexgrad/kokoro-82m"
DEFAULT_TTS_VOICE = "af_heart"
DEFAULT_TTS_PRICE_PER_CHARACTER_USD = 0.00000062
MAXIMUM_TTS_CHARACTERS = 2_500


class SpeechError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    media_type: str
    cost_usd: float
    model: str
    voice: str


def synthesize_interviewer_speech(
    text: str,
    *,
    client: httpx.Client | None = None,
    model: str | None = None,
    voice: str | None = None,
) -> SpeechAudio:
    spoken = " ".join(text.split())
    if not spoken:
        raise ValueError("speech text is empty")
    if len(spoken) > MAXIMUM_TTS_CHARACTERS:
        raise ValueError("speech text is too long")
    requested_model = model or os.getenv("OPENROUTER_TTS_MODEL") or DEFAULT_TTS_MODEL
    requested_voice = voice or os.getenv("OPENROUTER_TTS_VOICE") or DEFAULT_TTS_VOICE
    payload: dict[str, Any] = {
        "model": requested_model,
        "voice": requested_voice,
        "input": spoken,
        "response_format": "mp3",
    }

    def post(sender: httpx.Client) -> httpx.Response:
        try:
            response = sender.post(OPENROUTER_SPEECH_URL, json=payload)
        except httpx.RequestError as error:
            raise SpeechError("text-to-speech request did not reach OpenRouter") from error
        if not 200 <= response.status_code < 300:
            raise SpeechError(f"text-to-speech failed ({response.status_code})")
        return response

    if client is None:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise SpeechError("OPENROUTER_API_KEY is required for text-to-speech")
        timeout = float(os.getenv("OPENROUTER_TTS_TIMEOUT_SECONDS", "8"))
        with httpx.Client(
            headers={"Authorization": f"Bearer {key}"}, timeout=timeout
        ) as owned:
            response = post(owned)
    else:
        response = post(client)

    if not response.content:
        raise SpeechError("text-to-speech returned empty audio")
    price = float(
        os.getenv(
            "OPENROUTER_TTS_PRICE_PER_CHARACTER_USD",
            str(DEFAULT_TTS_PRICE_PER_CHARACTER_USD),
        )
    )
    return SpeechAudio(
        content=response.content,
        media_type=response.headers.get("content-type", "audio/mpeg").split(";", 1)[0],
        cost_usd=round(len(spoken) * max(0.0, price), 6),
        model=requested_model,
        voice=requested_voice,
    )
