"""Shared routing for hierarchy operations and ordinary retrieval questions."""

import os
import re
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from dotenv import load_dotenv

from retrieval.langchain import BookRetriever, document_from_result
from retrieval.models import book_scope
from retrieval.postgres import chunks_by_id, search_result_from_row
from retrieval.search import RetrievalMode
from storage.database import connection as database_connection
from storage.database import parse_owner_id

from .content import load_scope_content
from .context import build_scope_context
from .contracts import (
    AnswerArchetype,
    CitationRef,
    ConversationState,
    EvidenceRef,
    PromptProfile,
    ResponseDepth,
    ScopeRef,
    TurnResult,
)
from .figures import load_figure_images, select_figures
from .prompts import (
    DEFAULT_PROMPT_PROFILE,
    build_answer_messages,
    profile_version,
    resolve_answer_archetype,
)
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
    normalize_citation_syntax,
    prompt_budget,
    summarize_scope_with_repair,
)


class ChatModel(Protocol):
    """Minimal LangChain-compatible chat model used by both query routes."""

    def invoke(self, messages): ...


class QueryExecutionError(RuntimeError):
    """A routed query could not produce a safe reader-facing response."""


SOURCE_CITATION = re.compile(r"\[S(\d+)]")
DEFAULT_GENERATION_MODEL = "openai/gpt-5.6-luna"
INSUFFICIENT_EVIDENCE_MARKER = "INSUFFICIENT_EVIDENCE:"
INSUFFICIENT_EVIDENCE_LANGUAGE = re.compile(
    r"\b(?:the\s+)?evidence\s+is\s+insufficient\b|"
    r"\bnot\s+enough\s+evidence\b|"
    r"\bcannot\s+be\s+answered\s+from\s+(?:the|this)\s+evidence\b|"
    r"\bcannot\s+recommend\s+(?:a|an|the|any)\b",
    re.IGNORECASE,
)
RETRIEVAL_LIMIT_BY_DEPTH: dict[ResponseDepth, int] = {
    "quick": 5,
    "interview": 8,
    "deep": 8,
}


def retrieval_limit(response_depth: ResponseDepth) -> int:
    """Return a small evaluated evidence budget for the requested answer depth."""

    return RETRIEVAL_LIMIT_BY_DEPTH[response_depth]


def _summary_config() -> SummaryConfig:
    return SummaryConfig(
        context_window_tokens=int(os.getenv("SUMMARY_CONTEXT_WINDOW_TOKENS", "64000")),
        max_output_tokens=int(os.getenv("SUMMARY_MAX_OUTPUT_TOKENS", "8000")),
        safety_margin_tokens=int(os.getenv("SUMMARY_SAFETY_MARGIN_TOKENS", "1000")),
    )


