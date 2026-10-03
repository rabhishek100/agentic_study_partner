"""Turn one spoken question into composer text.

Dictation is the smallest possible use of hosted transcription: a clip of a
few seconds that replaces typing, discarded as soon as its words reach the
question box. `video.audio` is deliberately not reused even though it calls
the same provider and model — it splits media with ffmpeg, charges every
chunk against an ingestion budget, and returns timestamped cues, none of
which a question needs and all of which would put a subprocess on the request
path.

Nothing here is canonical or derived data. The audio is never written to disk
or to the database, and no provenance row survives the request: a dictated
question is indistinguishable from a typed one once it lands in the composer,
and the answer it produces is grounded and cited the same way.
"""

from __future__ import annotations


import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from observability import provider_post, traced


OPENROUTER_TRANSCRIPTIONS_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_DICTATION_MODEL = "openai/whisper-1"

# Roughly two minutes of the Opus that browsers record at, with room for the
# WAV fallback Safari may produce. The composer stops recording well before
# this; the ceiling exists so a hand-built request cannot buy a long
# transcription, not to bound ordinary use.
MAXIMUM_QUESTION_BYTES = 8 * 1024 * 1024

# Whisper infers a container from the filename, so the extension is part of
# the request rather than cosmetic. Parameters are stripped before lookup:
# MediaRecorder reports `audio/webm;codecs=opus`.
QUESTION_MEDIA_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}

REQUEST_TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 2

logger = logging.getLogger("study_partner.dictation")

# Whisper-family models can emit these short, subtitle-like phrases for room
# noise or silence. This list is deliberately exact and conservative: a real
# answer that merely starts or ends with the same words is preserved.
_SILENCE_HALLUCINATIONS = frozenset(
    {
        "bye",
        "goodbye",
        "music",
        "silence",
        "thanks",
        "thanks for listening",
        "thanks for watching",
        "thank you",
        "thank you for listening",
        "thank you for watching",
        "thank you for your attention",
        "you",
    }
)


class DictationError(RuntimeError):
    """The provider could not be reached, or answered with nothing usable.

    The message is for logs. Callers show the reader something that names the
    way out — keep typing — because a failed dictation blocks nothing.
    """


@dataclass(frozen=True)
class DictationResult:
    text: str
    cost_usd: float = 0.0
    seconds: float = 0.0


def is_probable_silence_hallucination(text: str) -> bool:
    """Whether a complete transcript is a known Whisper silence artifact."""

    normalized = " ".join(re.sub(r"[^\w']+", " ", text.casefold()).split())
    if normalized in _SILENCE_HALLUCINATIONS:
        return True
    words = normalized.split()
    return bool(words) and len(words) <= 8 and set(words) <= {"thank", "thanks", "you"}


def audio_extension(media_type: str) -> str | None:
    """The container Whisper should be told about, or None if unsupported."""

    return QUESTION_MEDIA_TYPES.get(media_type.split(";", 1)[0].strip().lower())


def transcribe_spoken_question(
    audio: bytes,
    *,
    media_type: str,
    language: str | None = "en",
    client: httpx.Client | None = None,
    model: str | None = None,
) -> str:
    """Return the words in one recorded clip, or "" when it holds no speech.

    Silence is a normal outcome — a reader taps the mic, thinks better of it,
    and taps again — so it returns empty rather than raising. Only a provider
    that fails or answers unintelligibly is an error.
    """

    return transcribe_spoken_question_result(
        audio,
        media_type=media_type,
        language=language,
        client=client,
        model=model,
    ).text


@traced("study.dictation.transcribe_spoken_question_result", flow="dictation")
def transcribe_spoken_question_result(
    audio: bytes,
    *,
    media_type: str,
    language: str | None = "en",
    client: httpx.Client | None = None,
    model: str | None = None,
) -> DictationResult:
    """Return text plus provider-reported usage for a cost-tracked session."""

    extension = audio_extension(media_type)
    if extension is None:
        raise ValueError(f"unsupported dictation media type: {media_type!r}")
    if not audio:
        raise ValueError("dictation audio is empty")
    if len(audio) > MAXIMUM_QUESTION_BYTES:
        raise ValueError("dictation audio is too large")
    if language is not None and not language.strip():
        raise ValueError("language must be a non-empty string or null")

    configured = model or os.getenv("OPENROUTER_AUDIO_MODEL", "")
    requested_model = configured.strip() or DEFAULT_DICTATION_MODEL
    data: dict[str, str] = {
        "model": requested_model,
        # Plain JSON, not the `verbose_json` ingestion asks for: a composer
        # wants a sentence, and segment timings would only be discarded.
        "response_format": "json",
    }
    if language is not None:
        data["language"] = language.strip()

    if client is not None:
        return _post_result(
            client, data=data, audio=audio, filename=f"question.{extension}"
        )
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise DictationError("OPENROUTER_API_KEY is required for dictation")
    with httpx.Client(
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as owned:
        return _post_result(
            owned, data=data, audio=audio, filename=f"question.{extension}"
        )


def _post(
    client: httpx.Client, *, data: dict[str, str], audio: bytes, filename: str
) -> str:
    return _post_result(client, data=data, audio=audio, filename=filename).text


def _post_result(
    client: httpx.Client, *, data: dict[str, str], audio: bytes, filename: str
) -> DictationResult:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = provider_post(
                client,
                OPENROUTER_TRANSCRIPTIONS_URL,
                trace_metadata={"provider_attempt": attempt},
                data=data,
                files={"file": (filename, audio, "application/octet-stream")},
            )
        except httpx.RequestError:
            if attempt < MAX_ATTEMPTS:
                continue
            raise DictationError("dictation request never reached the provider") from None
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < MAX_ATTEMPTS:
                continue
            raise DictationError(
                f"dictation provider is unavailable ({response.status_code})"
            )
        if not 200 <= response.status_code < 300:
            raise DictationError(
                f"dictation request was rejected ({response.status_code})"
            )
        try:
            body = response.json()
            text = _spoken_text(body)
            usage = body.get("usage") if isinstance(body, dict) else None
            cost = usage.get("cost", 0) if isinstance(usage, dict) else 0
            seconds = usage.get("seconds", 0) if isinstance(usage, dict) else 0
            return DictationResult(
                text=text,
                cost_usd=(
                    round(float(cost), 6)
                    if isinstance(cost, (int, float)) and cost >= 0
                    else 0.0
                ),
                seconds=(
                    round(float(seconds), 3)
                    if isinstance(seconds, (int, float)) and seconds >= 0
                    else 0.0
                ),
            )
        except ValueError as error:
            raise DictationError(str(error)) from None
    raise AssertionError("bounded dictation retry loop was exhausted")


def _spoken_text(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError("dictation response was not an object")
    text = body.get("text")
    if text is None:
        raise ValueError("dictation response carried no text")
    if not isinstance(text, str):
        raise ValueError("dictation response text was not a string")
    # Collapsed to single spaces because the value is spliced into whatever is
    # already in the composer, where a stray newline would submit the turn.
    return " ".join(text.split())
