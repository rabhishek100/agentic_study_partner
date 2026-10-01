"""Budgeted OpenRouter transcription fallback for canonical video audio."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Protocol

import httpx

from observability import provider_post, traced
from video.transcripts import TranscriptCue


OPENROUTER_TRANSCRIPTIONS_URL = (
    "https://openrouter.ai/api/v1/audio/transcriptions"
)
DEFAULT_TRANSCRIPTION_MODEL = "openai/whisper-1"
CHUNK_DURATION_MS = 10 * 60 * 1_000
SAMPLE_RATE_HZ = 16_000
BITRATE = "64k"
ESTIMATED_COST_PER_MINUTE_USD = Decimal("0.006")
MAX_ATTEMPTS = 2
FFMPEG_TIMEOUT_SECONDS = 2 * 60 * 60
MAXIMUM_CHUNK_BYTES = 8 * 1024 * 1024
TIMESTAMP_TOLERANCE_MS = 250
CHUNK_NAME = re.compile(r"^chunk-(\d{5})\.mp3$")


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CommandResult]


def run_command(
    argv: Sequence[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=timeout_seconds,
    )


class AudioTranscriptionError(RuntimeError):
    """Audio extraction or hosted transcription failed safely."""


class AudioBudgetExceeded(AudioTranscriptionError):
    """The next request, or a just-completed request, crossed the caller cap."""

    def __init__(
        self,
        *,
        incurred_cost_usd: Decimal,
        required_cost_usd: Decimal,
        generation_ids: tuple[str, ...] = (),
    ) -> None:
        self.incurred_cost_usd = incurred_cost_usd
        self.required_cost_usd = required_cost_usd
        self.generation_ids = generation_ids
        super().__init__("audio transcription would exceed the remaining budget")


@dataclass(frozen=True)
class AudioChunkProvenance:
    chunk_index: int
    start_ms: int
    end_ms: int
    generation_id: str
    model: str
    input_hash: str
    estimated_cost_usd: float
    cost_usd: float
    attempt: int
    usage: dict[str, Any]


@dataclass(frozen=True)
class AudioTranscriptionProvenance:
    provider: str
    requested_model: str
    total_estimated_cost_usd: float
    total_cost_usd: float
    chunk_duration_ms: int
    sample_rate_hz: int
    bitrate: str
    chunks: tuple[AudioChunkProvenance, ...]

    @property
    def generation_ids(self) -> tuple[str, ...]:
        return tuple(chunk.generation_id for chunk in self.chunks)

    def as_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "total_estimated_cost_usd": self.total_estimated_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            "chunk_duration_ms": self.chunk_duration_ms,
            "sample_rate_hz": self.sample_rate_hz,
            "bitrate": self.bitrate,
            "chunks": [
                {
                    "chunk_index": chunk.chunk_index,
                    "start_ms": chunk.start_ms,
                    "end_ms": chunk.end_ms,
                    "generation_id": chunk.generation_id,
                    "model": chunk.model,
                    "input_hash": chunk.input_hash,
                    "estimated_cost_usd": chunk.estimated_cost_usd,
                    "cost_usd": chunk.cost_usd,
                    "attempt": chunk.attempt,
                    "usage": chunk.usage,
                }
                for chunk in self.chunks
            ],
        }


@dataclass(frozen=True)
class AudioTranscription:
    cues: tuple[TranscriptCue, ...]
    language: str | None
    provenance: AudioTranscriptionProvenance


@dataclass(frozen=True)
class _ChunkResponse:
    segments: tuple[dict[str, Any], ...]
    language: str | None
    generation_id: str
    model: str
    usage: dict[str, Any]
    cost_usd: Decimal
    attempt: int


class OpenRouterAudioClient:
    """Split canonical media and transcribe each bounded audio chunk."""

    def __init__(
        self,
        model: str | None = None,
        *,
        timeout: float = 180.0,
        client: httpx.Client | None = None,
        command_runner: CommandRunner = run_command,
        ffmpeg_binary: str | None = None,
        estimated_cost_per_minute_usd: Decimal | str | float | None = None,
    ) -> None:
        configured_model = model or os.getenv("OPENROUTER_AUDIO_MODEL")
        self.model = (
            configured_model.strip()
            if configured_model and configured_model.strip()
            else DEFAULT_TRANSCRIPTION_MODEL
        )
        configured_rate = (
            estimated_cost_per_minute_usd
            if estimated_cost_per_minute_usd is not None
            else os.getenv("OPENROUTER_AUDIO_COST_PER_MINUTE_USD")
        )
        if configured_rate in (None, ""):
            if self.model != DEFAULT_TRANSCRIPTION_MODEL:
                raise ValueError(
                    "OPENROUTER_AUDIO_COST_PER_MINUTE_USD is required for a custom audio model"
                )
            self.estimated_cost_per_minute_usd = ESTIMATED_COST_PER_MINUTE_USD
        else:
            self.estimated_cost_per_minute_usd = _decimal_budget(configured_rate)
            if self.estimated_cost_per_minute_usd <= 0:
                raise ValueError("estimated audio cost must be positive")
        self._runner = command_runner
        self._ffmpeg_binary = (
            ffmpeg_binary
            or os.getenv("VIDEO_FFMPEG_BINARY", "ffmpeg").strip()
            or "ffmpeg"
        )
        self._owns_client = client is None
        if client is None:
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY is required for audio fallback")
            client = httpx.Client(
                headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
            )
        self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenRouterAudioClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @traced("video.audio.OpenRouterAudioClient.transcribe", flow="transcription")
    def transcribe(
        self,
        media_path: Path,
        *,
        duration_ms: int,
        work_dir: Path,
        remaining_budget_usd: Decimal | str | float,
        language: str | None = "en",
    ) -> AudioTranscription:
        source, root = _validate_inputs(
            media_path,
            duration_ms=duration_ms,
            work_dir=work_dir,
        )
        budget = _decimal_budget(remaining_budget_usd)
        total_estimate = sum(
            (
                _estimated_cost(
                    min(CHUNK_DURATION_MS, duration_ms - start_ms),
                    cost_per_minute_usd=self.estimated_cost_per_minute_usd,
                )
                for start_ms in range(0, duration_ms, CHUNK_DURATION_MS)
            ),
            Decimal("0"),
        )
        # Never spend on an initial chunk when the complete transcription is
        # already known not to fit. Partial transcripts are not publishable.
        if total_estimate > budget:
            raise AudioBudgetExceeded(
                incurred_cost_usd=Decimal("0"),
                required_cost_usd=total_estimate,
            )
        if language is not None and (not isinstance(language, str) or not language.strip()):
            raise ValueError("language must be a non-empty string or null")
        normalized_language = language.strip() if language is not None else None

        with tempfile.TemporaryDirectory(prefix="audio-chunks-", dir=root) as temporary:
            chunk_root = Path(temporary).resolve()
            chunks = self._extract_chunks(
                source,
                destination=chunk_root,
                duration_ms=duration_ms,
            )
            cues: list[TranscriptCue] = []
            provenance: list[AudioChunkProvenance] = []
            spent = Decimal("0")
            total_estimated = Decimal("0")
            detected_language: str | None = normalized_language
            for index, chunk in enumerate(chunks):
                start_ms = index * CHUNK_DURATION_MS
                end_ms = min(duration_ms, start_ms + CHUNK_DURATION_MS)
                estimate = _estimated_cost(
                    end_ms - start_ms,
                    cost_per_minute_usd=self.estimated_cost_per_minute_usd,
                )
                if spent + estimate > budget:
                    raise AudioBudgetExceeded(
                        incurred_cost_usd=spent,
                        required_cost_usd=estimate,
                        generation_ids=tuple(
                            item.generation_id for item in provenance
                        ),
                    )
                response = self._transcribe_chunk(
                    chunk,
                    chunk_index=index,
                    language=normalized_language,
                )
                spent += response.cost_usd
                if spent > budget:
                    raise AudioBudgetExceeded(
                        incurred_cost_usd=spent,
                        required_cost_usd=response.cost_usd,
                        generation_ids=tuple(
                            [
                                *(item.generation_id for item in provenance),
                                response.generation_id,
                            ]
                        ),
                    )
                total_estimated += estimate
                try:
                    normalized = _normalize_segments(
                        response.segments,
                        cue_offset=len(cues),
                        chunk_start_ms=start_ms,
                        chunk_end_ms=end_ms,
                        duration_ms=duration_ms,
                    )
                except ValueError:
                    raise AudioTranscriptionError(
                        f"audio transcription was invalid for chunk {index}"
                    ) from None
                cues.extend(normalized)
                if response.language:
                    detected_language = response.language
                provenance.append(
                    AudioChunkProvenance(
                        chunk_index=index,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        generation_id=response.generation_id,
                        model=response.model,
                        input_hash=_file_hash(chunk),
                        estimated_cost_usd=float(estimate),
                        cost_usd=float(response.cost_usd),
                        attempt=response.attempt,
                        usage=response.usage,
                    )
                )
            if not cues:
                raise AudioTranscriptionError(
                    "audio transcription returned no timestamped speech"
                )
            _validate_cue_order(cues, duration_ms=duration_ms)
            return AudioTranscription(
                cues=tuple(cues),
                language=detected_language,
                provenance=AudioTranscriptionProvenance(
                    provider="openrouter",
                    requested_model=self.model,
                    total_estimated_cost_usd=float(total_estimated),
                    total_cost_usd=float(spent),
                    chunk_duration_ms=CHUNK_DURATION_MS,
                    sample_rate_hz=SAMPLE_RATE_HZ,
                    bitrate=BITRATE,
                    chunks=tuple(provenance),
                ),
            )

    def _extract_chunks(
        self, source: Path, *, destination: Path, duration_ms: int
    ) -> tuple[Path, ...]:
        pattern = destination / "chunk-%05d.mp3"
        argv = (
            self._ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-b:a",
            BITRATE,
            "-map_metadata",
            "-1",
            "-f",
            "segment",
            "-segment_time",
            str(CHUNK_DURATION_MS // 1_000),
            "-reset_timestamps",
            "1",
            str(pattern),
        )
        try:
            completed = self._runner(
                argv, timeout_seconds=FFMPEG_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AudioTranscriptionError("audio extraction failed") from None
        if completed.returncode != 0:
            raise AudioTranscriptionError("audio extraction failed")
        chunks = tuple(sorted(destination.glob("chunk-*.mp3")))
        maximum_chunks = math.ceil(duration_ms / CHUNK_DURATION_MS)
        if not chunks or len(chunks) > maximum_chunks:
            raise AudioTranscriptionError("audio extraction produced invalid chunks")
        for index, chunk in enumerate(chunks):
            match = CHUNK_NAME.fullmatch(chunk.name)
            if match is None or int(match.group(1)) != index:
                raise AudioTranscriptionError(
                    "audio extraction produced invalid chunks"
                )
            if (
                chunk.is_symlink()
                or not chunk.is_file()
                or chunk.resolve().parent != destination
                or not 0 < chunk.stat().st_size <= MAXIMUM_CHUNK_BYTES
            ):
                raise AudioTranscriptionError(
                    "audio extraction produced invalid chunks"
                )
        return chunks

    def _transcribe_chunk(
        self,
        chunk: Path,
        *,
        chunk_index: int,
        language: str | None,
    ) -> _ChunkResponse:
        data = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if language is not None:
            data["language"] = language
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with chunk.open("rb") as audio:
                    response = provider_post(
                        self._client,
                        OPENROUTER_TRANSCRIPTIONS_URL,
                        trace_metadata={"provider_attempt": attempt},
                        data=data,
                        files={"file": (chunk.name, audio, "audio/mpeg")},
                    )
            except httpx.RequestError:
                if attempt < MAX_ATTEMPTS:
                    continue
                raise AudioTranscriptionError(
                    f"audio transcription failed for chunk {chunk_index}"
                ) from None
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable:
                if attempt < MAX_ATTEMPTS:
                    continue
                raise AudioTranscriptionError(
                    f"audio transcription failed for chunk {chunk_index}"
                )
            if not 200 <= response.status_code < 300:
                raise AudioTranscriptionError(
                    f"audio transcription was rejected for chunk {chunk_index}"
                )
            try:
                body = response.json()
                return _chunk_response(
                    body,
                    response_headers=response.headers,
                    requested_model=self.model,
                    attempt=attempt,
                )
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                raise AudioTranscriptionError(
                    f"audio transcription was invalid for chunk {chunk_index}"
                ) from None
        raise AssertionError("bounded transcription retry loop was exhausted")


def _validate_inputs(
    media_path: Path, *, duration_ms: int, work_dir: Path
) -> tuple[Path, Path]:
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or duration_ms <= 0
    ):
        raise ValueError("duration_ms must be a positive integer")
    source = Path(media_path)
    if source.is_symlink() or not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("media_path must be a non-empty regular file")
    source = source.resolve()
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("work_dir must be a real directory")
    return source, root.resolve()


def _decimal_budget(value: Decimal | str | float) -> Decimal:
    try:
        budget = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("remaining budget must be a valid amount") from None
    if not budget.is_finite() or budget < 0:
        raise ValueError("remaining budget cannot be negative or non-finite")
    return budget


def _estimated_cost(
    duration_ms: int, *, cost_per_minute_usd: Decimal
) -> Decimal:
    billed_minutes = (
        Decimal(duration_ms) / Decimal(60_000)
    ).to_integral_value(rounding=ROUND_CEILING)
    return billed_minutes * cost_per_minute_usd


def _chunk_response(
    body: Any,
    *,
    response_headers: httpx.Headers,
    requested_model: str,
    attempt: int,
) -> _ChunkResponse:
    if not isinstance(body, dict):
        raise ValueError("invalid transcription response")
    segments = body.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("transcription response has no segments")
    if any(not isinstance(segment, dict) for segment in segments):
        raise ValueError("transcription response has invalid segments")
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("transcription response has no usage")
    cost = _decimal_cost(usage.get("cost"))
    generation_id = (
        body.get("id")
        or body.get("generation_id")
        or response_headers.get("x-generation-id")
        or response_headers.get("x-request-id")
    )
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise ValueError("transcription response has no generation id")
    model = body.get("model") or requested_model
    if not isinstance(model, str) or not model.strip():
        raise ValueError("transcription response has no model")
    language = body.get("language")
    if language is not None and (
        not isinstance(language, str) or not language.strip()
    ):
        raise ValueError("transcription response has invalid language")
    return _ChunkResponse(
        segments=tuple(segments),
        language=language.strip() if isinstance(language, str) else None,
        generation_id=generation_id.strip(),
        model=model.strip(),
        usage=dict(usage),
        cost_usd=cost,
        attempt=attempt,
    )


def _decimal_cost(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("invalid provider cost")
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid provider cost") from None
    if not cost.is_finite() or cost < 0:
        raise ValueError("invalid provider cost")
    return cost


def _normalize_segments(
    segments: tuple[dict[str, Any], ...],
    *,
    cue_offset: int,
    chunk_start_ms: int,
    chunk_end_ms: int,
    duration_ms: int,
) -> list[TranscriptCue]:
    cues: list[TranscriptCue] = []
    prior_start = -1
    chunk_length = chunk_end_ms - chunk_start_ms
    for segment in segments:
        local_start = _timestamp_ms(segment.get("start"))
        local_end = _timestamp_ms(segment.get("end"))
        if local_start < prior_start or local_end <= local_start:
            raise ValueError("transcription segments are unordered")
        if local_start > chunk_length + TIMESTAMP_TOLERANCE_MS:
            raise ValueError("transcription segment is outside its chunk")
        if local_end > chunk_length + TIMESTAMP_TOLERANCE_MS:
            raise ValueError("transcription segment is outside its chunk")
        local_start = min(local_start, chunk_length)
        local_end = min(local_end, chunk_length)
        if local_end <= local_start:
            raise ValueError("transcription segment has an empty time range")
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("transcription segment has no text")
        start_ms = chunk_start_ms + local_start
        end_ms = min(duration_ms, chunk_start_ms + local_end)
        cues.append(
            TranscriptCue(
                cue_index=cue_offset + len(cues),
                start_ms=start_ms,
                end_ms=end_ms,
                text=" ".join(text.split()),
                raw_text=text,
            )
        )
        prior_start = local_start
    return cues


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("transcription timestamp is invalid")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("transcription timestamp is invalid")
    return round(seconds * 1_000)


def _validate_cue_order(cues: list[TranscriptCue], *, duration_ms: int) -> None:
    previous_start = -1
    for index, cue in enumerate(cues):
        if cue.cue_index != index:
            raise AudioTranscriptionError("audio transcript cue indexes are invalid")
        if (
            cue.start_ms < previous_start
            or cue.start_ms < 0
            or cue.end_ms <= cue.start_ms
            or cue.end_ms > duration_ms
            or not cue.text.strip()
        ):
            raise AudioTranscriptionError("audio transcript cues are invalid")
        previous_start = cue.start_ms


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
