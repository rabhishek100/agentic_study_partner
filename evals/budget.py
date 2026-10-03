"""Persistent reservations for one isolated evaluation experiment, not billing."""
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

import httpx


class BudgetStop(BaseException):
    """Bypasses ordinary provider retry/repair catches; the runner checkpoints it."""


def money(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise BudgetStop("Unknown price/cost") from error
    if not result.is_finite() or result < 0:
        raise BudgetStop("Invalid price/cost")
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


@contextmanager
def experiment_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BudgetStop("This experiment is already running") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def pricing_snapshot():
    """Read-only public metadata; freeze in the experiment before any paid call."""
    result = {}
    with httpx.Client(timeout=30) as client:
        for endpoint in ("models", "embeddings/models"):
            response = client.get(f"https://openrouter.ai/api/v1/{endpoint}")
            response.raise_for_status()
            for model in response.json()["data"]:
                result[model["id"]] = {key: model.get(key) for key in
                                      ("pricing", "context_length", "top_provider", "canonical_slug")}
    return {"fetched_at": datetime.now(UTC).isoformat(), "models": result}


class Budget:
    def __init__(self, directory, *, cap=2, prices=None):
        self.directory = Path(directory)
        self.path = self.directory / "budget.json"
        self.lock = RLock()
        self.case_id, self.phase = None, "generation"
        requested = money(cap)
        if not 0 < requested <= 2:
            raise BudgetStop("Experiment ceiling must be greater than zero and at most $2")
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if money(self.data["cap_usd"]) != requested:
                raise BudgetStop("Resume must preserve the original experiment ceiling")
        else:
            if prices is None:
                raise BudgetStop("A frozen pricing snapshot is required")
            self.data = {"version": 1, "cap_usd": str(requested), "pricing": prices, "calls": []}
            atomic_json(self.path, self.data)

    @property
    def committed(self):
        return sum((money(call.get("cost_usd", call["reserved_usd"]))
                    for call in self.data["calls"]), Decimal(0))

    def reserve(self, amount, *, model, request=None):
        amount = money(amount)
        with self.lock:
            if self.data.get("blocked"):
                raise BudgetStop(self.data["blocked"])
            if self.committed + amount > money(self.data["cap_usd"]):
                raise BudgetStop("Next request would exceed the experiment ceiling")
            call = {"id": uuid4().hex, "case_id": self.case_id, "phase": self.phase,
                    "model": model, "reserved_usd": str(amount), "status": "reserved",
                    "cost_kind": "conservative_reservation"}
            if request is not None:
                relative = f"requests/{call['id']}.json"
                atomic_json(self.directory / relative, request)
                call["capture"] = relative
            self.data["calls"].append(call)
            atomic_json(self.path, self.data)  # durable before transmission
            return call["id"]

    def settle(self, call_id, usage):
        with self.lock:
            call = next(call for call in self.data["calls"] if call["id"] == call_id)
            if isinstance(usage, dict) and usage.get("cost") is not None:
                reported = money(usage["cost"])
                call.update(cost_usd=str(reported), status="settled", cost_kind="provider_reported",
                            usage={key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")})
                atomic_json(self.path, self.data)
                if reported > money(call["reserved_usd"]) or self.committed > money(self.data["cap_usd"]):
                    self.data["blocked"] = "Provider receipt exceeded its reservation; pricing investigation required"
                    atomic_json(self.path, self.data)
                    raise BudgetStop("Provider receipt exceeded its reservation; stop and investigate pricing")
            # Missing receipt, timeout or interruption keeps the entire reserve.

    def record_response(self, call_id, response):
        """Keep private provider bodies for debugging without paying to replay.

        Never store transport headers or credentials. Failed calls without a
        usage receipt still retain their conservative reservation.
        """
        from hashlib import sha256
        with self.lock:
            call = next(call for call in self.data["calls"] if call["id"] == call_id)
            relative = f"responses/{call_id}.json"
            try:
                payload = response.json()
            except ValueError:
                payload = {"unparsed_body": response.text}
            atomic_json(self.directory / relative, payload)
            call.update(response_capture=relative, response_status=response.status_code,
                        response_sha256=sha256((self.directory / relative).read_bytes()).hexdigest())
            atomic_json(self.path, self.data)

    def prepare(self, request):
        if request.url.host != "openrouter.ai" or request.url.path not in {"/api/v1/chat/completions", "/api/v1/embeddings"}:
            raise BudgetStop("Unpriced inference endpoint")
        try:
            payload = json.loads(request.content)
            model = payload["model"]
            metadata = self.data["pricing"]["models"][model]
            pricing = metadata["pricing"]
        except (KeyError, ValueError, httpx.RequestNotRead) as error:
            raise BudgetStop("Unpriced model or unreadable request") from error
        if payload.get("models") or payload.get("plugins") or payload.get("stream") or payload.get("n", 1) != 1:
            raise BudgetStop("Fallback models, paid plugins, provider streaming and multiple completions require separate budgeting")
        if any(tool.get("type") != "function" for tool in payload.get("tools", [])):
            raise BudgetStop("Unpriced provider tool")
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        # A byte per token is a conservative text bound, including JSON schema.
        # Image requests reserve the whole model context; no image-token guess.
        if b'"audio"' in encoded or b'"video"' in encoded or b'"file"' in encoded:
            raise BudgetStop("Unpriced audio/video/file input")
        context = int(metadata["context_length"])
        input_bound = context if b'"image_url"' in encoded else min(context, len(encoded) + 1024)
        output_bound = 0
        if request.url.path.endswith("chat/completions"):
            original = payload.get("max_completion_tokens", payload.get("max_tokens", 16000))
            output_bound = min(int(original), 16000)
            if output_bound <= 0:
                raise BudgetStop("A positive output limit is required")
            payload.pop("max_tokens", None)
            payload["max_completion_tokens"] = output_bound
            payload["usage"] = {"include": True}
        candidates = [pricing, *pricing.get("overrides", [])]
        prompt_price = max(money(item.get("input_cache_write", item.get("prompt", pricing["prompt"])))
                           for item in candidates)
        prompt_price = max(prompt_price, *(money(item.get("prompt", pricing["prompt"])) for item in candidates))
        completion_price = max(money(item.get("internal_reasoning", item.get("completion", pricing.get("completion", 0)))) for item in candidates)
        completion_price = max(completion_price, *(money(item.get("completion", pricing.get("completion", 0))) for item in candidates))
        request_price = money(pricing.get("request", 0))
        image_price = money(pricing.get("image", 0))
        # Bound provider routing too: listed model prices need not be all endpoints' prices.
        provider = dict(payload.get("provider") or {})
        provider["max_price"] = {"prompt": float(prompt_price * 1_000_000),
                                 "completion": float(completion_price * 1_000_000),
                                 "request": float(request_price), "image": float(image_price)}
        payload["provider"] = provider
        amount = (input_bound * prompt_price + output_bound * completion_price + request_price
                  + (context * image_price if b'"image_url"' in encoded else 0)) * Decimal("1.2")
        call_id = self.reserve(amount, model=model, request=payload)
        headers = dict(request.headers)
        headers.pop("content-length", None)
        bounded = httpx.Request(request.method, request.url, headers=headers, json=payload,
                                extensions=request.extensions)
        return call_id, bounded

    @contextmanager
    def guard(self):
        """Temporary transport hook only inside the isolated eval command.

        Covers LangChain/OpenAI and raw httpx calls, including threads/retries.
        Non-OpenRouter inference and paid speech are outside this runner.
        """
        from unittest.mock import patch
        sync, asynchronous = httpx.Client.send, httpx.AsyncClient.send
        def allowed(request):
            host = request.url.host or ""
            return (request.method in {"GET", "HEAD"} or host in {"localhost", "127.0.0.1", "api.smith.langchain.com"}
                    or host.endswith(".grafana.net"))
        def send(client, request, **kwargs):
            if request.url.host != "openrouter.ai":
                if not allowed(request):
                    raise BudgetStop("Unpriced external write/provider endpoint")
                return sync(client, request, **kwargs)
            if request.method != "POST":
                return sync(client, request, **kwargs)
            call_id, bounded = self.prepare(request)
            response = sync(client, bounded, **kwargs)
            response.read()
            try:
                usage = response.json().get("usage")
            except ValueError:
                usage = None
            self.record_response(call_id, response)
            self.settle(call_id, usage)
            return response
        async def asend(client, request, **kwargs):
            if request.url.host != "openrouter.ai":
                if not allowed(request):
                    raise BudgetStop("Unpriced external write/provider endpoint")
                return await asynchronous(client, request, **kwargs)
            if request.method != "POST":
                return await asynchronous(client, request, **kwargs)
            call_id, bounded = self.prepare(request)
            response = await asynchronous(client, bounded, **kwargs)
            await response.aread()
            try:
                usage = response.json().get("usage")
            except ValueError:
                usage = None
            self.record_response(call_id, response)
            self.settle(call_id, usage)
            return response
        with patch.object(httpx.Client, "send", send), patch.object(httpx.AsyncClient, "send", asend):
            yield