def control_model() -> ChatModel:
    """The cheap model for judgement calls that are not answers.

    Routing decisions already run on `OPENROUTER_CONTROL_MODEL` rather than
    the generation model, for the obvious reason: a yes/no does not need an
    answer-grade call. External QA makes two decisions of the same kind —
    whether a question needs a live search, and what to type into the search
    box — and this is where they go.
    """

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise QueryExecutionError("OPENROUTER_API_KEY is missing from .env")

    from langchain_openai import ChatOpenAI

    from .analyze import DEFAULT_CONTROL_MODEL

    return ChatOpenAI(
        model=os.getenv("OPENROUTER_CONTROL_MODEL") or DEFAULT_CONTROL_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=1,
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=0,
        extra_body={"reasoning": {"effort": "low", "exclude": True}},
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
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
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
    prompt_profile: PromptProfile,
    response_depth: ResponseDepth,
    routing_reason: str | None,
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
            response_depth=response_depth,
            routing_reason=routing_reason,
            prompt_profile_version=profile_version(prompt_profile),
        )

    with database_connection(database_url, readonly=True) as source:
        evidence_bundle = load_scope_content(source, scope, owner_id=owner_id)
    context = build_scope_context(evidence_bundle)
    config = _summary_config()
    budget = prompt_budget(
        build_summary_messages(
            scope,
            context,
            profile=prompt_profile,
            response_depth=response_depth,
        ),
        config=config,
    )
    if not budget.fits:
        raise ContextWindowExceededError(budget)

    result = summarize_scope_with_repair(
        model or openrouter_model(max_tokens=config.max_output_tokens),
        scope=scope,
        context=context,
        config=config,
        profile=prompt_profile,
        response_depth=response_depth,
    )
    if not result.validation.citation_safe:
        raise QueryExecutionError(
            "I could not produce a summary with fully verified citations. "
            "Please try again; no unverified draft was shown."
        )

    # The answer is the summary and nothing else. The scope, the reference
    # list, and the validation warnings that used to be concatenated here are
    # all already first-class fields on TurnResult (`resolved_scope`,
    # `evidence`/`citations`, `warnings`), and the interface renders them as
    # structure. Baking them into markdown made them unreadable and forced the
    # client to parse prose to recover data the server already had.
    answer = result.text.rstrip()
    nodes = {node.id: node for node in scope.nodes}
    warnings = list(result.validation.warnings)
    if result.validation.required_missing_node_ids:
        missing_paths = [
            nodes[node_id].path_text
            for node_id in sorted(
                result.validation.required_missing_node_ids,
                key=lambda node_id: nodes[node_id].toc_index,
            )
        ]
        warnings.append(
            "Some source material could not be incorporated without weakening "
            "citation guarantees: "
            + "; ".join(missing_paths)
            + ". The answer contains only verified material."
        )
    if result.attempt_count > 1:
        warnings.append(
            "The answer was automatically repaired to improve source coverage "
            "and citation accuracy."
        )
    if token_callback is not None:
        # Summary drafts are buffered until citation safety is established.
        # This exposes one stable answer instead of streaming an invalid draft
        # and visibly restarting during a repair attempt.
        token_callback("token", answer)
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
    with database_connection(database_url, readonly=True) as source:
        figures = select_figures(
            source,
            owner_id=owner_id,
            evidence=evidence,
            citations=citations,
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
        figures=figures,
        outcome="answer",
        warnings=warnings,
        answer_archetype="chapter_review",
        response_depth=response_depth,
        routing_reason=routing_reason,
        prompt_profile_version=profile_version(prompt_profile),
    )


# The retrieval method recorded for a chunk that was not searched for but
# named, by the citation markers inside a passage a reader highlighted.
ANCHOR_RETRIEVAL_METHOD = "anchor_pin"


def _pinned_documents(
    chunk_ids: Sequence[str],
    *,
    database_url: str | None,
    owner_id: str | UUID,
    scope: list[int] | None,
) -> list:
    """Load specific chunks as evidence documents, in the order named.

    Ownership is enforced by the query and book scope is enforced here: a side
    chat freezes its book selection at creation, so a pinned chunk from a book
    that is no longer in scope is dropped rather than quietly widening the
    selection the reader made.
    """

    if not chunk_ids:
        return []
    with database_connection(database_url, readonly=True) as source:
        rows = chunks_by_id(source, list(chunk_ids), owner_id=owner_id)
    documents = []
    for chunk_id in chunk_ids:
        row = rows.get(chunk_id)
        if row is None:
            continue
        if scope is not None and row["source_book_id"] not in scope:
            continue
        documents.append(
            document_from_result(
                search_result_from_row(
                    row,
                    # Anchored chunks are not ranked against the query; they
                    # are named. A sentinel score keeps that visible in the
                    # inspector instead of implying a retrieval score.
                    score=1.0,
                    retrieval_method=ANCHOR_RETRIEVAL_METHOD,
                )
            )
        )
    return documents


