"""Stage overrides must not silently change independent readers or reviewers."""
import json
from unittest.mock import patch

import pytest

from interviews.contracts import AnswerEvaluation, InterviewQuestion
from interviews.models import structured_model
from model_routing import provider_options, structured_client
from revision_sheets.generate import Draft, SheetPatch, config_key, revision_model
from revision_sheets.review import FigureBatch, Inventory, Review
from scripts.screen_model_candidates import candidate_environment, LUNA, QWEN


def test_provider_route_is_exact_model_only(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER_ROUTES", json.dumps({"cheap/model": ["streamlake/fp8"]}))
    assert provider_options("cheap/model")["provider"] == {
        "only": ["streamlake/fp8"], "allow_fallbacks": False, "require_parameters": True}
    assert provider_options("other/model") == {}


@pytest.mark.parametrize("value", ['[]', '{"cheap/model":[]}', '{"cheap/model":"provider"}',
                                   '{"cheap/model":[1]}', '{"cheap/model":[""]}'])
def test_invalid_provider_routes_fail_before_requests(monkeypatch, value):
    monkeypatch.setenv("OPENROUTER_PROVIDER_ROUTES", value)
    with pytest.raises(ValueError):
        provider_options("cheap/model")


def test_grader_override_keeps_interviewer_on_original_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setenv("OPENROUTER_INTERVIEW_MODEL", "original/model")
    monkeypatch.setenv("OPENROUTER_INTERVIEW_GRADER_MODEL", "cheap/grader")
    with patch("langchain_openai.ChatOpenAI") as factory:
        structured_model(AnswerEvaluation)
        assert factory.call_args.kwargs["model"] == "cheap/grader"
        structured_model(InterviewQuestion)
        assert factory.call_args.kwargs["model"] == "original/model"


def test_sheet_author_override_preserves_independent_stages(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setenv("OPENROUTER_REVISION_MODEL", "original/model")
    monkeypatch.setenv("OPENROUTER_REVISION_JUDGE_MODEL", "original/judge")
    monkeypatch.setenv("OPENROUTER_REVISION_AUTHOR_MODEL", "cheap/author")
    monkeypatch.setenv("OPENROUTER_REVISION_FIGURE_MODEL", "vision/model")
    with patch("langchain_openai.ChatOpenAI") as factory:
        for schema, judge, expected in [(Draft, False, "cheap/author"),
                                        (SheetPatch, False, "cheap/author"),
                                        (Inventory, False, "original/model"),
                                        (FigureBatch, False, "vision/model"),
                                        (Review, True, "original/judge")]:
            revision_model(schema, judge=judge)
            assert factory.call_args.kwargs["model"] == expected


def test_sheet_stage_changes_invalidate_derived_cache_key(monkeypatch):
    for name in ("OPENROUTER_REVISION_AUTHOR_MODEL", "OPENROUTER_REVISION_INVENTORY_MODEL",
                 "OPENROUTER_REVISION_FIGURE_MODEL", "OPENROUTER_PROVIDER_ROUTES"):
        monkeypatch.delenv(name, raising=False)
    original = config_key()
    monkeypatch.setenv("OPENROUTER_REVISION_FIGURE_MODEL", "vision/model")
    vision_key = config_key()
    assert vision_key != original
    monkeypatch.setenv("OPENROUTER_REVISION_AUTHOR_MODEL", "cheap/author")
    assert config_key() != vision_key
    before_route = config_key()
    monkeypatch.setenv("OPENROUTER_PROVIDER_ROUTES", '{"cheap/author":["provider"]}')
    assert config_key() != before_route


def test_candidate_environment_does_not_change_other_roles_or_review_model():
    sheet = candidate_environment("07", base={"UNRELATED": "preserved"})
    assert sheet["OPENROUTER_REVISION_AUTHOR_MODEL"] == QWEN
    assert sheet["OPENROUTER_REVISION_MODEL"] == LUNA
    assert sheet["OPENROUTER_REVISION_FIGURE_MODEL"] == LUNA
    assert sheet["OPENROUTER_REVISION_JUDGE_MODEL"] == LUNA
    assert sheet["UNRELATED"] == "preserved"
    grader = candidate_environment("06", base={})
    assert grader["OPENROUTER_INTERVIEW_GRADER_MODEL"] == QWEN
    assert grader["OPENROUTER_INTERVIEW_MODEL"] == LUNA


def test_json_compatibility_hint_is_in_the_actual_structured_request(monkeypatch):
    import httpx
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel
    class Result(BaseModel):
        answer: str
    received = []
    def respond(request):
        body = json.loads(request.content)
        received.append(body)
        if not any("json" in str(m["content"]).lower() for m in body["messages"]):
            return httpx.Response(400, json={"error": {"message": "JSON-mode requests must mention json"}})
        return httpx.Response(200, json={"id": "fixture", "model": QWEN,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"answer":"42"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setenv("OPENROUTER_INTERVIEW_MODEL", QWEN)
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport, patch(
            "langchain_openai.ChatOpenAI", side_effect=lambda **kwargs: ChatOpenAI(**kwargs, http_client=transport)):
        result = structured_model(Result).invoke([HumanMessage(content="Compute six times seven.")])
    assert result["parsed"].answer == "42"
    assert json.dumps(Result.model_json_schema(), ensure_ascii=False, sort_keys=True) in received[0]["messages"][0]["content"]
    assert len(received) == 1
    assert received[0]["response_format"]["type"] == "json_schema"
    assert received[0]["messages"][-1]["content"] == "Compute six times seven."


def test_other_models_keep_their_existing_structured_client():
    sentinel = object()
    assert structured_client(sentinel, model=LUNA) is sentinel
