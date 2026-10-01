"""Verify hosted trace delivery/nesting with synthetic data and no paid calls.

Run: uv run python -m scripts.check_langsmith_tracing
"""

import os
import threading
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from langsmith import tracing_context
from langsmith.run_trees import get_cached_client
from langsmith.utils import LangSmithNotFoundError

from observability import flush_traces, in_current_context, provider_post, span


def main():
    load_dotenv()
    if not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        raise SystemExit("Set LANGSMITH_API_KEY to verify hosted delivery.")
    project = (os.getenv("LANGSMITH_PROJECT") or "agentic-study-partner") + "-tracing-smoke"
    operation_id = str(uuid4())
    with tracing_context(enabled=True, project_name=project, metadata={
        "synthetic": True, "thread_id": operation_id, "flow": "observability_smoke",
    }, tags=["synthetic", "observability-smoke"]):
        with span("observability.smoke") as root:
            if root is None:
                raise SystemExit("Could not construct a LangSmith run.")
            with span("python.retrieve", run_type="retriever") as retrieval:
                retrieval.end(outputs={"evidence_ids": ["synthetic-section-1"]})
            def stream():
                transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
                    "choices": [{"message": {"role": "assistant", "content": "Synthetic cited answer"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7, "cost": 0},
                }))
                with span("stream.worker"), httpx.Client(transport=transport) as http:
                    provider_post(http, "https://openrouter.ai/api/v1/chat/completions", json={
                        "model": "synthetic-model", "messages": [{"role": "user", "content": "Synthetic question"}],
                    })
            worker = threading.Thread(target=in_current_context(stream))
            worker.start()
            worker.join()
            with span("python.persist") as persistence:
                persistence.end(outputs={"saved": True})
            root.end(outputs={"synthetic": True, "provider_spend_usd": 0})
        # Explicit context enables flushing even if the process env disables it.
        flush_traces()
        client = get_cached_client()
        expected = {"python.retrieve", "stream.worker", "openrouter.completions", "python.persist"}
        def descendants(run):
            return [item for child in run.child_runs or [] for item in [child, *descendants(child)]]
        children = []
        # A drained client queue does not imply immediate query visibility.
        for delay in (0, 0.5, 1, 2, 4, 8):
            time.sleep(delay)
            try:
                uploaded = client.read_run(root.id, load_child_runs=True)
                children = descendants(uploaded)
            except LangSmithNotFoundError:
                continue
            if {child.name for child in children} == expected:
                break
        if {child.name for child in children} != expected:
            raise SystemExit("Hosted trace is incomplete; rerun after checking delivery.")
        for child in children:
            if child.trace_id != root.trace_id or child.extra.get("metadata", {}).get("thread_id") != operation_id:
                raise SystemExit("Hosted trace correlation is incomplete.")
            if child.end_time is None or child.error:
                raise SystemExit("Hosted child completion is incomplete.")
        provider = next(child for child in children if child.name == "openrouter.completions")
        worker_run = next(child for child in children if child.name == "stream.worker")
        if provider.parent_run_id != worker_run.id or provider.extra["metadata"]["usage_metadata"]["total_cost"] != 0:
            raise SystemExit("Hosted provider nesting/usage is incomplete.")
        print(f"Verified {len(children) + 1} hosted spans; paid provider spend: $0.")
        print(client.get_run_url(run=uploaded))


if __name__ == "__main__":
    main()
