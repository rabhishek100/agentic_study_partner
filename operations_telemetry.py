"""Operational logs, traces and process metrics; opt-in OTLP/HTTP export.

LangSmith remains the source for model inputs, tokens and cost. This stream
contains operation names and correlation IDs, never request/response bodies.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
import psutil

_FIELDS = frozenset(("flow", "operation", "method", "provider", "provider_attempt", "ls_model_name", "page_number", "purpose", "cache_hit", "transport", "job_id", "book_id", "video_id",
    "course_id", "session_id", "conversation_id", "sheet_id", "stage", "attempt_count",
    "attempt", "status", "status_code", "error_code", "error_detail", "pages", "chunks",
    "elapsed_seconds", "langsmith_trace_id", "route", "outcome"))
_STACK = ContextVar("operational_stack", default=())
_CURRENT = ContextVar("operational_span", default=None)
_RUNTIME = None
_LOCK = threading.Lock()


def _clean(value):
    value = str(value)
    value = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", value)
    value = re.sub(r"(?i)((?:password|token|api_key|secret|authorization)\s*[=:]\s*)\S+", r"\1[redacted]", value)
    def url(match):
        try:
            parts = urlsplit(match.group())
            return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
        except ValueError:
            return "[url omitted]"
    return re.sub(r"https?://[^\s\"<>]+", url, value)[:1000]


def attributes(values):
    return {k: _clean(v) if not isinstance(v, (bool, int, float)) else v
            for k, v in values.items() if k in _FIELDS and v is not None}


class JsonFormatter(logging.Formatter):
    """One valid JSON record per line, with operational and AI correlation."""
    def format(self, record):
        payload = {"timestamp": self.formatTime(record), "level": record.levelname,
                   "logger": record.name, "message": _clean(record.getMessage())}
        payload.update(attributes(vars(record)))
        current = _CURRENT.get()
        if current is not None:
            payload.update(attributes(dict(current.attributes or {})))
            ctx = current.get_span_context()
            if ctx.is_valid:
                payload.update(trace_id=f"{ctx.trace_id:032x}", span_id=f"{ctx.span_id:016x}")
        from langsmith.run_helpers import get_current_run_tree
        run = get_current_run_tree()
        if run is not None:
            payload["langsmith_trace_id"] = str(run.trace_id)
            payload.update(attributes(run.metadata))
        # Include stack locations and type, omitting exception text/locals.
        if record.exc_info:
            kind, _, tb = record.exc_info
            payload["exception_type"] = kind.__name__
            payload["exception_frames"] = [f"{f.filename}:{f.lineno} {f.name}" for f in traceback.extract_tb(tb)]
        return json.dumps(payload, ensure_ascii=False)


class _ExportHandler(LoggingHandler):
    def emit(self, record):
        # SDK LoggingHandler otherwise forwards *every* extra field. Clone a
        # sanitized record so arbitrary extras can't bypass the allowlist.
        if record.name.startswith(("opentelemetry", "urllib3", "requests", "httpx", "httpcore")):
            return  # exporter errors must not recursively export themselves
        clean = logging.LogRecord(record.name, record.levelno, record.pathname,
                                  record.lineno, JsonFormatter().format(record), (), None)
        try:
            super().emit(clean)
        except Exception:
            pass


def configure_logging(service: str, *, log_file: str = ""):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    # Uvicorn's own access log includes raw paths/query strings. Our HTTP
    # middleware emits a route-template access record instead.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    configure(service)
    if _RUNTIME:
        root.addHandler(_ExportHandler(logger_provider=_RUNTIME["logs"]))


def configure(service: str):
    """No exporters/threads unless explicitly enabled and given an endpoint."""
    global _RUNTIME
    if _RUNTIME is not None or os.getenv("OTEL_ENABLED", "false").lower() != "true":
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logging.getLogger(__name__).warning("OTLP export enabled without an endpoint; using local logs")
        return
    with _LOCK:
        if _RUNTIME is not None:
            return
        providers = []
        try:
            resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME") or service,
                "service.instance.id": uuid4().hex,
                "deployment.environment.name": os.getenv("APP_ENV", "local")})
            traces = TracerProvider(resource=resource)
            providers.append(traces)
            traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=5)))
            reader = PeriodicExportingMetricReader(OTLPMetricExporter(timeout=5), export_interval_millis=60000)
            meters = MeterProvider(resource=resource, metric_readers=[reader], views=[View(
                instrument_name="study.operation.duration", aggregation=ExplicitBucketHistogramAggregation([.1, 1, 5, 30, 120]))])
            providers.append(meters)
            meter = meters.get_meter("study_partner.operations")
            process = psutil.Process()
            def cpu(_):
                try:
                    times = process.cpu_times()
                    return [Observation(times.user + times.system)]
                except psutil.Error:
                    return []
            def memory(_):
                try:
                    return [Observation(process.memory_info().rss)]
                except psutil.Error:
                    return []
            meter.create_observable_counter("process.cpu.time", callbacks=[cpu], unit="s")
            meter.create_observable_gauge("process.memory.usage", callbacks=[memory], unit="By")
            logs = LoggerProvider(resource=resource)
            providers.append(logs)
            logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(timeout=5)))
            _RUNTIME = {"tracer": traces.get_tracer("study_partner.operations"),
                        "providers": providers, "logs": logs,
                        "duration": meter.create_histogram("study.operation.duration", unit="s"),
                        "count": meter.create_counter("study.operation.count")}
        except Exception as error:
            for provider in providers:
                try:
                    provider.shutdown()
                except Exception:
                    pass
            logging.getLogger(__name__).warning("Operational telemetry unavailable: %s", type(error).__name__)


def enabled():
    return _RUNTIME is not None


def annotate(**values):
    current = _CURRENT.get()
    if current is not None:
        try:
            captured = attributes(values)
            current.set_attributes(captured)
            ids = {k: v for k, v in captured.items() if k.endswith("_id")}
            if ids:
                for parent in _STACK.get():
                    if parent is not current:
                        parent.set_attributes(ids)
        except Exception:
            pass


def http_status(code):
    annotate(status_code=code)
    current = _CURRENT.get()
    if current is not None and code >= 400:
        annotate(outcome="failed")
        try:
            current.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {code}"))
        except Exception:
            pass


def organize(flow):
    for current in _STACK.get():
        if not current.attributes.get("flow"):
            try:
                current.set_attribute("flow", flow)
            except Exception:
                pass


def error(error, *, outcome="failed"):
    current = _CURRENT.get()
    if current is not None:
        annotate(outcome=outcome, error_code=type(error).__name__)
        if outcome != "cancelled":
            try:
                current.set_status(trace.Status(trace.StatusCode.ERROR, type(error).__name__))
            except Exception:
                pass


@contextmanager
def operation(name, metadata=None):
    if not enabled():
        yield
        return
    runtime = _RUNTIME
    parent = _CURRENT.get()
    started = perf_counter()
    # Avoid the SDK's automatic exception message/stack payload capture.
    try:
        boundary = runtime["tracer"].start_as_current_span(name, attributes=attributes(metadata or {}),
            record_exception=False, set_status_on_exception=False)
        current = boundary.__enter__()
    except Exception:
        yield
        return
    try:
        token = _CURRENT.set(current)
        stack_token = _STACK.set((*_STACK.get(), current))
        try:
            yield
        except BaseException as exc:
            error(exc, outcome="cancelled" if type(exc).__name__ in {
                "CancelledError", "CancellationRequested", "DeckCancellationRequested"} else "failed")
            raise
        finally:
            if parent is None:
                values = dict(current.attributes or {})
                labels = {"flow": values.get("flow", "http" if values.get("transport") == "http" else "other"),
                          "outcome": values.get("outcome", "success")}
                # No IDs, URL paths, routes, or operation names in metric labels.
                try:
                    runtime["duration"].record(perf_counter() - started, labels)
                    runtime["count"].add(1, labels)
                except Exception:
                    pass
            _STACK.reset(stack_token)
            _CURRENT.reset(token)
    finally:
        try:
            boundary.__exit__(None, None, None)
        except Exception:
            pass


def trace_id():
    current = _CURRENT.get()
    return f"{current.get_span_context().trace_id:032x}" if current is not None else None


def flush():
    if _RUNTIME:
        for provider in _RUNTIME["providers"]:
            provider.force_flush(timeout_millis=5000)


def shutdown():
    global _RUNTIME
    runtime, _RUNTIME = _RUNTIME, None
    if runtime:
        for provider in runtime["providers"]:
            try:
                provider.shutdown()
            except Exception:
                pass


atexit.register(shutdown)