def _answer_retrieval_question(
    question: str,
    *,
    database_url: str | None,
    owner_id: str | UUID,
    book_id: int | None,
    book_ids: Sequence[int] | None = None,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
    prompt_profile: PromptProfile,
    response_depth: ResponseDepth,
    routing_reason: str | None,
    answer_archetype: AnswerArchetype | None = None,
    token_callback: TokenCallback | None = None,
    pinned_chunk_ids: Sequence[str] = (),
    request_context: str = "",
    allow_external_fallback: bool = True,
    # The conversation this question was asked in, carried purely so that an
    # escalation to external QA can resolve a follow-up's referents. Retrieval
    # itself is unchanged: the analyser has already rewritten the question into
    # a standalone one before it reaches here.
    conversation: ConversationState | None = None,
    # Off by default. Sending pictures costs tokens on every turn that has one
    # nearby, and the main chat's answers are measured against a frozen gold
    # set; a source-first turn asks about a page the reader is looking at,
    # which is where a diagram is the answer rather than an illustration.
    send_figures: bool = False,
) -> TurnResult:
    """Answer one ordinary question from top-k retrieval evidence.

    `pinned_chunk_ids` are placed ahead of the retrieved documents, so they
    take the lowest `[S…]` markers. That ordering is the mechanism behind a
    side chat's priority context: the chunks a highlighted passage cited are
    the first evidence the model reads, and retrieval for the new question
    fills in around them.
    """

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
    archetype: AnswerArchetype = answer_archetype or resolve_answer_archetype(
        question,
        "retrieval_qa",
    )
    retrieved = BookRetriever(
        database_url=database_url or "",
        owner_id=str(owner),
        mode=retrieval_mode,
        book_id=book_id,
        book_ids=book_ids,
        k=retrieval_limit(response_depth),
        # A system-design scope is often represented by one large hierarchy
        # node containing several chunks. Collapsing to one chunk per node
        # discards most of that design while admitting unrelated chapters.
        unique_nodes=archetype != "system_design",
    ).invoke(question)
    pinned = _pinned_documents(
        pinned_chunk_ids,
        database_url=database_url,
        owner_id=owner,
        scope=scope,
    )
    pinned_ids = {document.metadata["chunk_id"] for document in pinned}
    # A pinned chunk that retrieval also found keeps its pinned position: the
    # same chunk twice would consume two markers for one piece of evidence.
    documents = pinned + [
        document
        for document in retrieved
        if document.metadata.get("chunk_id") not in pinned_ids
    ]
    if not documents:
        if allow_external_fallback:
            from .external_qa import execute_external_qa

            return execute_external_qa(
                question,
                conversation or ConversationState(conversation_id=""),
                model=model,
                token_callback=token_callback,
                response_depth=response_depth,
                history_dependency=(
                    "dependent"
                    if conversation and conversation.previous_answer
                    else "independent"
                ),
                standalone_query=question,
                routing_reason=routing_reason
                or (
                    "No matching evidence in indexed sources; falling back to "
                    "external QA."
                ),
            )
        return TurnResult(
            question=question,
            answer="I could not find relevant evidence in the indexed sources.",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query=question,
            outcome="abstain",
            retrieval_mode=retrieval_mode,
            answer_archetype=resolve_answer_archetype(question, "retrieval_qa"),
            response_depth=response_depth,
            routing_reason=routing_reason,
            prompt_profile_version=profile_version(prompt_profile),
        )

    evidence = "\n\n".join(
        f"[S{i}] {document.metadata['path']} "
        f"(PDF pp. {document.metadata['start_page']}–"
        f"{document.metadata['end_page']})\n{document.page_content}"
        for i, document in enumerate(documents, 1)
    )
    model = model or openrouter_model()
    grounding = (
        "Answer only from the evidence. Cite claims with [S1], [S2], etc. "
        "Evidence is sufficient when it directly supports the requested claim "
        "or causal explanation; do not demand extra quantification, exact user "
        "wording, or consumer-specific examples that the question did not require. "
        "A direct logical implication is enough for a yes/no answer; do not abstain "
        "merely because the source does not phrase the conclusion as a normative "
        "sentence. For a 'should every X trigger Y' question, evidence that only "
        "major or meaningful X matters, or that false alerts cause unnecessary Y, "
        "supports answering no. "
        "Evidence beginning with 'Figure:' is a description of a diagram, chart, "
        "or plot from the selected source rather than its prose. Treat it as "
        "evidence like "
        "any other, and cite it at the point where you discuss what it shows, so "
        "the figure can be placed beside that sentence. Describe it as a figure "
        "rather than quoting the description as if it were the source's wording. "
        "If the evidence cannot support the requested answer, begin the response "
        f"exactly with {INSUFFICIENT_EVIDENCE_MARKER} and briefly explain what "
        "evidence is missing. Do not answer from general knowledge."
    )
    # A book like this answers half its questions in pictures, and until now the
    # model never saw one: figures were selected *after* generation, for the
    # interface to display. Asked to explain a diagram it was looking at, it
    # replied asking to be shown the diagram.
    figure_images: list[tuple[str, str]] = []
    if send_figures:
        # The same nodes and pages the answer will cite, built early. Only the
        # fields `select_figures` reads are filled in: this list exists to
        # choose pictures, not to be reported.
        locating = [
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
                rank=rank,
            )
            for rank, document in enumerate(documents, start=1)
        ]
        with database_connection(database_url, readonly=True) as source:
            pre_figures = select_figures(
                source,
                owner_id=owner,
                evidence=locating,
                citations=(),
            )
            figure_images = load_figure_images(
                source,
                owner_id=owner,
                figures=pre_figures,
            )

    reply = invoke_with_streaming(
        model,
        build_answer_messages(
            profile=prompt_profile,
            question=question,
            evidence=evidence,
            archetype=archetype,
            depth=response_depth,
            request_context=request_context,
            additional_grounding=grounding,
            figures=figure_images,
        ),
        token_callback=token_callback,
    )
    reply_text = normalize_citation_syntax(str(reply.content).strip())
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

    if insufficient:
        if allow_external_fallback:
            from .external_qa import execute_external_qa

            return execute_external_qa(
                question,
                conversation or ConversationState(conversation_id=""),
                model=model,
                token_callback=token_callback,
                response_depth=response_depth,
                history_dependency=(
                    "dependent"
                    if conversation and conversation.previous_answer
                    else "independent"
                ),
                standalone_query=question,
                routing_reason=routing_reason or "Indexed evidence was evaluated as insufficient; falling back to external QA.",
            )
        if reply_text.startswith(INSUFFICIENT_EVIDENCE_MARKER):
            explanation = reply_text.removeprefix(INSUFFICIENT_EVIDENCE_MARKER).strip()
            reply_text = "Insufficient evidence"
            if explanation:
                reply_text += f": {explanation}"
        else:
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
    with database_connection(database_url, readonly=True) as source:
        figures = select_figures(
            source,
            owner_id=owner,
            evidence=evidence_refs,
            citations=citations,
        )
    return TurnResult(
        question=question,
        answer=answer,
        route="retrieval_qa",
        history_dependency="independent",
        standalone_query=question,
        evidence=evidence_refs,
        citations=citations,
        figures=figures,
        outcome="abstain" if insufficient else "answer",
        retrieval_mode=retrieval_mode,
        answer_archetype=archetype,
        response_depth=response_depth,
        routing_reason=routing_reason,
        prompt_profile_version=profile_version(prompt_profile),
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
    prompt_profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
    routing_reason: str | None = None,
    answer_archetype: AnswerArchetype | None = None,
    pinned_chunk_ids: Sequence[str] = (),
    request_context: str = "",
    allow_external_fallback: bool = True,
    # Carried through to external QA so a follow-up that escalates past the
    # library still knows what it is a follow-up to. Only the escalation reads
    # it; the retrieval path is self-contained by design.
    conversation: ConversationState | None = None,
    send_figures: bool = False,
) -> TurnResult:
    """Execute a single self-contained hierarchy or retrieval request."""

    load_dotenv()
    profile = prompt_profile or DEFAULT_PROMPT_PROFILE
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
        # A hierarchy answer loads its whole canonical scope, so pinning
        # individual chunks inside that scope would add nothing, and its
        # summary prompt is built by `build_summary_messages`, which has no
        # request-context layer. A side chat that asks for a whole-chapter
        # summary therefore gets the ordinary summary, quotes and all context
        # excluded — recorded here rather than looking like an oversight.
        request, scope = hierarchy
        result = _answer_hierarchy_request(
            request,
            scope,
            database_url=database_url,
            owner_id=owner_id,
            model=model,
            prompt_profile=profile,
            response_depth=response_depth,
            routing_reason=routing_reason,
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
        prompt_profile=profile,
        response_depth=response_depth,
        routing_reason=routing_reason,
        answer_archetype=answer_archetype,
        token_callback=token_callback,
        pinned_chunk_ids=pinned_chunk_ids,
        request_context=request_context,
        allow_external_fallback=allow_external_fallback,
        conversation=conversation,
        send_figures=send_figures,
    )


def answer_query(
    question: str,
    database_url: str | None = None,
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    *,
    owner_id: str | UUID,
    model: ChatModel | None = None,
    prompt_profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
) -> str:
    """Compatibility wrapper returning the existing reader-facing Markdown."""

    return execute_query(
        question,
        database_url=database_url,
        book_id=book_id,
        retrieval_mode=retrieval_mode,
        owner_id=owner_id,
        model=model,
        prompt_profile=prompt_profile,
        response_depth=response_depth,
    ).answer
