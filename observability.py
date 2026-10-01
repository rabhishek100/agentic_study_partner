"""Shared LangSmith boundaries for HTTP, Python workflows and raw providers.

LangChain owns its model spans; this module never wraps those calls a second
time. Telemetry failures cannot change application results. Disabled tracing
does not construct clients, serialize inputs, or send requests.
"""

from __future__ import annotations

import atexit
import inspect
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import wraps
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from langsmith import tracing_context
from langsmith.run_helpers import get_current_run_tree, get_tracing_context
from langsmith.run_trees import RunTree, get_cached_client
from langsmith.utils import tracing_is_enabled

import operations_telemetry as operations

logger = logging.getLogger("study_partner.tracing")
_OPERATIONS = ContextVar("study_partner_trace_operations", default=())
_SECRET = re.compile(r"authorization|api.?key|password|secret|token$|signed.?url|database.?url", re.I)
_DATA_URL = re.compile(r"data:[\w/+.-]+(?:;[\w=.-]+)*;base64,[A-Za-z0-9+/=]+")
_URL = re.compile(r"https?://[^\s\"<>]+")
_IDS = ("conversation_id", "session_id", "side_chat_id", "flow_id", "book_id", "book_ids",
        "video_id", "course_id", "sheet_id", "scope_key", "job_id", "attempt_count", "stage", "request_id")
_OMIT = {"self", "connection", "db", "client", "model", "dependencies", "runtime",
         "on_token", "progress", "audio", "payload", "images", "ctx"}


def safe_value(value: Any, depth: int = 0, _budget=None) -> Any:
    """Bound captures and exclude credentials, objects and inline media."""
    if _budget is None:
        _budget = [64000, 3000]
    _budget[1] -= 1
    if _budget[1] < 0 or _budget[0] <= 0:
        return "[capture limit]"
    if depth > 6:
        return "[depth limit]"
    if isinstance(value, Enum):
        return safe_value(value.value, depth, _budget)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (UUID, Decimal, date, datetime)):
        return str(value)
    if isinstance(value, str):
        value = _DATA_URL.sub("[inline media omitted]", value)
        def clean_url(match):
            parts = urlsplit(match.group())
            # Credentials and signed query strings never belong in traces.
            return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
        value = _URL.sub(clean_url, value)
        limit = min(12000, _budget[0])
        _budget[0] -= min(len(value), limit)
        return value[:limit] + (" [truncated]" if len(value) > limit else "")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"media_bytes": len(value)}
    if isinstance(value, dict):
        return {str(k): "[redacted]" if _SECRET.search(str(k)) else safe_value(v, depth + 1, _budget)
                for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_value(v, depth + 1, _budget) for v in list(value)[:40]]
    if hasattr(type(value), "model_fields"):
        return safe_value({k: getattr(value, k) for k in type(value).model_fields}, depth, _budget)
    if is_dataclass(value) and not isinstance(value, type):
        return safe_value({f.name: getattr(value, f.name) for f in fields(value)}, depth, _budget)
    return f"[{type(value).__name__}]"


def _telemetry(action):
    try:
        return action()
    except Exception as error:
        logger.warning("LangSmith telemetry failed (%s)", type(error).__name__)
        return None


