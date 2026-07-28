"""Shared routing for hierarchy operations and ordinary retrieval questions."""

from collections.abc import Sequence
import os
import re
from typing import Protocol
from uuid import UUID

from dotenv import load_dotenv

from retrieval.langchain import BookRetriever
from retrieval.models import book_scope
from retrieval.search import RetrievalMode
from storage.database import connection as database_connection, parse_owner_id
from .content import load_scope_content
from .contracts import (
    CitationRef,
    EvidenceRef,
    ScopeRef,
    TurnResult,
)
from .context import build_scope_context
from .render import format_chapter_list, format_outline
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
from .streaming import TokenCallback, invoke_with_streaming
from .summarize import (
    ContextWindowExceededError,
    SummaryConfig,
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
DEFAULT_GENERATION_MODEL = "deepseek/deepseek-v4-flash"
INSUFFICIENT_EVIDENCE_MARKER = "INSUFFICIENT_EVIDENCE:"
INSUFFICIENT_EVIDENCE_LANGUAGE = re.compile(
    r"\b(?:the\s+)?evidence\s+is\s+insufficient\b|"
    r"\bnot\s+enough\s+evidence\b|"
    r"\bcannot\s+be\s+answered\s+from\s+(?:the|this)\s+evidence\b|"
    r"\bcannot\s+recommend\s+(?:a|an|the|any)\b",
    re.IGNORECASE,
)


def _summary_config() -> SummaryConfig:
    return SummaryConfig(
        context_window_tokens=int(os.getenv("SUMMARY_CONTEXT_WINDOW_TOKENS", "64000")),
        max_output_tokens=int(os.getenv("SUMMARY_MAX_OUTPUT_TOKENS", "8000")),
        safety_margin_tokens=int(os.getenv("SUMMARY_SAFETY_MARGIN_TOKENS", "1000")),
    )


def openrouter_model(*, max_tokens: int | None = None) -> ChatModel:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise QueryExecutionError("OPENROUTER_API_KEY is missing from .env")

    from langchain_openai import ChatOpenAI

    options = {"max_tokens": max_tokens} if max_tokens is not None else {}
    reasoning_effort = os.getenv(
        "OPENROUTER_GENERATION_REASONING",
        "none",
    )
    return ChatOpenAI(
        model=os.getenv("OPENROUTER_GENERATION_MODEL") or DEFAULT_GENERATION_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=2,
        extra_body={
            "reasoning": {
                "effort": reasoning_effort,
                "exclude": True,
            }
        },
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
    database_url: str | None,
    owner_id: str | UUID,
    book_id: int | None,
    book_ids: Sequence[int] | None = None,
) -> tuple[StudyRequest, ResolvedScope] | None:
    """Return a resolved study request, or None for ordinary retrieval."""

    try:
        request = parse_study_request(question)
    except UnsupportedStudyRequestError:
        return None

    try:
        with database_connection(database_url, readonly=True) as source:
            scope = resolve_study_request(
                source,
                request,
                owner_id=owner_id,
                book_id=book_id,
                book_ids=book_ids,
            )
    except ScopeNotFoundError:
        if request.scope_kind == "named":
            return None
        raise
    return request, scope


def _answer_hierarchy_request(
    request: StudyRequest,
    scope: ResolvedScope,
    *,
    database_url: str | None,
    owner_id: str | UUID,
    model: ChatModel | None,
    token_callback: TokenCallback | None = None,
) -> TurnResult:
    """List or summarize one complete canonical hierarchy subtree."""

    if request.intent in {"list_chapters", "list_sections"}:
        is_chapter_list = request.intent == "list_chapters"
        return TurnResult(
            question="",
            answer=(
                format_chapter_list(scope) if is_chapter_list else format_outline(scope)
            ),
            route="hierarchy_list",
            history_dependency="independent",
            standalone_query=(
                f"List {'chapters' if is_chapter_list else 'sections'} in "
                f"{scope.display_path}."
            ),
            resolved_scope=_scope_ref(scope),
            outline_node_ids=(
                [node.id for node in scope.nodes if node.node_type == "chapter"]
                if is_chapter_list
                else [node.id for node in scope.nodes[1:]]
            ),
            outcome="answer",
        )

    with database_connection(database_url, readonly=True) as source:
        evidence_bundle = load_scope_content(source, scope, owner_id=owner_id)
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

    # The answer is the summary and nothing else. The scope, the reference
    # list, and the validation warnings that used to be concatenated here are
    # all already first-class fields on TurnResult (`resolved_scope`,
    # `evidence`/`citations`, `warnings`), and the interface renders them as
    # structure. Baking them into markdown made them unreadable and forced the
    # client to parse prose to recover data the server already had.
    answer = result.text.rstrip()
    warnings = list(result.validation.warnings)
    if result.attempt_count > 1:
        warnings.append(
            "An earlier draft failed deterministic citation validation and "
            "was regenerated with exact validation feedback."
        )
    if token_callback is not None:
        # Summary drafts are buffered until citation validation succeeds. This
        # exposes one stable answer instead of streaming an invalid draft and
        # visibly restarting during a repair attempt.
        token_callback("token", answer)
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
            book_id=scope.book_id,
            book_title=scope.book_title,
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
                book_id=scope.book_id,
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
        warnings=warnings,
    )


