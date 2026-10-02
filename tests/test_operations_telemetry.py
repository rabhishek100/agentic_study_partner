"""Operational correlation, privacy, and real OTLP wire-format smoke tests."""
import asyncio
import json
import logging
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langsmith import tracing_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

import operations_telemetry as ops
from observability import LangSmithMiddleware, in_current_context, record_error, traced, span, provider_post


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.traces = TracerProvider()
        self.traces.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.reader = InMemoryMetricReader()
        self.meters = MeterProvider(metric_readers=[self.reader])
        meter = self.meters.get_meter("test")
        self.runtime = {"tracer": self.traces.get_tracer("test"), "duration": meter.create_histogram("duration"), "count": meter.create_counter("count")}
        self.patch = patch.object(ops, "_RUNTIME", self.runtime)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.traces.shutdown()
        self.meters.shutdown()

    def test_python_thread_nesting_logs_and_handled_errors_without_langsmith(self):
        logs = []
        @traced("worker.job", flow="ingestion")
        def job(job_id):
            def child():
                with ops.operation("worker.stage", {"job_id": job_id, "password": "secret"}):
                    record = logging.LogRecord("study_partner.worker", logging.INFO, __file__, 1, "stage complete", (), None)
                    logs.append(json.loads(ops.JsonFormatter().format(record)))
                    record_error(ValueError("secret provider message"))
            thread = threading.Thread(target=in_current_context(child))
            thread.start(); thread.join()
            return "unchanged"
        with tracing_context(enabled=False):
            self.assertEqual(job("job-123"), "unchanged")
        spans = self.exporter.get_finished_spans()
        child, root = spans
        self.assertEqual(child.parent.span_id, root.context.span_id)
        self.assertEqual(child.context.trace_id, root.context.trace_id)
        self.assertEqual(logs[0]["trace_id"], f"{root.context.trace_id:032x}")
        self.assertEqual(logs[0]["job_id"], "job-123")
        self.assertNotIn("password", child.attributes)
        self.assertEqual(child.status.status_code.name, "ERROR")
        self.assertNotIn("secret", str(child.events))
        points = [point for resource in self.reader.get_metrics_data().resource_metrics for scope in resource.scope_metrics for metric in scope.metrics for point in metric.data.data_points]
        self.assertTrue(points)
        self.assertTrue(all("job_id" not in point.attributes for point in points))

    def test_http_stream_lifetime_and_safe_route_access_log(self):
        app = FastAPI()
        app.add_middleware(LangSmithMiddleware)
        @app.get("/api/books/{book_id}")
        async def stream(book_id):
            async def chunks():
                with ops.operation("stream.chunk", {"book_id": book_id}):
                    yield b"one"
                await asyncio.sleep(.001)
                yield b"two"
            return StreamingResponse(chunks())
        async def request():
            with tracing_context(enabled=False):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    return await client.get("/api/books/private-id?token=secret")
        with self.assertLogs("study_partner.tracing", level="INFO") as logs:
            response = asyncio.run(request())
        child, root = self.exporter.get_finished_spans()
        self.assertEqual(response.content, b"onetwo")
        self.assertEqual(response.headers["x-trace-id"], f"{root.context.trace_id:032x}")
        self.assertGreater(root.end_time, child.end_time)
        self.assertEqual(root.attributes["route"], "/api/books/{book_id}")
        self.assertNotIn("secret", str(root.attributes))
        self.assertEqual(logs.records[0].route, "/api/books/{book_id}")

    def test_langsmith_and_operational_spans_share_correlation_without_payloads(self):
        from tests.test_observability import RecordingClient
        with tracing_context(enabled="local", client=RecordingClient()):
            with span("root", inputs={"prompt": "private source"}, metadata={"flow": "chat"}) as run:
                with span("child"):
                    pass
                expected = str(run.trace_id)
        child, root = self.exporter.get_finished_spans()
        self.assertEqual(root.attributes["langsmith_trace_id"], expected)
        self.assertEqual(child.attributes["langsmith_trace_id"], expected)
        self.assertEqual(child.parent.span_id, root.context.span_id)
        self.assertNotIn("private source", str(root.attributes))

    def test_exporter_setup_and_metric_failures_do_not_change_results(self):
        @traced("operation", flow="chat")
        def work():
            return 42
        with patch.object(self.runtime["tracer"], "start_as_current_span", side_effect=RuntimeError("down")), tracing_context(enabled=False):
            self.assertEqual(work(), 42)
        with patch.object(self.runtime["duration"], "record", side_effect=RuntimeError("down")), tracing_context(enabled=False):
            self.assertEqual(work(), 42)

    def test_enqueued_job_id_reaches_http_parent_without_langsmith(self):
        @traced("jobs.enqueue", flow="ingestion")
        def enqueue():
            return {"job": {"id": "generated-job"}}
        with tracing_context(enabled=False), span("http", metadata={"transport": "http"}):
            enqueue()
        child, root = self.exporter.get_finished_spans()
        self.assertEqual(child.attributes["job_id"], "generated-job")
        self.assertEqual(root.attributes["job_id"], "generated-job")

    def test_raw_provider_http_errors_are_visible_without_langsmith(self):
        with tracing_context(enabled=False), httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(429, json={"error": "private body"}))) as client:
            response = provider_post(client, "https://openrouter.ai/api/v1/chat/completions",
                                     json={"model": "synthetic", "messages": [{"content": "private prompt"}]})
        self.assertEqual(response.status_code, 429)
        run = self.exporter.get_finished_spans()[0]
        self.assertEqual(run.status.status_code.name, "ERROR")
        self.assertEqual(run.attributes["status_code"], 429)
        self.assertNotIn("private", str(run.attributes))

    def test_concurrent_authenticated_streams_and_spoofed_anonymous_identity(self):
        from uuid import uuid4
        from fastapi import Depends
        from api.auth import authenticated_identity, AuthenticatedIdentity, TokenVerificationError
        from tests.test_observability import RecordingClient
        from langgraph.graph import StateGraph, START, END
        from typing_extensions import TypedDict
        users = {"one": uuid4(), "two": uuid4()}
        client = RecordingClient()
        logs = []
        app = FastAPI()
        app.add_middleware(LangSmithMiddleware)
        class State(TypedDict):
            value: str
        graph_builder = StateGraph(State)
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        def retrieve(state):
            FakeListChatModel(responses=["grounded fixture"]).invoke("fixture")
            return state
        graph_builder.add_node("retrieve", retrieve)
        graph_builder.add_edge(START, "retrieve")
        graph_builder.add_edge("retrieve", END)
        graph = graph_builder.compile()
        def verify(token):
            if token not in users:
                raise TokenVerificationError("invalid")
            return AuthenticatedIdentity(owner_id=users[token], email="private@example.com", role="authenticated")
        @app.get("/api/study")
        async def stream(identity=Depends(authenticated_identity)):
            await asyncio.sleep(.002)
            await asyncio.to_thread(graph.invoke, {"value": "source"})
            async def chunks():
                with span("stream.chunk"):
                    await asyncio.sleep(.002)
                    logs.append(json.loads(ops.JsonFormatter().format(logging.makeLogRecord({"msg": "streaming"}))))
                    yield str(identity.owner_id).encode()
            return StreamingResponse(chunks())
        @app.get("/api/health")
        async def health():
            logs.append(json.loads(ops.JsonFormatter().format(logging.makeLogRecord({"msg": "anonymous"}))))
            return {"ok": True}
        async def requests():
            with tracing_context(enabled=True, client=client):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                    responses = await asyncio.gather(*(http.get("/api/study?user_id=spoof", headers={"Authorization": f"Bearer {token}", "X-User-Id": "spoof"}) for token in users))
                    denied = await http.get("/api/study", headers={"Authorization": "Bearer invalid", "X-User-Id": str(users["one"])})
                    anonymous = await http.get("/api/health?user_id=spoof", headers={"X-User-Id": str(users["two"])})
                    return responses, denied, anonymous
        with patch("api.auth.verify_access_token", side_effect=verify):
            responses, denied, anonymous = asyncio.run(requests())
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(anonymous.status_code, 200)
        spans = self.exporter.get_finished_spans()
        roots = [s for s in spans if s.parent is None]
        for response, expected in zip(responses, users.values()):
            root = next(s for s in roots if f"{s.context.trace_id:032x}" == response.headers["x-trace-id"])
            self.assertEqual(root.attributes["user_id"], str(expected))
            children = [s for s in spans if s.context.trace_id == root.context.trace_id]
            self.assertTrue(all(s.attributes["user_id"] == str(expected) for s in children))
            ls_id = response.headers["x-langsmith-trace-id"]
            ls_runs = [r for r in client.created + client.updated if str(r.get("trace_id")) == ls_id]
            self.assertTrue(ls_runs)
            # Initial HTTP post occurs before auth; final update must have identity.
            final_roots = [r for r in client.updated if str(r.get("run_id")) == ls_id]
            self.assertEqual(final_roots[-1]["extra"]["metadata"]["user_id"], str(expected))
            graph_runs = [r for r in ls_runs if r["name"] in ("LangGraph", "retrieve", "FakeListChatModel", "stream.chunk")]
            self.assertTrue(any(r["name"] == "FakeListChatModel" for r in graph_runs))
            self.assertTrue(all(r["extra"]["metadata"].get("user_id") == str(expected) for r in graph_runs))
        self.assertEqual({r["user_id"] for r in logs if "user_id" in r}, {str(v) for v in users.values()})
        self.assertNotIn("user_id", logs[-1])
        self.assertTrue(all("user_id" not in s.attributes for s in roots if s.attributes.get("status_code") == 401 or s.attributes.get("route") == "/api/health"))
        self.assertNotIn("private@example.com", str(spans) + str(client.created) + str(logs))
        self.assertIsNone(ops.user_id())
        points = [p for r in self.reader.get_metrics_data().resource_metrics for s in r.scope_metrics for m in s.metrics for p in m.data.data_points]
        self.assertTrue(points)
        self.assertTrue(all("user_id" not in p.attributes for p in points))

    def test_job_owner_inheritance_threads_failures_and_multiuser_batch(self):
        from types import SimpleNamespace
        from uuid import uuid4
        from tests.test_observability import RecordingClient
        first, second = uuid4(), uuid4()
        logs, client = [], RecordingClient()
        @traced("worker.process", flow="ingestion")
        def process(job, fail=False):
            def stage():
                with span("worker.stage"):
                    logs.append(json.loads(ops.JsonFormatter().format(logging.makeLogRecord({"msg": "stage"}))))
            thread = threading.Thread(target=in_current_context(stage))
            thread.start(); thread.join()
            if fail:
                raise ValueError("fixture")
        with tracing_context(enabled=True, client=client), span("batch"):
            process(SimpleNamespace(id="first", owner_id=first))
            with self.assertRaises(ValueError):
                process({"id": "second", "owner_id": second}, fail=True)
            with span("idle"):
                logs.append(json.loads(ops.JsonFormatter().format(logging.makeLogRecord({"msg": "idle"}))))
        spans = self.exporter.get_finished_spans()
        self.assertEqual([l.get("user_id") for l in logs], [str(first), str(second), None])
        for name in ("batch", "idle"):
            self.assertNotIn("user_id", next(s for s in spans if s.name == name).attributes)
            self.assertNotIn("user_id", next(r for r in client.updated if r["name"] == name)["extra"]["metadata"])
        self.assertIsNone(ops.user_id())

    def test_identity_without_exporters_and_request_body_is_not_trusted(self):
        from uuid import uuid4
        from types import SimpleNamespace
        owner = uuid4()
        @traced("service", flow="chat")
        def service(owner_id=None, request=None):
            return json.loads(ops.JsonFormatter().format(logging.makeLogRecord({"msg": "service"})))
        @traced("voice.command", flow="narration_voice")
        def command(self):
            return ops.user_id()
        with patch.object(ops, "_RUNTIME", None), tracing_context(enabled=False):
            self.assertEqual(service(owner_id=owner)["user_id"], str(owner))
            self.assertNotIn("user_id", service(request={"owner_id": owner, "user_id": owner}))
            with ops.user_scope(owner):
                self.assertEqual(service(owner_id=uuid4())["user_id"], str(owner))
            self.assertEqual(command(SimpleNamespace(binding=SimpleNamespace(owner_id=owner))), str(owner))
            self.assertIsNone(ops.user_id())


    def test_json_validity_and_redaction(self):
        record = logging.LogRecord("worker", 40, __file__, 1, 'failed "quoted" url https://host/path?token=secret Bearer private', (), None)
        record.api_key = "secret"
        payload = json.loads(ops.JsonFormatter().format(record))
        self.assertNotIn("private", payload["message"])
        self.assertNotIn("secret", str(payload))
        self.assertNotIn("api_key", payload)