@contextmanager
def _langsmith_span(name: str, *, inputs=None, metadata=None, run_type="chain", tags=None):
    """Nest an SDK run in the current graph/evaluation context, if enabled."""
    context = get_tracing_context()
    enabled = tracing_is_enabled(context)
    if not enabled:
        yield None
        return
    inherited = context.get("metadata") or {}
    run_metadata = {**inherited, **(metadata or {})}
    run_tags = list(dict.fromkeys([*(context.get("tags") or []), *(tags or [])]))
    parent = get_current_run_tree()
    def create():
        kwargs = dict(name=name, run_type=run_type, inputs=safe_value(inputs or {}),
                      extra={"metadata": safe_value(run_metadata)}, tags=run_tags)
        if parent:
            return parent.create_child(**kwargs)
        if context.get("project_name"):
            kwargs["project_name"] = context["project_name"]
        return RunTree(**kwargs, client=context.get("client") or get_cached_client(anonymizer=safe_value))
    run = _telemetry(create)
    if run is None:
        yield None
        return
    if enabled is True:
        _telemetry(run.post)
    token = _OPERATIONS.set((*_OPERATIONS.get(), run))
    with tracing_context(parent=run, metadata=run_metadata, tags=run_tags, enabled=enabled):
        try:
            yield run
        except BaseException as error:
            # Exceptions can include request URLs or credentials; omit the text.
            cancelled = type(error).__name__ in {"CancelledError", "CancellationRequested", "DeckCancellationRequested"}
            _telemetry(lambda: run.end(error=None if cancelled else type(error).__name__,
                                      metadata={"outcome": "cancelled" if cancelled else "failed"}))
            raise
        finally:
            if not run.end_time:
                _telemetry(run.end)
            if enabled is True:
                _telemetry(run.patch)
            _OPERATIONS.reset(token)


@contextmanager
def span(name: str, *, inputs=None, metadata=None, run_type="chain", tags=None):
    """Shared boundaries; operational exports carry no model payloads."""
    with operations.operation(name, metadata):
        with _langsmith_span(name, inputs=inputs, metadata=metadata, run_type=run_type, tags=tags) as run:
            if run is not None:
                operations.annotate(langsmith_trace_id=str(run.trace_id))
            yield run


def _enclosing_runs():
    # Recent SDKs retain parent IDs rather than parent object references.
    # Keep only our active operation stack, including across copied threads.
    current = get_current_run_tree()
    seen = set()
    for run in [current, *reversed(_OPERATIONS.get())]:
        if run is not None and run.id not in seen:
            seen.add(run.id)
            yield run


def annotate(**metadata):
    """Put correlation IDs on the operation and its enclosing HTTP/job runs."""
    operations.annotate(**metadata)
    for run in _enclosing_runs():
        _telemetry(lambda run=run: run.add_metadata(safe_value(metadata)))


def organize(flow):
    """Classify enclosing HTTP runs once, retaining specific child flow names."""
    operations.organize(flow)
    for run in _enclosing_runs():
        if not run.metadata.get("flow"):
            _telemetry(lambda run=run: run.add_metadata({"flow": flow}))
            _telemetry(lambda run=run: run.add_tags([f"flow:{flow}"]))


def record_error(error: BaseException, *, outcome="failed"):
    """Mark handled worker/provider failures without changing retry behavior."""
    operations.error(error, outcome=outcome)
    run = get_current_run_tree()
    if run:
        def capture():
            run.error = None if outcome == "cancelled" else type(error).__name__
            run.add_metadata({"outcome": outcome})
        _telemetry(capture)


def record_estimate(cost_usd):
    """Keep configured audio estimates distinct from provider receipts."""
    run = get_current_run_tree()
    if run:
        _telemetry(lambda: run.add_metadata({"estimated_cost_usd": float(cost_usd), "cost_source": "estimate"}))


def record_metadata(**metadata):
    operations.annotate(**metadata)
    run = get_current_run_tree()
    if run:
        _telemetry(lambda: run.add_metadata(safe_value(metadata)))


def _correlation(arguments):
    metadata = {k: safe_value(v) for k, v in arguments.items() if k in _IDS and v is not None}
    for key in ("job", "state", "session", "binding", "request", "source"):
        value = arguments.get(key)
        if value is None:
            continue
        for name in _IDS:
            item = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
            if item is not None:
                metadata[name] = safe_value(item)
        if key == "job":
            job_id = value.get("id") if isinstance(value, dict) else getattr(value, "id", None)
            if job_id is not None:
                metadata["job_id"] = str(job_id)
    self = arguments.get("self")
    if self is not None and hasattr(self, "binding"):
        metadata.update(_correlation({"binding": self.binding}))
    identity = metadata.get("session_id") or metadata.get("conversation_id") or metadata.get("flow_id") or metadata.get("job_id")
    if identity:
        metadata["thread_id"] = str(identity)
    return metadata


