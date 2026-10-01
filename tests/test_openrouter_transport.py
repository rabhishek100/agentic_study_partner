"""Exercise the installed SDK request builder, without transmitting or billing."""
import json

import httpx
import pytest

from evals.judge import (OpenRouterAnswerJudge, OpenRouterInterviewJudge,
                         OpenRouterInterviewSequenceJudge, OpenRouterInterviewInteractionJudge)
from study.analyze import _openrouter_model, ModelDecision
from video.models import control_model


class Captured(BaseException):
    pass


@pytest.mark.parametrize("factory", [_openrouter_model, lambda: control_model(ModelDecision),
    lambda: OpenRouterAnswerJudge().model, lambda: OpenRouterInterviewJudge().model,
    lambda: OpenRouterInterviewSequenceJudge().model, lambda: OpenRouterInterviewInteractionJudge().model])
def test_structured_reasoning_stays_on_openrouter_chat_transport(monkeypatch, factory):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setenv("OPENROUTER_CONTROL_MODEL", "openai/gpt-6-luna")
    monkeypatch.setenv("OPENROUTER_JUDGE_MODEL", "openai/gpt-6-luna")
    requests = []
    def capture(client, request, **kwargs):
        requests.append(request)
        raise Captured()
    monkeypatch.setattr(httpx.Client, "send", capture)
    with pytest.raises(Captured):
        factory().invoke("Inspect transport using fixture input")
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "openrouter.ai"
    assert request.url.path == "/api/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["reasoning"]["exclude"] is True
    assert payload["response_format"]["type"] == "json_schema"
    assert "messages" in payload and "input" not in payload
