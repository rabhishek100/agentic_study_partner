"""Low-cost OpenRouter text-to-speech for interviewer utterances.

The request itself now lives in `narration.synthesis`, shared with read-aloud.
What stays here is what is specific to an interviewer speaking: which parts of
a question are read out, and the fact that the interview voice is configured
by the `OPENROUTER_TTS_*` pair.
"""

from __future__ import annotations

import httpx

from narration.synthesis import (
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_PRICE_PER_CHARACTER_USD,
    DEFAULT_TTS_VOICE,
    MAXIMUM_TTS_CHARACTERS,
    OPENROUTER_SPEECH_URL,
    SpeechAudio,
    SpeechError,
    synthesize_speech,
)

from .contracts import InterviewQuestion


__all__ = [
    "DEFAULT_TTS_MODEL",
    "DEFAULT_TTS_PRICE_PER_CHARACTER_USD",
    "DEFAULT_TTS_VOICE",
    "MAXIMUM_TTS_CHARACTERS",
    "OPENROUTER_SPEECH_URL",
    "SpeechAudio",
    "SpeechError",
    "spoken_question_text",
    "synthesize_interviewer_speech",
]


def spoken_question_text(question: InterviewQuestion) -> str:
    """Include an automatically requested work sample in voice narration."""

    parts = [question.text.strip()]
    if question.work_sample != "none" and question.work_sample_prompt:
        parts.append(question.work_sample_prompt.strip())
    return " ".join(part for part in parts if part)


def synthesize_interviewer_speech(
    text: str,
    *,
    client: httpx.Client | None = None,
    model: str | None = None,
    voice: str | None = None,
) -> SpeechAudio:
    return synthesize_speech(
        text,
        purpose="interview",
        client=client,
        model=model,
        voice=voice,
    )