def traced(name: str, *, flow: str, run_type="chain"):
    """Trace real Python operations without serializing dependency objects."""
    def decorate(function):
        signature = inspect.signature(function)
        def operation(args, kwargs):
            arguments = signature.bind_partial(*args, **kwargs).arguments
            metadata = {"flow": flow, "operation": name, **_correlation(arguments)}
            inputs = {k: v for k, v in arguments.items() if k not in _OMIT}
            return span(name, inputs=inputs, metadata=metadata, run_type=run_type,
                        tags=[f"flow:{flow}"]), metadata
        def finish(run, result):
            if run is not None:
                _telemetry(lambda: run.end(outputs={**(run.outputs or {}), "result": safe_value(result)}))
            if name == "narration.cache.load":
                record_metadata(cache_hit=result is not None)
            values = result if isinstance(result, tuple) else (result,)
            for value in values:
                annotate(**_correlation({"request": value}))
            job = result.get("job", result) if isinstance(result, dict) else result
            if isinstance(job, tuple) and job:
                job = job[0]
            ids = _correlation({"job": job}) if name.endswith("enqueue") else {}
            annotate(**ids)
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def wrapper(*args, **kwargs):
                _telemetry(lambda: operations.configure("study-partner-" + flow.replace("_", "-") if flow.endswith("voice") else "study-partner-cli"))
                if not tracing_is_enabled() and not operations.enabled():
                    return await function(*args, **kwargs)
                prepared = _telemetry(lambda: operation(args, kwargs))
                if prepared is None:
                    return await function(*args, **kwargs)
                boundary, metadata = prepared
                with boundary as run:
                    _telemetry(lambda: organize(flow))
                    annotate(**{k: v for k, v in metadata.items() if k in _IDS or k == "thread_id"})
                    result = await function(*args, **kwargs)
                    _telemetry(lambda: finish(run, result))
                    return result
        else:
            @wraps(function)
            def wrapper(*args, **kwargs):
                _telemetry(lambda: operations.configure("study-partner-" + flow.replace("_", "-") if flow.endswith("voice") else "study-partner-cli"))
                if not tracing_is_enabled() and not operations.enabled():
                    return function(*args, **kwargs)
                prepared = _telemetry(lambda: operation(args, kwargs))
                if prepared is None:
                    return function(*args, **kwargs)
                boundary, metadata = prepared
                with boundary as run:
                    _telemetry(lambda: organize(flow))
                    annotate(**{k: v for k, v in metadata.items() if k in _IDS or k == "thread_id"})
                    result = function(*args, **kwargs)
                    _telemetry(lambda: finish(run, result))
                    return result
        wrapper.__trace_name__ = name
        return wrapper
    return decorate


def in_current_context(function):
    """Capture before starting a manual thread; each invocation needs a copy."""
    context = copy_context()
    @wraps(function)
    def run(*args, **kwargs):
        return context.copy().run(function, *args, **kwargs)
    return run


def record_voice_metrics(payload):
    """Expose provider SDK usage events without exporting transcript/audio."""
    run = get_current_run_tree()
    if run:
        def capture():
            details = payload.get("metadata") or {}
            run.add_metadata({"provider": "livekit", "metric_type": payload.get("type"),
                              "provider_request_id": payload.get("request_id"),
                              "ls_model_name": details.get("model_name"), "cost_source": "unreported"})
            run.add_tags(["provider:livekit"])
            run.outputs = {"metrics": safe_value(payload)}
        _telemetry(capture)


