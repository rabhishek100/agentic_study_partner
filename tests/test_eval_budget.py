"""Budget checks exercise transport, crashes and sharing, without paid calls."""
import asyncio
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from evals.budget import Budget, BudgetStop, experiment_lock


PRICES = {"fetched_at": "fixture", "models": {"fixture/model": {
    "context_length": 10000,
    "pricing": {"prompt": "0.000001", "completion": "0.000002"}}}}


def payload(**values):
    return {"model": "fixture/model", "messages": [{"role": "user", "content": "Q"}],
            "max_tokens": 100, **values}


def test_shared_cap_and_resume_keep_uncertain_reservations(tmp_path):
    budget = Budget(tmp_path, cap=1, prices=PRICES)
    budget.phase = "generation"
    budget.reserve("0.7", model="fixture/model")
    resumed = Budget(tmp_path, cap=1)
    resumed.phase = "judging"
    with pytest.raises(BudgetStop):
        resumed.reserve("0.4", model="fixture/model")
    assert resumed.committed == Decimal("0.7")
    with pytest.raises(BudgetStop):
        Budget(tmp_path, cap=2)


def test_reported_receipt_releases_only_unused_reservation(tmp_path):
    budget = Budget(tmp_path, prices=PRICES)
    call = budget.reserve("0.5", model="fixture/model")
    budget.settle(call, {"cost": 0.1, "prompt_tokens": 20})
    assert Budget(tmp_path).committed == Decimal("0.1")
    assert budget.data["calls"][0]["cost_kind"] == "provider_reported"


@pytest.mark.parametrize("usage", [None, {}, {"cost": None}])
def test_missing_receipt_does_not_become_free(tmp_path, usage):
    budget = Budget(tmp_path, prices=PRICES)
    call = budget.reserve("0.5", model="fixture/model")
    budget.settle(call, usage)
    assert budget.committed == Decimal("0.5")


def test_transport_guards_sync_async_retries_and_exact_prompt_capture(tmp_path):
    budget = Budget(tmp_path, prices=PRICES)
    received = []
    def respond(request):
        import json
        body = json.loads(request.content)
        received.append(body)
        return httpx.Response(200, json={"usage": {"cost": 0.001}, "choices": []})
    transport = httpx.MockTransport(respond)
    with budget.guard():
        with httpx.Client(transport=transport) as client:
            for _ in range(2):
                client.post("https://openrouter.ai/api/v1/chat/completions", json=payload())
        async def request():
            async with httpx.AsyncClient(transport=transport) as client:
                return await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload())
        asyncio.run(request())
    assert len(budget.data["calls"]) == 3 and budget.committed == Decimal("0.003")
    assert all(body["max_completion_tokens"] == 100 for body in received)
    assert all(body["provider"]["max_price"]["prompt"] == 1 for body in received)
    captures = list((tmp_path / "requests").glob("*.json"))
    assert len(captures) == 3 and all('"content": "Q"' in p.read_text() for p in captures)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in captures)


@pytest.mark.parametrize("body", [payload(model="unknown"), payload(plugins=[{"id": "web"}]),
                                  payload(stream=True), payload(models=["fallback"]),
                                  payload(n=2), payload(tools=[{"type": "web_search"}])])
def test_unpriced_calls_never_reach_transport(tmp_path, body):
    budget = Budget(tmp_path, prices=PRICES)
    received = []
    with budget.guard(), httpx.Client(transport=httpx.MockTransport(lambda r: received.append(r))) as client:
        with pytest.raises(BudgetStop):
            client.post("https://openrouter.ai/api/v1/chat/completions", json=body)
    assert not received and not budget.data["calls"]


def test_over_cap_never_sends_request(tmp_path):
    budget = Budget(tmp_path, cap=.001, prices=PRICES)
    received = []
    with budget.guard(), httpx.Client(transport=httpx.MockTransport(lambda r: received.append(r))) as client:
        with pytest.raises(BudgetStop):
            client.post("https://openrouter.ai/api/v1/chat/completions", json=payload())
    assert not received


def test_timeout_keeps_reservation_and_external_provider_is_blocked(tmp_path):
    budget = Budget(tmp_path, prices=PRICES)
    def fail(request):
        raise httpx.ReadTimeout("fixture timeout")
    with budget.guard(), httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.post("https://openrouter.ai/api/v1/chat/completions", json=payload())
        with pytest.raises(BudgetStop):
            client.post("https://unpriced.invalid/inference", json={})
    assert budget.committed > 0


def test_concurrent_reservations_cannot_race_past_cap(tmp_path):
    budget = Budget(tmp_path, cap=1, prices=PRICES)
    def reserve(_):
        try:
            budget.reserve(.6, model="fixture/model")
            return True
        except BudgetStop:
            return False
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(results) == 1 and budget.committed == Decimal("0.6")


def test_exclusive_run_lock_and_receipt_violation(tmp_path):
    with experiment_lock(tmp_path):
        with pytest.raises(BudgetStop), experiment_lock(tmp_path):
            pass
    budget = Budget(tmp_path, prices=PRICES)
    call = budget.reserve(.1, model="fixture/model")
    with pytest.raises(BudgetStop):
        budget.settle(call, {"cost": .2})
    assert Budget(tmp_path).committed == Decimal("0.2")
    with pytest.raises(BudgetStop):
        Budget(tmp_path).reserve(.01, model="fixture/model")


@pytest.mark.parametrize("value", [-1, 0, 3, "NaN", "Infinity"])
def test_invalid_experiment_ceiling_is_rejected(tmp_path, value):
    with pytest.raises(BudgetStop):
        Budget(tmp_path, cap=value, prices=PRICES)
