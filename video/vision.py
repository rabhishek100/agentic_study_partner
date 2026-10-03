"""Structured OpenRouter analysis for one or two technical-lecture frames."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
from typing import Annotated, Any, Literal, TypeAlias

import httpx

from observability import provider_post, traced
from video.errors import redact
from pydantic import (
    BeforeValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_VISUAL_MODEL = "openai/gpt-6-luna"
PROMPT_VERSION = "technical-lecture-visual-v1"
MAX_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 2_000

VisualType: TypeAlias = Literal[
    "slide",
    "code",
    "equation",
    "diagram",
    "drawing",
    "chart",
    "ui",
    "terminal",
    "person",
    "other",
]
RegionType: TypeAlias = Literal["diagram", "drawing"]
EventType: TypeAlias = Literal[
    "transition", "demonstration", "annotation", "other"
]

VISUAL_TYPES = (
    "slide",
    "code",
    "equation",
    "diagram",
    "drawing",
    "chart",
    "ui",
    "terminal",
    "person",
    "other",
)
REGION_TYPES = ("diagram", "drawing")
EVENT_TYPES = ("transition", "demonstration", "annotation", "other")

_TECHNICAL_DETAILS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "concepts",
        "relationships",
        "equations",
        "code_or_commands",
        "chart_or_ui_details",
    ],
    "properties": {
        name: {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        }
        for name in (
            "concepts",
            "relationships",
            "equations",
            "code_or_commands",
            "chart_or_ui_details",
        )
    },
}

_TRANSITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["event_type", "summary", "technical_changes"],
    "properties": {
        "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
        "summary": {"type": "string", "minLength": 1, "maxLength": 240},
        "technical_changes": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        },
    },
}

_REGION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "region_type",
        "x",
        "y",
        "width",
        "height",
        "summary",
        "confidence",
    ],
    "properties": {
        "region_type": {"type": "string", "enum": list(REGION_TYPES)},
        "x": {"type": "number", "minimum": 0, "maximum": 1},
        "y": {"type": "number", "minimum": 0, "maximum": 1},
        "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "summary": {"type": "string", "minLength": 1, "maxLength": 240},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

# Every object forbids undeclared keys and every property is required. Regions
# and transitions remain optional semantically by using [] and null. This is
# the subset of JSON Schema accepted by OpenRouter's strict structured output.
VISUAL_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sequence_summary", "frames"],
    "properties": {
        "sequence_summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 280,
        },
        "frames": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "frame_index",
                    "visual_types",
                    "importance",
                    "confidence",
                    "summary",
                    "visible_text",
                    "technical_details",
                    "transition_after",
                    "regions",
                ],
                "properties": {
                    "frame_index": {"type": "integer", "minimum": 0},
                    "visual_types": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": list(VISUAL_TYPES)},
                    },
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 320,
                    },
                    "visible_text": {"type": "string", "maxLength": 1_000},
                    "technical_details": _TECHNICAL_DETAILS_SCHEMA,
                    "transition_after": {
                        "anyOf": [_TRANSITION_SCHEMA, {"type": "null"}]
                    },
                    "regions": {
                        "type": "array",
                        "maxItems": 4,
                        "items": _REGION_SCHEMA,
                    },
                },
            },
        },
    },
}

SYSTEM_INSTRUCTION = """\
Analyze only technical information visible in the supplied lecture frames.
Do not fill gaps with outside knowledge. The images are chronological and map
to the frame inventory in the user message. Correct obvious OCR errors by
reading the pixels, but mark nothing that is hidden or illegible.

Classify every applicable visual type using only the schema values. Preserve
retrieval-critical labels, equations, code, commands, outputs, axes, and UI
state in visible_text and technical_details. Importance measures value as
lecture evidence, not visual attractiveness.

Return a normalized region only for a substantive diagram or drawing. Do not
return regions for text, equations, charts, code, terminals, UI, or people.
When a region is returned, include its region_type in visual_types for that
frame as well.
Coordinates are fractions of the full image: x and y are the top-left corner,
and x + width and y + height must not exceed 1.