def provider_post(client, url, *, trace_metadata=None, **kwargs):
    """One child per physical OpenRouter attempt, including retryable failures."""
    if not tracing_is_enabled() and not operations.enabled():
        return client.post(url, **kwargs)
    request = kwargs.get("json") or kwargs.get("data") or {}
    endpoint = str(url).rstrip("/").rsplit("/", 1)[-1]
    kind = {"completions": "llm", "embeddings": "embedding"}.get(endpoint, "tool")
    with span(f"openrouter.{endpoint}", inputs=request, run_type=kind,
              metadata={"ls_provider": "openrouter", "ls_model_name": request.get("model"),
                        "provider": "openrouter", "cost_source": "unreported", **(trace_metadata or {})},
              tags=["provider:openrouter"]) as run:
        response = client.post(url, **kwargs)
        operations.http_status(response.status_code)
        if run is None:
            return response
        def capture():
            outputs = {"status_code": response.status_code}
            if response.status_code >= 400:
                run.error = f"HTTP {response.status_code}"
            else:
                try:
                    body = response.json()
                except (ValueError, UnicodeError):
                    body = None
                if isinstance(body, dict):
                    if body.get("model"):
                        run.add_metadata({"ls_model_name": body["model"]})
                    usage = body.get("usage") or {}
                    usage_metadata = {}
                    for source, target in (("prompt_tokens", "input_tokens"),
                                           ("completion_tokens", "output_tokens"),
                                           ("total_tokens", "total_tokens"), ("cost", "total_cost")):
                        if usage.get(source) is not None:
                            usage_metadata[target] = usage[source]
                    if usage_metadata:
                        run.add_metadata({"usage_metadata": usage_metadata,
                                          "cost_source": "provider_receipt" if "cost" in usage else "unreported"})
                    if endpoint == "embeddings":
                        data = body.get("data") or []
                        outputs["vectors"] = [{"index": item.get("index"),
                                               "dimensions": len(item.get("embedding") or [])} for item in data]
                    else:
                        outputs.update(safe_value(body))
            run.end(outputs=outputs)
        _telemetry(capture)
        return response


def flush_traces():
    """Drain the SDK queue on finite CLI/worker/API shutdown."""
    _telemetry(operations.flush)
    if tracing_is_enabled() is True:
        _telemetry(lambda: (get_tracing_context().get("client") or get_cached_client()).flush())


atexit.register(flush_traces)


class LangSmithMiddleware:
    """Server-created HTTP roots that remain open through the last SSE chunk.

    Never imports an untrusted caller's trace headers, auth, query or body.
    Route templates replace entity IDs in names; IDs remain filterable metadata.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method = scope["method"]
        with span(f"http.{method}", metadata={"transport": "http", "method": method}, tags=["http"]) as run:
            status_code = 500
            async def traced_send(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                    route = getattr(scope.get("route"), "path", "unmatched")
                    operations.organize(route.removeprefix("/api/").split("/")[0])
                    operations.annotate(route=route, status_code=status_code,
                                        **_correlation(scope.get("path_params") or {}))
                    operations.http_status(status_code)
                if message["type"] == "http.response.start" and operations.trace_id():
                    message = {**message, "headers": [*message.get("headers", []),
                               (b"x-trace-id", operations.trace_id().encode())]}
                if run is not None and message["type"] == "http.response.start":
                    route = getattr(scope.get("route"), "path", "unmatched")
                    code = message["status"]
                    def capture():
                        run.name = f"http.{method} {route}"
                        run.add_metadata({"route": route, **_correlation(scope.get("path_params") or {})})
                        if not run.metadata.get("flow"):
                            # Route templates organize endpoints with no deeper AI operation.
                            resource = route.removeprefix("/api/").split("/")[0]
                            run.add_metadata({"flow": resource, "operational": resource in {"health", "queue-health"}})
                            run.add_tags([f"flow:{resource}"])
                        run.outputs = {"status_code": code}
                        if code >= 400:
                            run.error = f"HTTP {code}"
                    _telemetry(capture)
                    message = {**message, "headers": [*message.get("headers", []),
                               (b"x-langsmith-trace-id", str(run.trace_id).encode())]}
                await send(message)
            try:
                await self.app(scope, receive, traced_send)
            finally:
                # Route templates and status only: no auth, query or bodies.
                route = getattr(scope.get("route"), "path", "unmatched")
                logger.info("http request finished", extra={"route": route, "status_code": status_code})
