"""OpenRouter Luna clients with provider-reported usage."""

from __future__ import annotations

import os
from typing import Any, TypeVar

from pydantic import BaseModel

from video.models import reported_cost_usd


DEFAULT_INTERVIEW_MODEL = "openai/gpt-5.6-luna"


class InterviewModelError(RuntimeError):
    pass


Schema = TypeVar("Schema", bound=BaseModel)


def model_name() -> str:
    return (
        os.getenv("OPENROUTER_INTERVIEW_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_INTERVIEW_MODEL
    )


def structured_model(schema: type[Schema], *, temperature: float = 0.1):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise InterviewModelError("OPENROUTER_API_KEY is required for interviews")

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=model_name(),
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=temperature,
        extra_body={
            "usage": {"include": True},
            "reasoning": {
                "effort": os.getenv("OPENROUTER_INTERVIEW_REASONING", "low"),
                "exclude": True,
            },
        },
    )
    return model.with_structured_output(
        schema,
        method="json_schema",
        include_raw=True,
    )


def invoke_structured(client: Any, messages: list[Any], schema: type[Schema]) -> tuple[Schema, float]:
    response = client.invoke(messages)
    if isinstance(response, dict):
        parsed = response.get("parsed")
        raw = response.get("raw")
    else:
        parsed = response
        raw = response
    if not isinstance(parsed, schema):
        try:
            parsed = schema.model_validate(parsed)
        except Exception as error:
            raise InterviewModelError("the interview model returned invalid structure") from error
    return parsed, reported_cost_usd(raw)