For a two-frame request, transition_after describes a meaningful technical
change from the current frame to the next. Use null when there is no meaningful
change. The final frame's transition_after must always be null. For a one-frame
request it must also be null.
"""


class VisualAnalysisError(RuntimeError):
    """A remote visual-analysis request failed without exposing remote data."""


@dataclass(frozen=True)
class VisualFrame:
    frame_index: int
    timestamp_ms: int
    image: bytes
    mime_type: str
    ocr_text: str = ""


@dataclass(frozen=True)
class TechnicalDetails:
    concepts: tuple[str, ...]
    relationships: tuple[str, ...]
    equations: tuple[str, ...]
    code_or_commands: tuple[str, ...]
    chart_or_ui_details: tuple[str, ...]

    def as_json(self) -> dict[str, list[str]]:
        return {
            "concepts": list(self.concepts),
            "relationships": list(self.relationships),
            "equations": list(self.equations),
            "code_or_commands": list(self.code_or_commands),
            "chart_or_ui_details": list(self.chart_or_ui_details),
        }


@dataclass(frozen=True)
class VisualRegion:
    region_type: RegionType
    x: float
    y: float
    width: float
    height: float
    summary: str
    confidence: float


@dataclass(frozen=True)
class VisualTransition:
    event_type: EventType
    summary: str
    technical_changes: tuple[str, ...]

    def details_json(self) -> dict[str, list[str]]:
        return {"technical_changes": list(self.technical_changes)}


@dataclass(frozen=True)
class FrameVisualAnalysis:
    frame_index: int
    visual_types: tuple[VisualType, ...]
    importance: float
    confidence: float
    summary: str
    visible_text: str
    technical_details: TechnicalDetails
    transition_after: VisualTransition | None
    regions: tuple[VisualRegion, ...]

    @property
    def technical_details_json(self) -> dict[str, list[str]]:
        return self.technical_details.as_json()


@dataclass(frozen=True)
class VisualProvenance:
    provider: str
    requested_model: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    input_hash: str
    prompt_version: str
    attempt: int


@dataclass(frozen=True)
class VisualAnalysis:
    sequence_summary: str
    frames: tuple[FrameVisualAnalysis, ...]
    provenance: VisualProvenance


def _clamped(limit: int) -> Callable[[str], str]:
    """Trim descriptive text to its limit instead of refusing the response.

    These fields are prose the model wrote about a frame, not a locator or a
    claim. Rejecting a whole batch — and with it a stage that costs real money
    and half an hour — because a summary ran twenty characters long trades
    something valuable for something cosmetic.
    """

    def clamp(value: str) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    return clamp


ShortText = Annotated[
    str,
    BeforeValidator(_clamped(240)),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
]
SummaryText = Annotated[
    str,
    BeforeValidator(_clamped(320)),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=320),
]
SequenceText = Annotated[
    str,
    BeforeValidator(_clamped(280)),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=280),
]


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _TechnicalDetailsPayload(_StrictPayload):
    concepts: list[ShortText] = Field(max_length=8)
    relationships: list[ShortText] = Field(max_length=8)
    equations: list[ShortText] = Field(max_length=8)
    code_or_commands: list[ShortText] = Field(max_length=8)
    chart_or_ui_details: list[ShortText] = Field(max_length=8)


class _TransitionPayload(_StrictPayload):
    event_type: EventType
    summary: ShortText
    technical_changes: list[ShortText] = Field(max_length=6)


class _RegionPayload(_StrictPayload):
    region_type: RegionType
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)
    summary: ShortText
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def contained_in_frame(self) -> _RegionPayload:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("region extends outside the normalized frame")
        return self


class _FramePayload(_StrictPayload):
    frame_index: int = Field(ge=0)
    visual_types: list[VisualType] = Field(min_length=1)
    importance: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    summary: SummaryText
    visible_text: Annotated[
        str,
        BeforeValidator(_clamped(1_000)),
        StringConstraints(max_length=1_000),
    ]
    technical_details: _TechnicalDetailsPayload
    transition_after: _TransitionPayload | None
    regions: list[_RegionPayload] = Field(max_length=4)

    @model_validator(mode="after")
    def types_and_regions_are_consistent(self) -> _FramePayload:
        if len(set(self.visual_types)) != len(self.visual_types):
            raise ValueError("visual types must be unique")
        # A diagram/drawing region is stronger evidence for the frame's type
        # than the model's parallel visual_types list. Structured-output
        # providers cannot express this cross-field invariant in JSON Schema,
        # and otherwise valid analyses frequently omit the duplicate label.
        # Reconcile the redundant fields deterministically instead of paying
        # for an identical retry and discarding all earlier frame groups.
        for region in self.regions:
            if region.region_type not in self.visual_types:
                self.visual_types.append(region.region_type)
        return self


class _AnalysisPayload(_StrictPayload):
    sequence_summary: SequenceText
    frames: list[_FramePayload] = Field(min_length=1, max_length=2)


class OpenRouterVisualClient:
    """One purpose-built OpenRouter client for lecture-frame interpretation."""

    def __init__(
        self,
        model: str | None = None,
        *,
        timeout: float = 180.0,
        client: httpx.Client | None = None,
    ) -> None:
        configured_model = model or os.getenv("OPENROUTER_VIDEO_VISION_MODEL")
        self.model = (
            configured_model.strip() if configured_model else DEFAULT_VISUAL_MODEL
        )
        if not self.model:
            self.model = DEFAULT_VISUAL_MODEL
        self._owns_client = client is None
        if client is None:
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY is required for video vision")
            client = httpx.Client(
                headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
            )
        self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenRouterVisualClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @traced("video.vision.OpenRouterVisualClient.analyze", flow="visual_analysis")
    def analyze(
        self, frames: list[VisualFrame] | tuple[VisualFrame, ...]
    ) -> VisualAnalysis:
        validated_frames = _validate_frames(frames)
        prompt = _analysis_prompt(validated_frames)
        input_hash = _input_hash(self.model, prompt, validated_frames)
        last_failure: str | None = None
        request = _request_payload(self.model, prompt, validated_frames)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = provider_post(
                    self._client, OPENROUTER_CHAT_URL, json=request,
                    trace_metadata={"provider_attempt": attempt, "input_hash": input_hash,
                                    "frame_count": len(validated_frames), "provider_operation": "visual_analysis"},
                )
                response.raise_for_status()
                body = response.json()
                parsed = _parse_response(body, validated_frames)
                provenance = _provenance(
                    body,
                    requested_model=self.model,
                    input_hash=input_hash,
                    attempt=attempt,
                )
                return VisualAnalysis(
                    sequence_summary=parsed.sequence_summary,
                    frames=tuple(_frame_result(item) for item in parsed.frames),
                    provenance=provenance,
                )
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
                ValidationError,
            ) as error:
                # Image data and signed URLs must not escape through durable
                # job errors, but the provider's own complaint must: a bare
                # "failed after 2 attempts" hid a schema the provider rejected
                # on every single call, and the stage looked flaky instead of
                # broken.
                last_failure = _provider_failure(error)
                continue
        raise VisualAnalysisError(
            f"visual analysis failed after {MAX_ATTEMPTS} attempts"
            + (f": {last_failure}" if last_failure else "")
        ) from None


def _provider_failure(error: Exception) -> str:
    """The provider's reason for refusing, with payload echoes removed."""

    if isinstance(error, httpx.HTTPStatusError):
        body = " ".join((error.response.text or "").split())
        # A rejected request is echoed back with the images inlined; the
        # message is the part worth keeping.
        message = re.search(r'"message"\s*:\s*"([^"]{0,300})"', body)
        detail = redact(message.group(1) if message else body)
        return f"{error.response.status_code} {detail}".strip()
    return redact(f"{type(error).__name__}: {error}")