class OtlpWireTests(unittest.TestCase):
    def test_all_three_signals_export_to_one_endpoint_with_process_metrics(self):
        received = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received[self.path] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200); self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        try:
            with patch.object(ops, "_RUNTIME", None), patch.dict(os.environ, {
                "OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                "OTEL_EXPORTER_OTLP_HEADERS": "", "OTEL_SERVICE_NAME": "wire-smoke", "APP_ENV": "test",
            }), tracing_context(enabled=False):
                ops.configure_logging("wire-smoke")
                with ops.operation("synthetic", {"flow": "test"}):
                    logging.getLogger("study_partner.test").info("synthetic operation complete")
                ops.flush()
                ops.shutdown()
            self.assertEqual(set(received), {"/v1/traces", "/v1/metrics", "/v1/logs"})
            traces = ExportTraceServiceRequest.FromString(received["/v1/traces"])
            logs = ExportLogsServiceRequest.FromString(received["/v1/logs"])
            metrics = ExportMetricsServiceRequest.FromString(received["/v1/metrics"])
            span = traces.resource_spans[0].scope_spans[0].spans[0]
            log = logs.resource_logs[0].scope_logs[0].log_records[0]
            self.assertEqual(span.trace_id, log.trace_id)
            self.assertIn("synthetic operation complete", log.body.string_value)
            names = {m.name for r in metrics.resource_metrics for s in r.scope_metrics for m in s.metrics}
            self.assertTrue({"process.cpu.time", "process.memory.usage", "study.operation.count", "study.operation.duration"} <= names)
        finally:
            root.handlers, root.level = old_handlers, old_level
            server.shutdown(); server.server_close(); thread.join(timeout=2)
