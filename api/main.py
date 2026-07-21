"""FastAPI boundary over the existing conversational study workflow."""

import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from retrieval.langchain import warm_models
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
    status: Literal["ok"] = "ok"
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
    allow_headers=["Content-Type"],
)


@app.on_event("startup")
async def _warm_retrieval_models() -> None:
    logger.info("Warming embedder/reranker models before serving requests")
    await run_in_threadpool(warm_models)
    logger.info("Embedder/reranker models ready")


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        canonical_database_ready=Path("data/books.sqlite3").is_file(),
        retrieval_database_ready=Path("data/retrieval.sqlite3").is_file(),
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