def _answer_retrieval_question(
    question: str,
    *,
    database_url: str | None,
    owner_id: str | UUID,
    book_id: int | None,
    book_ids: Sequence[int] | None = None,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
    token_callback: TokenCallback | None = None,
) -> TurnResult:
    """Answer one ordinary question from top-k retrieval evidence."""

    owner = parse_owner_id(owner_id)
    scope = book_scope(book_id, book_ids)
    with database_connection(database_url, readonly=True) as source:
        if scope is None:
            rows = source.execute(
                "select id, title from books where owner_id = %s",
                (owner,),
            ).fetchall()
        else:
            rows = source.execute(
                "select id, title from books where owner_id = %s and id = any(%s)",
                (owner, scope),
            ).fetchall()
        books = {row["id"]: row["title"] for row in rows}
    documents = BookRetriever(
        database_url=database_url or "",
        owner_id=str(owner),
        mode=retrieval_mode,
        book_id=book_id,
        book_ids=book_ids,
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
        "Evidence is sufficient when it directly supports the requested claim "
        "or causal explanation; do not demand extra quantification, exact user "
        "wording, or consumer-specific examples that the question did not require. "
        "A direct logical implication is enough for a yes/no answer; do not abstain "
        "merely because the source does not phrase the conclusion as a normative "
        "sentence. For a 'should every X trigger Y' question, evidence that only "
        "major or meaningful X matters, or that false alerts cause unnecessary Y, "
        "supports answering no. "
        "If the evidence cannot support the requested answer, begin the response "
        f"exactly with {INSUFFICIENT_EVIDENCE_MARKER} and briefly explain what "
        "evidence is missing. Do not answer from general knowledge."
    )
    reply = invoke_with_streaming(
        model,
        [("system", rules), ("human", f"Question: {question}\n\n{evidence}")],
        token_callback=token_callback,
    )
    reply_text = str(reply.content).strip()
    insufficient = INSUFFICIENT_EVIDENCE_MARKER in reply_text or bool(
        INSUFFICIENT_EVIDENCE_LANGUAGE.search(reply_text)
    )
    if re.search(
        r"\binsufficient_evidence\s+is\s+not\s+applicable\b|"
        r"\bthe\s+evidence\s+is\s+sufficient\b",
        reply_text,
        re.IGNORECASE,
    ):
        insufficient = False
    if reply_text.startswith(INSUFFICIENT_EVIDENCE_MARKER):
        explanation = reply_text.removeprefix(INSUFFICIENT_EVIDENCE_MARKER).strip()
        reply_text = "Insufficient evidence"
        if explanation:
            reply_text += f": {explanation}"
    elif insufficient:
        reply_text = reply_text.replace(
            INSUFFICIENT_EVIDENCE_MARKER,
            "Insufficient evidence:",
        )
    # The source list and the retrieval-mode line that used to be appended here
    # are `evidence` and `retrieval_mode` on the result. See the note in
    # `_answer_hierarchy_request`.
    answer = reply_text
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
            book_id=document.metadata.get("book_id"),
            book_title=books.get(document.metadata.get("book_id", 0)),
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
    for match in SOURCE_CITATION.finditer(reply_text):
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
                book_id=document.metadata.get("book_id"),
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
        outcome="abstain" if insufficient else "answer",
        retrieval_mode=retrieval_mode,
    )


def execute_query(
    question: str,
    database_url: str | None = None,
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    *,
    book_ids: Sequence[int] | None = None,
    owner_id: str | UUID,
    model: ChatModel | None = None,
    token_callback: TokenCallback | None = None,
    force_retrieval: bool = False,
) -> TurnResult:
    """Execute a single self-contained hierarchy or retrieval request."""

    load_dotenv()
    hierarchy = None
    if not force_retrieval:
        hierarchy = _resolve_hierarchy_request(
            question,
            database_url=database_url,
            owner_id=owner_id,
            book_id=book_id,
            book_ids=book_ids,
        )
    if hierarchy is not None:
        request, scope = hierarchy
        result = _answer_hierarchy_request(
            request,
            scope,
            database_url=database_url,
            owner_id=owner_id,
            model=model,
            token_callback=token_callback,
        )
        return result.model_copy(update={"question": question})
    return _answer_retrieval_question(
        question,
        database_url=database_url,
        owner_id=owner_id,
        book_id=book_id,
        book_ids=book_ids,
        retrieval_mode=retrieval_mode,
        model=model,
        token_callback=token_callback,
    )


def answer_query(
    question: str,
    database_url: str | None = None,
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    *,
    owner_id: str | UUID,
    model: ChatModel | None = None,
) -> str:
    """Compatibility wrapper returning the existing reader-facing Markdown."""

    return execute_query(
        question,
        database_url=database_url,
        book_id=book_id,
        retrieval_mode=retrieval_mode,
        owner_id=owner_id,
        model=model,
    ).answer
