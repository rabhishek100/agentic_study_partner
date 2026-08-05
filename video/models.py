"""Model clients for video answering, with provider-reported cost.

The book path builds its own client the same way. Video keeps a separate
factory for one reason: a video turn has a hard USD cap it must record and
display, so it asks OpenRouter to return usage on every call and reads the
cost back off the response. Everything else — base URL, retries, timeout,
reasoning configuration — is the same proven client the book workflow uses.
"""

from __future__ import annotations

import os
from typing import Any, Protocol


DEFAULT_ANSWER_MODEL = "openai/gpt-5.6-luna"
DEFAULT_CONTROL_MODEL = "openai/gpt-5.6-luna"
MAXIMUM_TURN_COST_USD = 0.05
# A whole-lecture request reads the complete transcript instead of eight
# passages, so it costs several times an ordinary turn by construction. One
# ceiling for both would either reject every summary or stop capping QA.
MAXIMUM_LECTURE_COST_USD = 0.40


class VideoModelError(RuntimeError):
    """The configured model client cannot be built."""


class ChatModel(Protocol):
    def invoke(self, messages): ...

    def stream(self, messages): ...


def _api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise VideoModelError("OPENROUTER_API_KEY is required for video answers")
    return key


def answer_model(*, max_tokens: int | None = None) -> ChatModel:
    """Build the generation client used to synthesize a grounded answer."""

    from langchain_openai import ChatOpenAI

    options: dict[str, Any] = {}
    if max_tokens is not None:
        options["max_tokens"] = max_tokens
    return ChatOpenAI(
        model=os.getenv("OPENROUTER_VIDEO_ANSWER_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_ANSWER_MODEL,
        api_key=_api_key(),
        base_url="https://openrouter.ai/api/v1",
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        extra_body={
            "usage": {"include": True},
            "reasoning": {
                "effort": os.getenv("OPENROUTER_GENERATION_REASONING", "none"),
                "exclude": True,
            },
        },
        **options,
    )


def control_model(schema):
    """Build the small structured-output client used to route a turn."""

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_CONTROL_MODEL") or DEFAULT_CONTROL_MODEL,
        api_key=_api_key(),
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=0,
        reasoning={
            "effort": os.getenv("OPENROUTER_CONTROL_REASONING", "low"),
            "exclude": True,
        },
    )
    return model.with_structured_output(schema, method="json_schema")


def reported_cost_usd(response: Any) -> float:
    """Read the provider's own cost off a completed response.

    Estimating from token counts would invent a number the receipt can
    contradict. When the provider reports nothing, the turn records zero and
    says so rather than guessing.
    """

    metadata = getattr(response, "response_metadata", None) or {}
    for container in (metadata, metadata.get("token_usage") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("cost", "total_cost", "cost_usd"):
            value = container.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return round(float(value), 6)
    return 0.0
