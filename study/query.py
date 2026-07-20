"""Shared routing for hierarchy operations and ordinary retrieval questions."""

import os
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from retrieval.langchain import BookRetriever
from retrieval.search import RetrievalMode
from retrieval.sqlite import connect_source
from storage.sqlite import connect_readonly
from .content import load_scope_content
from .context import build_scope_context
from .render import format_outline
from .request import (
    StudyRequest,
    UnsupportedStudyRequestError,
    parse_study_request,
    resolve_study_request,
)
from .scope import (
    ResolvedScope,
    ScopeNotFoundError,
)
from .summarize import (
    ContextWindowExceededError,
    SummaryConfig,
    append_references,
    build_summary_messages,
    prompt_budget,
    summarize_scope,
)


class ChatModel(Protocol):
    """Minimal LangChain-compatible chat model used by both query routes."""

    def invoke(self, messages): ...


class QueryExecutionError(RuntimeError):
    """A routed query could not produce a safe reader-facing response."""


def _summary_config() -> SummaryConfig:
    return SummaryConfig(
        context_window_tokens=int(
            os.getenv("SUMMARY_CONTEXT_WINDOW_TOKENS", "64000")
        ),
        max_output_tokens=int(
            os.getenv("SUMMARY_MAX_OUTPUT_TOKENS", "8000")
        ),
        safety_margin_tokens=int(
            os.getenv("SUMMARY_SAFETY_MARGIN_TOKENS", "1000")
        ),
    )


def _openrouter_model(*, max_tokens: int | None = None) -> ChatModel:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise QueryExecutionError("OPENROUTER_API_KEY is missing from .env")

    from langchain_openai import ChatOpenAI

    options = {"max_tokens": max_tokens} if max_tokens is not None else {}
    return ChatOpenAI(
        model=os.getenv("OPENROUTER_MODEL", "openai/gpt-5.6-luna"),
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        **options,
    )


def _resolve_hierarchy_request(
    question: str,
    *,
    source_path: str | Path,
    book_id: int | None,
) -> tuple[StudyRequest, ResolvedScope] | None:
    """Return a resolved study request, or None for ordinary retrieval."""

    try:
        request = parse_study_request(question)
    except UnsupportedStudyRequestError:
        return None

    try:
        with connect_readonly(source_path) as source:
            scope = resolve_study_request(source, request, book_id=book_id)
    except ScopeNotFoundError:
        if request.scope_kind == "named":
            return None
        raise
    return request, scope


def _answer_hierarchy_request(
    request: StudyRequest,
    scope: ResolvedScope,
    *,
    source_path: str | Path,
    model: ChatModel | None,
) -> str:
    """List or summarize one complete canonical hierarchy subtree."""

    if request.intent == "list_sections":
        return format_outline(scope)

    with connect_readonly(source_path) as source:
        evidence = load_scope_content(source, scope)
    context = build_scope_context(evidence)
    config = _summary_config()
    budget = prompt_budget(
        build_summary_messages(scope, context),
        config=config,
    )
    if not budget.fits:
        raise ContextWindowExceededError(budget)

    result = summarize_scope(
        model or _openrouter_model(max_tokens=config.max_output_tokens),
        scope=scope,
        context=context,
        config=config,
    )
    if not result.validation.valid:
        finish_reason = (
            f" (model finish reason: {result.finish_reason})"
            if result.finish_reason
            else ""
        )
        raise QueryExecutionError(
            f"summary validation failed{finish_reason}: "
            + "; ".join(result.validation.errors)
        )

    summary = append_references(result.text, scope=scope)
    route = (
        f"_Scope route: complete {scope.kind} subtree — "
        f"{scope.display_path} (PDF pp. {scope.start_page}–{scope.end_page})_"
    )
    if not result.validation.warnings:
        return f"{route}\n\n{summary}"
    warnings = "\n".join(
        f"> **Validation warning:** {warning}"
        for warning in result.validation.warnings
    )
    return f"{route}\n\n{warnings}\n\n{summary}"


def _answer_retrieval_question(
    question: str,
    *,
    database_path: str,
    source_path: str,
    chroma_path: str,
    book_id: int | None,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
) -> str:
    """Answer one ordinary question from top-k retrieval evidence."""

    with connect_source(source_path) as source:
        if book_id is None:
            rows = source.execute("SELECT id, title FROM books").fetchall()
        else:
            rows = source.execute(
                "SELECT id, title FROM books WHERE id = ?",
                (book_id,),
            ).fetchall()
        books = {row["id"]: row["title"] for row in rows}
    documents = BookRetriever(
        database_path=database_path,
        chroma_path=chroma_path,
        mode=retrieval_mode,
        book_id=book_id,
        k=5,
    ).invoke(question)
    if not documents:
        return "I could not find relevant evidence in the indexed books."

    evidence = "\n\n".join(
        f"[S{i}] {document.metadata['path']} "
        f"(PDF pp. {document.metadata['start_page']}–"
        f"{document.metadata['end_page']})\n{document.page_content}"
        for i, document in enumerate(documents, 1)
    )
    model = model or _openrouter_model()
    rules = (
        "Answer only from the evidence. Cite claims with [S1], [S2], etc. "
        "If evidence is insufficient, say so."
    )
    reply = model.invoke(
        [("system", rules), ("human", f"Question: {question}\n\n{evidence}")]
    )
    sources = ["### Sources"]
    for i, document in enumerate(documents, 1):
        metadata = document.metadata
        hierarchy = metadata["path"].replace(" :: ", " → ")
        pages = str(metadata["start_page"])
        if metadata["end_page"] != metadata["start_page"]:
            pages += f"–{metadata['end_page']}"
        book = books.get(metadata["book_id"], f"Book {metadata['book_id']}")
        sources.append(f"- **[S{i}]** {book} → {hierarchy} — PDF p. {pages}")
    return (
        f"{reply.content}\n\n"
        f"_Retrieval: {retrieval_mode}_\n\n"
        + "\n".join(sources)
    )


def answer_query(
    question: str,
    database_path: str = "data/retrieval.sqlite3",
    source_path: str = "data/books.sqlite3",
    chroma_path: str = "data/chroma",
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    *,
    model: ChatModel | None = None,
) -> str:
    """Route hierarchy operations or answer an ordinary retrieval question."""

    load_dotenv()
    hierarchy = _resolve_hierarchy_request(
        question,
        source_path=source_path,
        book_id=book_id,
    )
    if hierarchy is not None:
        request, scope = hierarchy
        return _answer_hierarchy_request(
            request,
            scope,
            source_path=source_path,
            model=model,
        )
    return _answer_retrieval_question(
        question,
        database_path=database_path,
        source_path=source_path,
        chroma_path=chroma_path,
        book_id=book_id,
        retrieval_mode=retrieval_mode,
        model=model,
    )
