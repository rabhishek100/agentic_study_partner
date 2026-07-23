"""FastAPI boundary over the existing conversational study workflow."""

# Environment must be loaded before project modules evaluate model defaults.
# ruff: noqa: E402

import asyncio
import json
import logging
import os
import queue
import threading
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

load_dotenv()

from retrieval.langchain import warm_models
from storage.database import close_pools, database_readiness
from study.analyze import ConversationDecisionError
from study.contracts import ContractModel, ConversationState, TurnResult
from study.conversation import execute_conversation_turn
from study.query import QueryExecutionError
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("study_partner.api")


RetrievalMode = Literal["bm25", "vector", "hybrid", "hybrid_rerank"]


class ChatRequest(ContractModel):
    question: str = Field(min_length=1, max_length=10_000)
    retrieval_mode: RetrievalMode = "hybrid"
    book_id: int | None = Field(default=1, gt=0)
    state: ConversationState | None = None


class ChatResponse(ContractModel):
    result: TurnResult
    state: ConversationState


class HealthResponse(ContractModel):
    status: Literal["ok", "unavailable"]
    canonical_database_ready: bool
    retrieval_database_ready: bool


def _allowed_origins() -> list[str]:
    configured = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app = FastAPI(
    title="Agentic Study Partner API",
    version="0.1.0",
    description="Grounded book questions and complete-scope summaries.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.on_event("startup")
async def _warm_retrieval_models() -> None:
    logger.info("Initializing the hosted embedding client")
    await run_in_threadpool(warm_models)
    logger.info("Hosted embedding client ready; reranker remains on demand")


@app.on_event("shutdown")
async def _close_database_pools() -> None:
    close_pools()


@app.get("/api/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    canonical_ready, retrieval_ready = await run_in_threadpool(database_readiness)
    if not (canonical_ready and retrieval_ready):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=("ok" if canonical_ready and retrieval_ready else "unavailable"),
        canonical_database_ready=canonical_ready,
        retrieval_database_ready=retrieval_ready,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        result, updated = await run_in_threadpool(
            execute_conversation_turn,
            request.question.strip(),
            request.state,
            retrieval_mode=request.retrieval_mode,
            book_id=request.book_id,
        )
    except (
        ContextWindowExceededError,
        QueryExecutionError,
        ScopeResolutionError,
        ConversationDecisionError,
    ) as error:
        logger.warning("Chat turn rejected: %s", error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        logger.exception("Unhandled error while executing chat turn")
        raise
    return ChatResponse(result=result, state=updated)


_REJECTED_TURN_ERRORS = (
    ContextWindowExceededError,
    QueryExecutionError,
    ScopeResolutionError,
    ConversationDecisionError,
)
_STREAM_DONE = object()
_HEARTBEAT_INTERVAL_SECONDS = 15


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream the answer as it is generated instead of waiting for it whole.

    Ordinary answers emit `token` events as generation text arrives. Hierarchy
    summaries buffer validation/repair attempts and emit only the validated
    answer. Every request ends with one `final` (matching ChatResponse) or
    `error` event.
    """

    loop = asyncio.get_running_loop()
    events: queue.Queue = queue.Queue()

    def on_token(kind: str, text: str) -> None:
        events.put((kind, text))

    def run() -> None:
        try:
            result, updated = execute_conversation_turn(
                request.question.strip(),
                request.state,
                retrieval_mode=request.retrieval_mode,
                book_id=request.book_id,
                token_callback=on_token,
            )
            events.put(
                (
                    "final",
                    ChatResponse(result=result, state=updated).model_dump_json(),
                )
            )
        except _REJECTED_TURN_ERRORS as error:
            logger.warning("Chat turn rejected: %s", error)
            events.put(("error", json.dumps({"detail": str(error)})))
        except Exception:
            logger.exception("Unhandled error while executing chat turn")
            events.put(("error", json.dumps({"detail": "internal error"})))
        finally:
            events.put(_STREAM_DONE)

    threading.Thread(target=run, daemon=True).start()

    async def event_stream():
        while True:
            try:
                item = await loop.run_in_executor(
                    None,
                    lambda: events.get(timeout=_HEARTBEAT_INTERVAL_SECONDS),
                )
            except queue.Empty:
                # Routing/retrieval can run long before the first token; a
                # heartbeat keeps the connection from looking dead to
                # proxies and client-side idle timeouts.
                yield ": heartbeat\n\n"
                continue
            if item is _STREAM_DONE:
                return
            kind, payload = item
            if kind == "token":
                yield _sse("token", {"text": payload})
            elif kind in {"final", "error"}:  # already-serialized JSON
                yield f"event: {kind}\ndata: {payload}\n\n"
            else:
                logger.warning("Ignoring unknown stream event kind: %s", kind)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
