"""Shared routing for hierarchy operations and ordinary retrieval questions."""

import os
from pathlib import Path
import re
from typing import Protocol

from dotenv import load_dotenv

from retrieval.langchain import BookRetriever
from retrieval.search import RetrievalMode
from retrieval.sqlite import connect_source
from storage.sqlite import connect_readonly
from .content import load_scope_content
from .contracts import (
    CitationRef,
    EvidenceRef,
    ScopeRef,
    TurnResult,
)
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
    summarize_scope_with_repair,
)


class ChatModel(Protocol):
    """Minimal LangChain-compatible chat model used by both query routes."""

    def invoke(self, messages): ...


class QueryExecutionError(RuntimeError):
    """A routed query could not produce a safe reader-facing response."""


SOURCE_CITATION = re.compile(r"\[S(\d+)]")


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


def openrouter_model(*, max_tokens: int | None = None) -> ChatModel:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise QueryExecutionError("OPENROUTER_API_KEY is missing from .env")

    from langchain_openai import ChatOpenAI

    options = {"max_tokens": max_tokens} if max_tokens is not None else {}
    return ChatOpenAI(
        model=os.getenv(
            "OPENROUTER_GENERATION_MODEL",
            os.getenv("OPENROUTER_MODEL", "openai/gpt-5.6-luna"),
        ),
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=2,
        **options,
    )


def _scope_ref(scope: ResolvedScope) -> ScopeRef:
    return ScopeRef(
        kind=scope.kind,
        book_id=scope.book_id,
        node_id=scope.root_node_id,
        display_path=scope.display_path,
        start_page=scope.start_page,
        end_page=scope.end_page,
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
) -> TurnResult:
    """List or summarize one complete canonical hierarchy subtree."""

    if request.intent == "list_sections":
        return TurnResult(
            question="",
            answer=format_outline(scope),
            route="hierarchy_list",
            history_dependency="independent",
            standalone_query=(
                f"List sections in {scope.display_path}."
            ),
            resolved_scope=_scope_ref(scope),
            outline_node_ids=[node.id for node in scope.nodes[1:]],
            outcome="answer",
        )

    with connect_readonly(source_path) as source:
        evidence_bundle = load_scope_content(source, scope)
    context = build_scope_context(evidence_bundle)
    config = _summary_config()
    budget = prompt_budget(
        build_summary_messages(scope, context),
        config=config,
    )
    if not budget.fits:
        raise ContextWindowExceededError(budget)

    result = summarize_scope_with_repair(
        model or openrouter_model(max_tokens=config.max_output_tokens),
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
            f"summary validation failed after "
            f"{result.attempt_count} attempt(s){finish_reason}: "
            + "; ".join(result.validation.errors)
        )

    summary = append_references(result.text, scope=scope)
    route = (
        f"_Scope route: complete {scope.kind} subtree — "
        f"{scope.display_path} (PDF pp. {scope.start_page}–{scope.end_page})_"
    )
    warning_text = "\n".join(
        f"> **Validation warning:** {warning}"
        for warning in result.validation.warnings
    )
    answer = (
        f"{route}\n\n{summary}"
        if not warning_text
        else f"{route}\n\n{warning_text}\n\n{summary}"
    )
    if result.attempt_count > 1:
        warning = (
            "The first draft failed deterministic citation validation and "
            "was regenerated once with exact validation feedback."
        )
        warning_text = f"> **Validation repair:** {warning}"
        answer = f"{route}\n\n{warning_text}\n\n{summary}"
    nodes = {node.id: node for node in scope.nodes}
    evidence = [
        EvidenceRef(
            node_id=node_id,
            pages=sorted(
                page
                for candidate_node_id, page in context.allowed_citations
                if candidate_node_id == node_id
            ),
            path=nodes[node_id].path_text,
        )
        for node_id in sorted(
            context.expected_node_ids,
            key=lambda node_id: nodes[node_id].toc_index,
        )
    ]
    citations: list[CitationRef] = []
    seen_citations: set[tuple[int, int]] = set()
    for match in re.finditer(r"\[N(\d+):P(\d+)]", result.text):
        citation = (int(match.group(1)), int(match.group(2)))
        if citation in seen_citations:
            continue
        seen_citations.add(citation)
        citations.append(
            CitationRef(
                marker=match.group(0),
                node_id=citation[0],
                page=citation[1],
            )
        )
    return TurnResult(
        question="",
        answer=answer,
        route="hierarchy_summary",
        history_dependency="independent",
        standalone_query=f"Summarize {scope.display_path}.",
        resolved_scope=_scope_ref(scope),
        evidence=evidence,
        citations=citations,
        outcome="answer",
        warnings=list(result.validation.warnings),
    )


