"""Cost-accounted OpenRouter calls for visual analysis and evaluation."""

from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from .artifacts import CostLedger, read_json, stable_hash, write_json


CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
MODELS_URL = "https://openrouter.ai/api/v1/models"
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class OpenRouterError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(
        self,
        cache_dir: Path,
        ledger: CostLedger,
        *,
        timeout: float = 180.0,
    ) -> None:
        load_dotenv()
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is missing")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )

    def close(self) -> None:
        self.client.close()

    def models(self) -> list[dict[str, Any]]:
        cache = self.cache_dir / "models.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < 3_600:
            return list(read_json(cache, []) or [])
        response = self.client.get(MODELS_URL)
        response.raise_for_status()
        models = list(response.json().get("data") or [])
        write_json(cache, models)
        return models

    def price(self, model_id: str) -> tuple[float, float]:
        for model in self.models():
            if model.get("id") == model_id:
                pricing = model.get("pricing") or {}
                return (
                    float(pricing.get("prompt") or 0.0),
                    float(pricing.get("completion") or 0.0),
                )
        return (0.0, 0.0)

    def call_json(
        self,
        *,
        model: str,
        operation: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        images: list[Path] | None = None,
        max_tokens: int = 1_200,
        reasoning_effort: str | None = None,
        estimated_cost_usd: float = 0.01,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        images = images or []
        cache_key = stable_hash(
            {
                "model": model,
                "prompt": prompt,
                "schema": schema,
                "images": [_file_hash(path) for path in images],
                "reasoning_effort": reasoning_effort,
            }
        )
        cache = self.cache_dir / operation / f"{cache_key}.json"
        cached = read_json(cache)
        if cached:
            return dict(cached["result"]), dict(cached["provenance"])

        self.ledger.reserve(estimated_cost_usd, operation=operation)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images:
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            encoded = b64encode(path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )
        request: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "usage": {"include": True},
        }
        # Several inexpensive reasoning models spend the entire output budget
        # on hidden reasoning and return empty content unless it is explicitly
        # excluded. Visual extraction needs the schema result, not a chain of
        # thought, so low/excluded is the safe default.
        request["reasoning"] = {
            "effort": reasoning_effort or "low",
            "exclude": True,
        }

        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                response = self.client.post(CHAT_URL, json=request)
                response.raise_for_status()
                body = response.json()
                choices = body.get("choices") or []
                if not choices:
                    raise OpenRouterError("provider returned no choices")
                raw = choices[0]["message"].get("content") or ""
                if isinstance(raw, list):
                    raw = "".join(
                        str(part.get("text") or "")
                        for part in raw
                        if isinstance(part, dict)
                    )
                usage = body.get("usage") or {}
                provenance = {
                    "requested_model": model,
                    "model": body.get("model") or model,
                    "prompt_hash": sha256(prompt.encode()).hexdigest(),
                    "input_tokens": int(usage.get("prompt_tokens") or 0),
                    "output_tokens": int(usage.get("completion_tokens") or 0),
                    "cost_usd": float(usage.get("cost") or 0.0),
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "cache_key": cache_key,
                    "attempt": attempt,
                }
                cleaned = FENCE.sub("", str(raw).strip())
                if not cleaned:
                    self.ledger.record(
                        {
                            "operation": operation,
                            **provenance,
                            "status": "empty_response",
                            "finish_reason": choices[0].get("finish_reason"),
                            "created_at": time.time(),
                        }
                    )
                    raise OpenRouterError(
                        "provider returned empty content "
                        f"(model={provenance['model']}, "
                        f"finish={choices[0].get('finish_reason')}, "
                        f"output_tokens={provenance['output_tokens']})"
                    )
                try:
                    result = json.loads(cleaned)
                except json.JSONDecodeError:
                    self.ledger.record(
                        {
                            "operation": operation,
                            **provenance,
                            "status": "invalid_json",
                            "created_at": time.time(),
                        }
                    )
                    raise
                self.ledger.record(
                    {
                        "operation": operation,
                        **provenance,
                        "status": "success",
                        "created_at": time.time(),
                    }
                )
                write_json(cache, {"result": result, "provenance": provenance})
                return dict(result), provenance
            except Exception as error:  # noqa: BLE001 - bounded retry
                last_error = error
                if attempt == 1:
                    time.sleep(1)
        raise OpenRouterError(f"{operation} failed: {last_error}")

    def embed(
        self,
        *,
        model: str,
        operation: str,
        text: str | None = None,
        image: Path | None = None,
        input_type: str | None = None,
        dimensions: int | None = None,
        estimated_cost_usd: float = 0.002,
    ) -> tuple[list[float], dict[str, Any]]:
        if not text and image is None:
            raise ValueError("text or image is required")
        image_hash = _file_hash(image) if image else None
        cache_key = stable_hash(
            {
                "model": model,
                "text": text,
                "image": image_hash,
                "input_type": input_type,
                "dimensions": dimensions,
            }
        )
        cache = self.cache_dir / operation / f"{cache_key}.json"
        cached = read_json(cache)
        if cached:
            return list(cached["embedding"]), dict(cached["provenance"])

        self.ledger.reserve(estimated_cost_usd, operation=operation)
        if image:
            mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
            encoded = b64encode(image.read_bytes()).decode("ascii")
            parts: list[dict[str, Any]] = []
            if text:
                parts.append({"type": "text", "text": text})
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )
            request_input: Any = [{"content": parts}]
        else:
            request_input = text
        request: dict[str, Any] = {
            "model": model,
            "input": request_input,
            "encoding_format": "float",
        }
        if input_type:
            request["input_type"] = input_type
        if dimensions:
            request["dimensions"] = dimensions

        started = time.monotonic()
        response = self.client.post(EMBEDDINGS_URL, json=request)
        response.raise_for_status()
        body = response.json()
        data = body.get("data") or []
        if not data:
            raise OpenRouterError("embedding provider returned no data")
        embedding = list(data[0].get("embedding") or [])
        usage = body.get("usage") or {}
        provenance = {
            "requested_model": model,
            "model": body.get("model") or model,
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "cost_usd": float(usage.get("cost") or 0.0),
            "latency_seconds": round(time.monotonic() - started, 3),
            "cache_key": cache_key,
        }
        self.ledger.record(
            {
                "operation": operation,
                **provenance,
                "created_at": time.time(),
            }
        )
        write_json(cache, {"embedding": embedding, "provenance": provenance})
        return embedding, provenance

    def embed_texts(
        self,
        *,
        model: str,
        operation: str,
        texts: list[str],
        estimated_cost_usd: float = 0.01,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        """Embed a text corpus in one cached provider request.

        OpenRouter accepts an array of strings. Batching the lecture corpus is
        materially faster and gives the cost ledger one auditable operation
        instead of hundreds of tiny HTTP calls.
        """

        if not texts:
            return [], {"cost_usd": 0.0, "latency_seconds": 0.0}
        cache_key = stable_hash({"model": model, "texts": texts})
        cache = self.cache_dir / operation / f"{cache_key}.json"
        cached = read_json(cache)
        if cached:
            return [list(value) for value in cached["embeddings"]], dict(
                cached["provenance"]
            )

        self.ledger.reserve(estimated_cost_usd, operation=operation)
        started = time.monotonic()
        response = self.client.post(
            EMBEDDINGS_URL,
            json={"model": model, "input": texts, "encoding_format": "float"},
        )
        response.raise_for_status()
        body = response.json()
        data = sorted(body.get("data") or [], key=lambda item: int(item["index"]))
        if len(data) != len(texts):
            raise OpenRouterError(
                f"embedding provider returned {len(data)} rows for {len(texts)} texts"
            )
        embeddings = [list(item.get("embedding") or []) for item in data]
        usage = body.get("usage") or {}
        provenance = {
            "requested_model": model,
            "model": body.get("model") or model,
            "input_tokens": int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0),
            "cost_usd": float(usage.get("cost") or 0.0),
            "latency_seconds": round(time.monotonic() - started, 3),
            "cache_key": cache_key,
            "item_count": len(texts),
        }
        self.ledger.record(
            {"operation": operation, **provenance, "created_at": time.time()}
        )
        write_json(cache, {"embeddings": embeddings, "provenance": provenance})
        return embeddings, provenance


def _file_hash(path: Path | None) -> str | None:
    if path is None:
        return None
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