def _validate_frames(
    frames: list[VisualFrame] | tuple[VisualFrame, ...],
) -> tuple[VisualFrame, ...]:
    values = tuple(frames)
    if not 1 <= len(values) <= 2:
        raise ValueError("visual analysis requires one or two frames")
    indexes: set[int] = set()
    for frame in values:
        if (
            isinstance(frame.frame_index, bool)
            or not isinstance(frame.frame_index, int)
            or frame.frame_index < 0
        ):
            raise ValueError("frame_index must be a non-negative integer")
        if (
            isinstance(frame.timestamp_ms, bool)
            or not isinstance(frame.timestamp_ms, int)
            or frame.timestamp_ms < 0
        ):
            raise ValueError("timestamp_ms must be a non-negative integer")
        if not isinstance(frame.image, bytes) or not frame.image:
            raise ValueError("frame image cannot be empty")
        if not isinstance(frame.ocr_text, str):
            raise ValueError("frame OCR text must be a string")
        if not isinstance(frame.mime_type, str) or frame.mime_type not in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }:
            raise ValueError("frame image must be PNG, JPEG, or WebP")
        if frame.frame_index in indexes:
            raise ValueError("frame indexes must be unique")
        indexes.add(frame.frame_index)
    if any(
        current.timestamp_ms > following.timestamp_ms
        for current, following in zip(values, values[1:])
    ):
        raise ValueError("frames must be chronological")
    return values


def _analysis_prompt(frames: tuple[VisualFrame, ...]) -> str:
    inventory = "\n".join(
        f"Frame {frame.frame_index}: timestamp_ms={frame.timestamp_ms}; "
        f"OCR={frame.ocr_text[:2_000]!r}"
        for frame in frames
    )
    return f"{SYSTEM_INSTRUCTION}\n\nFrame inventory:\n{inventory}"


# Keywords that describe validation rather than shape. Strict structured
# output rejects some of them outright — a schema carrying "uniqueItems" is a
# 400 from the provider, which is how every visual-analysis call in production
# failed — and the response is validated against the real model afterwards
# anyway, so dropping them here costs nothing but the model's hint.
# Measured against the provider rather than assumed: with "uniqueItems"
# removed, a schema keeping maxLength, minItems, maxItems, and numeric bounds
# is accepted. Stripping more than this is actively harmful — dropping
# maxLength removed the model's only cue about length, and it promptly wrote a
# summary our own validator then rejected.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({"uniqueItems"})