def _answer_retrieval_question(
    question: str,
    *,
    database_path: str,
    source_path: str,
    chroma_path: str,
    book_id: int | None,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
) -> TurnResult:
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
        return TurnResult(
            question=question,
            answer="I could not find relevant evidence in the indexed books.",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query=question,
            outcome="abstain",
            retrieval_mode=retrieval_mode,
        )

    evidence = "\n\n".join(
        f"[S{i}] {document.metadata['path']} "
        f"(PDF pp. {document.metadata['start_page']}–"
        f"{document.metadata['end_page']})\n{document.page_content}"
        for i, document in enumerate(documents, 1)
    )
    model = model or openrouter_model()
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
    answer = (
        f"{reply.content}\n\n"
        f"_Retrieval: {retrieval_mode}_\n\n"
        + "\n".join(sources)
    )
    evidence_refs = [
        EvidenceRef(
            node_id=document.metadata.get("node_id", 0),
            pages=list(
                range(
                    document.metadata["start_page"],
                    document.metadata["end_page"] + 1,
                )
            ),
            path=document.metadata["path"],
            rank=rank,
            chunk_id=document.metadata.get("chunk_id"),
            chunk_index=document.metadata.get("chunk_index"),
            retrieval_method=document.metadata.get("retrieval_method"),
            score=document.metadata.get("score"),
            excerpt=" ".join(document.page_content.split())[:400],
        )
        for rank, document in enumerate(documents, start=1)
    ]
    citations: list[CitationRef] = []
    seen_markers: set[str] = set()
    for match in SOURCE_CITATION.finditer(str(reply.content)):
        marker = match.group(0)
        rank = int(match.group(1))
        if marker in seen_markers or rank < 1 or rank > len(documents):
            continue
        seen_markers.add(marker)
        document = documents[rank - 1]
        citations.append(
            CitationRef(
                marker=marker,
                node_id=document.metadata.get("node_id", 0),
                page=document.metadata["start_page"],
                evidence_rank=rank,
            )
        )
    return TurnResult(
        question=question,
        answer=answer,
        route="retrieval_qa",
        history_dependency="independent",
        standalone_query=question,
        evidence=evidence_refs,
        citations=citations,
        outcome="answer",
        retrieval_mode=retrieval_mode,
    )


def execute_query(
    question: str,
    database_path: str = "data/retrieval.sqlite3",
    source_path: str = "data/books.sqlite3",
    chroma_path: str = "data/chroma",
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    *,
    model: ChatModel | None = None,
) -> TurnResult:
    """Execute a single self-contained hierarchy or retrieval request."""

    load_dotenv()
    hierarchy = _resolve_hierarchy_request(
        question,
        source_path=source_path,
        book_id=book_id,
    )
    if hierarchy is not None:
        request, scope = hierarchy
        result = _answer_hierarchy_request(
            request,
            scope,
            source_path=source_path,
            model=model,
        )
        return result.model_copy(update={"question": question})
    return _answer_retrieval_question(
        question,
        database_path=database_path,
        source_path=source_path,
        chroma_path=chroma_path,
        book_id=book_id,
        retrieval_mode=retrieval_mode,
        model=model,
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
    """Compatibility wrapper returning the existing reader-facing Markdown."""

    return execute_query(
        question,
        database_path=database_path,
        source_path=source_path,
        chroma_path=chroma_path,
        book_id=book_id,
        retrieval_mode=retrieval_mode,
        model=model,
    ).answer
