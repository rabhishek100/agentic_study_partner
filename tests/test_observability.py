"""No-network tests of actual SDK nesting, streams, failures and receipts."""

import asyncio
import ast
import json
import os
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from langsmith.run_helpers import get_current_run_tree
from typing_extensions import TypedDict

from observability import (
    LangSmithMiddleware, in_current_context, provider_post, record_error,
    record_estimate, record_voice_metrics, safe_value, span, traced,
)


class RecordingClient:
    def __init__(self):
        self.created = []
        self.updated = []

    def create_run(self, **kwargs):
        self.created.append(deepcopy(kwargs))

    def update_run(self, **kwargs):
        self.updated.append(deepcopy(kwargs))

    def flush(self):
        pass


class ObservabilityTests(unittest.TestCase):
    def test_operational_worker_project_preserves_children_and_evaluation_context(self):
        client = RecordingClient()
        @traced("reminder.check", flow="reminders", operational=True)
        def check():
            with span("reminder.owner"):
                return 0
        with patch.dict(os.environ, {"LANGSMITH_OPERATIONS_PROJECT": ""}), \
                tracing_context(enabled=True, client=client, project_name="app-production"):
            check()
            with span("normal.request") as main:
                check()  # A nested operation must never detach from its parent.
        created = client.created
        self.assertEqual(created[0]["session_name"], "app-production-operations")
        self.assertEqual(created[1]["session_name"], "app-production-operations")
        self.assertEqual(created[1]["parent_run_id"], created[0]["id"])
        self.assertEqual(created[2]["session_name"], "app-production")
        self.assertEqual(created[3]["parent_run_id"], main.id)
        self.assertEqual(created[3]["session_name"], "app-production")
        with tracing_context(enabled=True, client=client, project_name="evaluation-project",
                             metadata={"experiment_id": "baseline"}, tags=["evaluation"]):
            check()
        self.assertEqual(client.created[-2]["session_name"], "evaluation-project")
        with patch.dict(os.environ, {"LANGSMITH_PROJECT": "env-project",
                                     "LANGSMITH_OPERATIONS_PROJECT": "custom-operations"}), \
                tracing_context(enabled=True, client=client):
            check()
        self.assertEqual(client.created[-2]["session_name"], "custom-operations")
        with patch.dict(os.environ, {"LANGSMITH_PROJECT": "env-project",
                                     "LANGSMITH_OPERATIONS_PROJECT": ""}), \
                tracing_context(enabled=True, client=client):
            check()
        self.assertEqual(client.created[-2]["session_name"], "env-project-operations")

    def test_graph_and_provider_nest_under_python_operation(self):
        client = RecordingClient()
        class State(TypedDict):
            answer: str
        def retrieve(state):
            with span("retrieve", run_type="retriever"):
                return {"answer": "evidence"}
        def answer(state):
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": state["answer"]}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23, "cost": 0.001},
            }))
            with httpx.Client(transport=transport) as http:
                provider_post(http, "https://openrouter.ai/api/v1/chat/completions",
                              json={"model": "test-model", "messages": [{"role": "user", "content": "question"}]})
            return state
        builder = StateGraph(State)
        builder.add_node("retrieve", retrieve)
        builder.add_node("answer", answer)
        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "answer")
        builder.add_edge("answer", END)
        graph = builder.compile()
        @traced("test.operation", flow="chat")
        def operation(conversation_id):
            result = graph.invoke({"answer": ""}, config={"run_name": "test.graph"})
            with span("persist"):
                return result
        with tracing_context(enabled=True, client=client, project_name="eval-project",
                             metadata={"experiment_id": "baseline"}):
            self.assertEqual(operation("conversation-1")["answer"], "evidence")
        created = {run["name"]: run for run in client.created}
        root = created["test.operation"]
        graph_run = created["test.graph"]
        self.assertEqual(graph_run["parent_run_id"], root["id"])
        provider = created["openrouter.completions"]
        self.assertEqual(provider["parent_run_id"], created["answer"]["id"])
        self.assertEqual(provider["extra"]["metadata"]["thread_id"], "conversation-1")
        self.assertEqual(provider["extra"]["metadata"]["experiment_id"], "baseline")
        self.assertEqual(provider["session_name"], "eval-project")
        receipt = next(run for run in client.updated if run["name"] == "openrouter.completions")
        self.assertEqual(receipt["extra"]["metadata"]["usage_metadata"]["total_cost"], 0.001)
        self.assertEqual(created["persist"]["parent_run_id"], root["id"])

    def test_manual_thread_keeps_parent_and_metadata(self):
        with tracing_context(enabled="local", client=RecordingClient(), metadata={"thread_id": "conversation-1"}):
            with span("root") as root:
                seen = []
                def execute():
                    with span("stream.worker") as child:
                        seen.append(child)
                thread = threading.Thread(target=in_current_context(execute))
                thread.start()
                thread.join()
                self.assertEqual(seen[0].parent_run_id, root.id)
                self.assertEqual(seen[0].metadata["thread_id"], "conversation-1")
                self.assertIs(get_current_run_tree(), root)
        self.assertIsNone(get_current_run_tree())

    def test_disabled_tracing_does_not_capture_or_construct_client(self):
        @traced("operation", flow="ingestion")
        def operation(job_id):
            return job_id
        with tracing_context(enabled=False), patch("observability.safe_value", side_effect=AssertionError), \
                patch("observability.get_cached_client", side_effect=AssertionError):
            self.assertEqual(operation("j1"), "j1")
            with span("disabled") as run:
                self.assertIsNone(run)

    def test_delivery_failure_does_not_change_result_or_exception(self):
        client = RecordingClient()
        client.create_run = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("credentials"))
        client.update_run = client.create_run
        @traced("operation", flow="cards")
        def operation(fail=False):
            if fail:
                raise ValueError("original failure")
            return 42
        with tracing_context(enabled=True, client=client), self.assertLogs("study_partner.tracing"):
            self.assertEqual(operation(), 42)
            with self.assertRaisesRegex(ValueError, "original failure"):
                operation(True)
            self.assertIsNone(get_current_run_tree())

    def test_handled_worker_error_stays_failed(self):
        client = RecordingClient()
        @traced("worker.process", flow="video_ingestion")
        def process(job):
            try:
                raise ValueError("a signed URL could appear here")
            except ValueError as error:
                record_error(error)
            return None
        with tracing_context(enabled=True, client=client):
            process({"id": "job-1", "attempt_count": 2, "stage": "transcript"})
        run = client.updated[-1]
        self.assertEqual(run["error"], "ValueError")
        self.assertEqual(run["extra"]["metadata"]["job_id"], "job-1")
        self.assertEqual(run["extra"]["metadata"]["attempt_count"], 2)
        self.assertEqual(run["extra"]["metadata"]["outcome"], "failed")

    def test_retry_attempts_and_embeddings_do_not_duplicate_cost(self):
        client = RecordingClient()
        attempts = iter([httpx.Response(429, json={"error": "retry"}), httpx.Response(200, json={
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 4, "total_tokens": 4, "cost": 0.00001},
        })])
        with tracing_context(enabled=True, client=client), httpx.Client(
            transport=httpx.MockTransport(lambda request: next(attempts))
        ) as http, span("embedding.operation"):
            first = provider_post(http, "https://openrouter.ai/api/v1/embeddings", json={"model": "embed", "input": ["text"]})
            second = provider_post(http, "https://openrouter.ai/api/v1/embeddings", json={"model": "embed", "input": ["text"]})
        self.assertEqual(first.status_code, 429)
        self.assertEqual(second.json()["data"][0]["embedding"], [0.1, 0.2])
        providers = [run for run in client.updated if run["name"] == "openrouter.embeddings"]
        self.assertEqual(len(providers), 2)
        self.assertEqual(providers[0]["error"], "HTTP 429")
        self.assertNotIn("usage_metadata", providers[0]["extra"]["metadata"])
        self.assertEqual(providers[1]["outputs"]["vectors"], [{"index": 0, "dimensions": 2}])
        root = client.updated[-1]
        self.assertNotIn("usage_metadata", root["extra"]["metadata"])

    def test_capture_omits_secrets_signed_urls_inline_media_and_objects(self):
        captured = safe_value({"api_key": "secret", "Authorization": "Bearer secret", "audio": b"private media",
                               "url": "https://user:pass@example.com/file?signature=secret",
                               "message": "Look: data:image/png;base64,c2VjcmV0", "connection": object()})
        rendered = json.dumps(captured)
        for secret in ("Bearer", "signature", "user:pass", "c2VjcmV0", "private media", "0x"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(captured["url"], "https://example.com/file")
        self.assertEqual(captured["api_key"], "[redacted]")

    def test_capture_failure_cannot_break_workflow(self):
        @traced("workflow", flow="chat")
        def operation(conversation_id):
            return "answer"
        with tracing_context(enabled=True, client=RecordingClient()), \
                patch("observability.safe_value", side_effect=ValueError("bad capture")), \
                self.assertLogs("study_partner.tracing"):
            self.assertEqual(operation("conversation-1"), "answer")

    def test_enqueue_and_worker_roots_share_job_identity(self):
        @traced("jobs.enqueue", flow="ingestion")
        def enqueue():
            return SimpleNamespace(id="job-1", attempt_count=0), True
        @traced("worker.process", flow="ingestion")
        def process(job):
            pass
        client = RecordingClient()
        with tracing_context(enabled=True, client=client):
            with span("http.enqueue"):
                job, created = enqueue()
            process(job)
        roots = [run for run in client.updated if not run["parent_run_id"]]
        self.assertEqual(len(roots), 2)
        self.assertEqual({run["extra"]["metadata"]["job_id"] for run in roots}, {"job-1"})
        self.assertEqual({run["extra"]["metadata"]["thread_id"] for run in roots}, {"job-1"})

    def test_task_cancellation_has_distinct_outcome_and_restores_context(self):
        client = RecordingClient()
        with tracing_context(enabled=True, client=client):
            with self.assertRaises(asyncio.CancelledError):
                with span("voice.capture"):
                    raise asyncio.CancelledError()
            self.assertIsNone(get_current_run_tree())
        self.assertIsNone(client.updated[-1]["error"])
        self.assertEqual(client.updated[-1]["extra"]["metadata"]["outcome"], "cancelled")

    def test_voice_metrics_are_tool_events_with_separate_estimated_cost(self):
        client = RecordingClient()
        @traced("voice.metrics", flow="interview_voice", run_type="tool")
        def metric():
            record_voice_metrics({"type": "tts_metrics", "characters_count": 1000,
                                  "request_id": "tts-1", "metadata": {"model_name": "voice-model"}})
            record_estimate(0.05)
        with tracing_context(enabled=True, client=client):
            metric()
        run = client.updated[-1]
        metadata = run["extra"]["metadata"]
        self.assertEqual(run["run_type"], "tool")
        self.assertEqual(metadata["provider_request_id"], "tts-1")
        self.assertEqual(metadata["ls_model_name"], "voice-model")
        self.assertEqual(metadata["cost_source"], "estimate")
        self.assertEqual(metadata["estimated_cost_usd"], 0.05)
        self.assertEqual(run["outputs"]["metrics"]["characters_count"], 1000)
        self.assertNotIn("usage_metadata", metadata)


class HttpObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stream_helpers_preserve_context_and_mark_sse_errors(self):
        from api.main import _streamed_turn
        from api.video_chat import _streamed_video_turn
        from api.course_chat import _stream
        for helper in (_streamed_turn, _streamed_video_turn, _stream):
            for fail in (False, True):
                with self.subTest(helper=helper.__name__, fail=fail):
                    client = RecordingClient()
                    with tracing_context(enabled=True, client=client), span("http.stream") as root:
                        def execute(on_token):
                            with span("execute"):
                                on_token("token", "answer")
                                if fail:
                                    from fastapi import HTTPException
                                    raise HTTPException(422, "invalid source")
                                return SimpleNamespace(model_dump_json=lambda: '{"answer":"complete"}')
                        response = helper(execute)
                        body = "".join([chunk async for chunk in response.body_iterator])
                    child = next(run for run in client.created if run["name"] == "execute")
                    self.assertEqual(child["parent_run_id"], root.id)
                    self.assertIn("event: error" if fail else "event: final", body)
                    self.assertEqual(client.updated[-1]["error"], "HTTPException" if fail else None)

    async def test_stream_root_lives_until_completion_with_threaded_child(self):
        app = FastAPI()
        app.add_middleware(LangSmithMiddleware)
        client = RecordingClient()
        @app.get("/api/conversations/{conversation_id}/stream")
        async def stream(conversation_id: str):
            loop = asyncio.get_running_loop()
            done = asyncio.Event()
            def execute():
                with span("stream.worker"):
                    loop.call_soon_threadsafe(done.set)
            threading.Thread(target=in_current_context(execute)).start()
            async def content():
                await done.wait()
                self.assertFalse(any(run["name"].startswith("http.") for run in client.updated))
                yield "data: complete\n\n"
            return StreamingResponse(content(), media_type="text/event-stream")
        with tracing_context(enabled=True, client=client):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                response = await http.get("/api/conversations/c1/stream?token=secret", headers={
                    "Authorization": "Bearer secret", "langsmith-trace": "untrusted-parent"})
        root = next(run for run in client.updated if run["name"].startswith("http."))
        child = next(run for run in client.created if run["name"] == "stream.worker")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-langsmith-trace-id"], str(root["trace_id"]))
        self.assertEqual(root["name"], "http.GET /api/conversations/{conversation_id}/stream")
        self.assertEqual(root["extra"]["metadata"]["conversation_id"], "c1")
        self.assertEqual(child["parent_run_id"], root["run_id"])
        self.assertIsNone(root["parent_run_id"])
        self.assertNotIn("secret", str(client.created) + str(client.updated))

    async def test_http_error_is_failed_trace(self):
        app = FastAPI()
        app.add_middleware(LangSmithMiddleware)
        @app.get("/missing")
        async def missing():
            from fastapi import HTTPException
            raise HTTPException(404, "missing")
        client = RecordingClient()
        with tracing_context(enabled=True, client=client):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                self.assertEqual((await http.get("/missing")).status_code, 404)
        self.assertEqual(client.updated[-1]["error"], "HTTP 404")

    async def test_notification_polls_use_operations_project_without_losing_errors(self):
        app = FastAPI()
        app.add_middleware(LangSmithMiddleware)
        @app.get("/api/notifications")
        async def notifications():
            with span("notifications.load"):
                from fastapi import HTTPException
                raise HTTPException(401, "not authenticated")
        @app.post("/api/chat/stream")
        async def chat():
            with span("chat.execute"):
                return {"answer": "test"}
        @app.post("/api/notifications/read-all")
        async def mark_read():
            return {}
        client = RecordingClient()
        with patch.dict(os.environ, {"LANGSMITH_OPERATIONS_PROJECT": ""}), \
                tracing_context(enabled=True, client=client, project_name="app-production"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                poll = await http.get("/api/notifications")
                self.assertEqual((await http.post("/api/chat/stream")).status_code, 200)
                self.assertEqual((await http.post("/api/notifications/read-all")).status_code, 200)
        poll_root = next(r for r in client.updated if r["name"] == "http.GET /api/notifications")
        self.assertEqual(poll.status_code, 401)
        self.assertEqual(poll_root["error"], "HTTP 401")
        self.assertEqual(poll_root["session_name"], "app-production-operations")
        self.assertTrue(poll_root["extra"]["metadata"]["operational"])
        self.assertEqual(poll.headers["x-langsmith-trace-id"], str(poll_root["trace_id"]))
        child = next(r for r in client.created if r["name"] == "notifications.load")
        self.assertEqual(child["session_name"], "app-production-operations")
        self.assertEqual(child["parent_run_id"], poll_root["run_id"])
        for r in client.updated:
            if r["name"] in {"http.POST /api/chat/stream", "http.POST /api/notifications/read-all"}:
                self.assertEqual(r["session_name"], "app-production")


class ProviderCoverageTests(unittest.TestCase):
    def test_raw_paid_provider_clients_all_use_shared_boundary(self):
        # Keep newly introduced raw inference paths from silently losing spans.
        roots = ["ingestion", "retrieval", "video", "study", "narration", "interviews", "decks", "revision_sheets", "experiments"]
        missed = []
        for directory in roots:
            for path in Path(directory).rglob("*.py"):
                source = path.read_text()
                if "openrouter.ai" not in source:
                    continue
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "post":
                        missed.append(f"{path}:{node.lineno}")
        self.assertEqual(missed, [])