def strict_schema(node: Any) -> Any:
    """Return the schema with provider-unsupported validation removed."""

    if isinstance(node, dict):
        return {
            key: strict_schema(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(node, list):
        return [strict_schema(value) for value in node]
    return node


def _request_payload(
    model: str, prompt: str, frames: tuple[VisualFrame, ...]
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": (
                    f"data:{frame.mime_type};base64,"
                    f"{b64encode(frame.image).decode('ascii')}"
                )
            },
        }
        for frame in frames
    )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "reasoning": {"effort": "low", "exclude": True},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "technical_lecture_visual_analysis",
                "strict": True,
                "schema": strict_schema(VISUAL_ANALYSIS_SCHEMA),
            },
        },
        "usage": {"include": True},
    }


def _input_hash(
    model: str, prompt: str, frames: tuple[VisualFrame, ...]
) -> str:
    value = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt": prompt,
        "schema": VISUAL_ANALYSIS_SCHEMA,
        "frames": [
            {
                "frame_index": frame.frame_index,
                "timestamp_ms": frame.timestamp_ms,
                "mime_type": frame.mime_type,
                "image_hash": sha256(frame.image).hexdigest(),
            }
            for frame in frames
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def visual_input_hash(
    model: str, frames: list[VisualFrame] | tuple[VisualFrame, ...]
) -> str:
    """Return the exact paid-request identity without contacting the provider.

    The worker uses this before a resumed visual group. Keeping validation,
    prompt construction, and hashing here guarantees that the preflight key is
    identical to the provenance written by :class:`OpenRouterVisualClient`.
    """

    validated_frames = _validate_frames(frames)
    return _input_hash(model, _analysis_prompt(validated_frames), validated_frames)


def _parse_response(
    body: Any, frames: tuple[VisualFrame, ...]
) -> _AnalysisPayload:
    if not isinstance(body, dict):
        raise ValueError("invalid provider response")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("provider returned no choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("provider returned no message")
    content = choice["message"].get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("provider returned empty content")
    parsed = _AnalysisPayload.model_validate_json(content)
    expected = [frame.frame_index for frame in frames]
    actual = [frame.frame_index for frame in parsed.frames]
    if actual != expected:
        raise ValueError("provider frame indexes do not match the request")
    if parsed.frames[-1].transition_after is not None:
        raise ValueError("the final frame cannot transition to an absent frame")
    return parsed


def _provenance(
    body: dict[str, Any],
    *,
    requested_model: str,
    input_hash: str,
    attempt: int,
) -> VisualProvenance:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("invalid provider usage")
    input_tokens = _nonnegative_int(usage.get("prompt_tokens"), "input tokens")
    output_tokens = _nonnegative_int(
        usage.get("completion_tokens"), "output tokens"
    )
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise ValueError("invalid provider cost")
    cost_usd = float(cost)
    if cost_usd < 0 or cost_usd != cost_usd or cost_usd == float("inf"):
        raise ValueError("invalid provider cost")
    actual_model = body.get("model") or requested_model
    if not isinstance(actual_model, str) or not actual_model.strip():
        raise ValueError("invalid provider model")
    return VisualProvenance(
        provider="openrouter",
        requested_model=requested_model,
        model=actual_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        input_hash=input_hash,
        prompt_version=PROMPT_VERSION,
        attempt=attempt,
    )


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid provider {label}")
    return value


def _frame_result(item: _FramePayload) -> FrameVisualAnalysis:
    details = item.technical_details
    transition = item.transition_after
    return FrameVisualAnalysis(
        frame_index=item.frame_index,
        visual_types=tuple(item.visual_types),
        importance=item.importance,
        confidence=item.confidence,
        summary=item.summary,
        visible_text=item.visible_text,
        technical_details=TechnicalDetails(
            concepts=tuple(details.concepts),
            relationships=tuple(details.relationships),
            equations=tuple(details.equations),
            code_or_commands=tuple(details.code_or_commands),
            chart_or_ui_details=tuple(details.chart_or_ui_details),
        ),
        transition_after=(
            VisualTransition(
                event_type=transition.event_type,
                summary=transition.summary,
                technical_changes=tuple(transition.technical_changes),
            )
            if transition is not None
            else None
        ),
        regions=tuple(
            VisualRegion(
                region_type=region.region_type,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                summary=region.summary,
                confidence=region.confidence,
            )
            for region in item.regions
        ),
    )
