"""Execute one conversational turn over summaries and book retrieval."""

import re
from collections.abc import Sequence
from uuid import UUID, uuid4

from dotenv import load_dotenv

from retrieval.search import RetrievalMode
from storage.database import connection as database_connection
from storage.postgres import list_books

from .analyze import AnalysisModel
from .contracts import (
    ConversationMessage,
    ConversationState,
    PromptProfile,
    ResponseDepth,
    TurnDecision,
    TurnResult,
)
from .prompts import (
    DEFAULT_PROMPT_PROFILE,
    build_answer_messages,
    profile_version,
    resolve_answer_archetype,
    resolve_response_depth,
)
from .grounding import GroundingPolicy
from .query import ChatModel, execute_query, openrouter_model
from .side_context import SideContext
from .streaming import TokenCallback, invoke_with_streaming


def new_conversation_state(
    *,
    book_ids: Sequence[int] | None = None,
    conversation_id: str | None = None,
) -> ConversationState:
    return ConversationState(
        conversation_id=conversation_id or str(uuid4()),
        book_ids=sorted({int(identifier) for identifier in book_ids or ()}),
    )


def _select_state(
    state: ConversationState | None,
    book_ids: Sequence[int] | None,
) -> ConversationState:
    """Continue the conversation, or start a new one if its scope changed.

    Answers already given were grounded in the previous selection, so a
    different set of books is a different conversation rather than a filter
    applied retroactively to one already under way.
    """

    requested = sorted({int(identifier) for identifier in book_ids or ()})
    if state is None or (requested and state.book_ids and state.book_ids != requested):
        return new_conversation_state(book_ids=requested)
    state = state.model_copy(deep=True)
    state.book_ids = state.book_ids or requested
    return state


def _hierarchy_query(decision: TurnDecision) -> str:
    scope = decision.resolved_scope
    if scope is None:
        raise ValueError("hierarchy decision has no scope")
    if decision.route == "verbatim_reading":
        # "this document" for a book-kind scope, exactly as the summary
        # wording below does, and for the same reason: re-execution is already
        # narrowed to the resolved id, so a generic noun cannot drift.
        subject = "this document" if scope.kind == "book" else scope.display_path
        return f"Read {subject} verbatim."
    if decision.route == "hierarchy_list":
        if scope.kind == "book":
            return "What chapters does this book have?"
        return f"What sections are present in {scope.display_path}?"
    if scope.kind == "book":
        # Book-level scopes are also how a complete paper is represented.
        # Re-execution is narrowed to the resolved id, so this generic wording
        # cannot drift to another selected document.
        return "Summarize this document."
    if scope.kind == "chapter":
        return f"Summarize {scope.display_path}."
    # The full canonical path disambiguates repeated section titles and works
    # for both books and papers. Reconstructing every section as "in chapter"
    # is invalid for papers, whose top-level nodes are sections by design.
    return f"Summarize {scope.display_path}."


def _library_listing(
    question: str,
    state: ConversationState,
    *,
    database_url: str | None,
    owner_id: str | UUID,
    prompt_profile: PromptProfile,
    response_depth: ResponseDepth,
    routing_reason: str,
) -> TurnResult:
    """Render canonical library metadata without retrieval or generation."""

    normalized = question.casefold()
    document_type = (
        "paper"
        if re.search(r"\bpaper(?:s)?\b", normalized)
        else "book"
        if re.search(r"\bbook(?:s)?\b", normalized)
        else None
    )
    selected = set(state.book_ids)
    with database_connection(database_url, readonly=True) as connection:
        rows = list_books(
            connection,
            owner_id=owner_id,
            document_type=document_type,
        )
    if selected:
        rows = [row for row in rows if row["id"] in selected]

    singular = document_type or "document"
    plural = {"paper": "papers", "book": "books"}.get(singular, "documents")
    if not rows:
        answer = f"No ready {plural} are available in the selected library."
    else:
        lines = []
        for index, row in enumerate(rows, start=1):
            details = []
            if row.get("author"):
                details.append(str(row["author"]))
            if row.get("page_count"):
                pages = int(row["page_count"])
                details.append(f"{pages} {'page' if pages == 1 else 'pages'}")
            suffix = f" — {', '.join(details)}" if details else ""
            lines.append(f"{index}. {row['title']}{suffix}")
        noun = singular if len(rows) == 1 else plural
        answer = (
            f"{len(rows)} ready {noun} "
            f"{'is' if len(rows) == 1 else 'are'} available:\n\n"
            + "\n".join(lines)
        )

    return TurnResult(
        question=question,
        answer=answer,
        route="library_list",
        history_dependency="independent",
        outcome="answer",
        response_depth=response_depth,
        routing_reason=routing_reason,
        prompt_profile_version=profile_version(prompt_profile),
        source_type="book_library",
    )


def _transform(
    question: str,
    state: ConversationState,
    model: ChatModel | None,
    prompt_profile: PromptProfile,
    response_depth: ResponseDepth,
    routing_reason: str,
    token_callback: TokenCallback | None = None,
) -> TurnResult:
    model = model or openrouter_model()
    response = invoke_with_streaming(
        model,
        build_answer_messages(
            profile=prompt_profile,
            question=question,
            evidence=state.previous_answer or "",
            archetype="answer_transform",
            depth=response_depth,
            request_context="Transform the previous answer rather than retrieving again.",
            additional_grounding=(
                "Transform the prior answer as requested. Add no facts and "
                "preserve every citation marker and qualification."
            ),
        ),
        token_callback=token_callback,
    )
    return TurnResult(
        question=question,
        answer=str(response.content),
        route="prior_answer_transform",
        history_dependency="dependent",
        resolved_scope=state.active_scope,
        evidence=list(state.previous_evidence),
        citations=list(state.previous_citations),
        outcome="answer",
        answer_archetype="answer_transform",
        response_depth=response_depth,
        routing_reason=routing_reason,
        prompt_profile_version=profile_version(prompt_profile),
    )


def execute_decision(
    question: str,
    decision: TurnDecision,
    state: ConversationState,
    *,
    database_url: str | None,
    owner_id: str | UUID,
    retrieval_mode: RetrievalMode,
    model: ChatModel | None,
    token_callback: TokenCallback | None = None,
    prompt_profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
    turn_book_ids: Sequence[int] | None = None,
    side_context: SideContext | None = None,
    # False hands escalation to the caller. The query layer falls through to
    # external QA on its own when retrieval comes up empty, which is right for
    # a turn with nothing above it and wrong for one climbing a ladder: it
    # would skip the library rung and leave the jump unrecorded.
    allow_external_fallback: bool = True,
) -> TurnResult:
    profile = prompt_profile or DEFAULT_PROMPT_PROFILE
    resolved_depth = resolve_response_depth(question, response_depth)
    report = side_context.report if side_context else None
    if decision.route == "library_list":
        listed = _library_listing(
            question,
            state,
            database_url=database_url,
            owner_id=owner_id,
            prompt_profile=profile,
            response_depth=resolved_depth,
            routing_reason=decision.reason,
        )
        return listed.model_copy(update={"side_context": report})
    if decision.route == "clarify":
        return TurnResult(
            question=question,
            answer=decision.clarification_question or "Could you clarify?",
            route="clarify",
            history_dependency=decision.history_dependency,
            outcome="clarify",
            response_depth=resolved_depth,
            routing_reason=decision.reason,
            prompt_profile_version=profile_version(profile),
            side_context=report,
        )
    if decision.route == "prior_answer_transform":
        transformed = _transform(
            question,
            state,
            model,
            profile,
            resolved_depth,
            decision.reason,
            token_callback=token_callback,
        )
        return transformed.model_copy(update={"side_context": report})

    if decision.route == "external_qa":
        from .external_qa import execute_external_qa

        ext_result = execute_external_qa(
            question,
            state,
            model=model,
            token_callback=token_callback,
            response_depth=resolved_depth,
            history_dependency=decision.history_dependency,
            standalone_query=decision.standalone_query or question,
            routing_reason=decision.reason,
        )
        return ext_result.model_copy(update={"side_context": report})

    is_hierarchy = decision.route in {
        "hierarchy_summary",
        "hierarchy_list",
        "verbatim_reading",
    }
    execution_question = (
        _hierarchy_query(decision)
        if is_hierarchy
        else decision.standalone_query or question
    )
    # A hierarchy decision already names one book. `execute_query` re-derives
    # the scope from the rendered sentence, and that sentence carries no book,
    # so resolving it against the whole selection made "summarize chapter 1"
    # ambiguous across every book the reader had open - after the analyser had
    # already decided which one they meant.
    execution_book_ids = (
        (decision.resolved_scope.book_id,)
        if is_hierarchy and decision.resolved_scope
        else turn_book_ids or state.book_ids or None
    )
    result = execute_query(
        execution_question,
        database_url=database_url,
        book_ids=execution_book_ids,
        retrieval_mode=retrieval_mode,
        owner_id=owner_id,
        model=model,
        token_callback=token_callback,
        force_retrieval=decision.route == "retrieval_qa",
        prompt_profile=profile,
        response_depth=resolved_depth,
        routing_reason=decision.reason,
        answer_archetype=resolve_answer_archetype(question, decision.route),
        pinned_chunk_ids=side_context.pinned_chunk_ids if side_context else (),
        request_context=side_context.request_context if side_context else "",
        allow_external_fallback=allow_external_fallback,
        # Retrieval does not read this; the escalation to external QA does.
        # Without it a follow-up that falls through to the model or the web
        # arrives stripped of the answer it is a follow-up to.
        conversation=state,
        # A source-first turn is asked about a page the reader is looking at,
        # and in a book of architecture diagrams that page's answer is often a
        # picture. The main chat is unchanged.
        send_figures=bool(side_context and side_context.sources_present),
    )
    updates = {
        "question": question,
        "history_dependency": decision.history_dependency,
        "standalone_query": execution_question,
        "side_context": report,
    }
    if result.route == "retrieval_qa":
        chapter_refs = set(
            re.findall(r"\bchapter\s+(\d+)\b", execution_question, re.IGNORECASE)
        )
        if len(chapter_refs) > 1:
            updates["resolved_scope"] = None
        elif state.active_scope and decision.history_dependency == "dependent":
            updates["resolved_scope"] = state.active_scope
        elif state.pending_clarification and decision.resolved_scope:
            updates["resolved_scope"] = decision.resolved_scope
        else:
            updates["resolved_scope"] = None
    return result.model_copy(update=updates)


def record_turn(
    state: ConversationState,
    question: str,
    result: TurnResult,
) -> ConversationState:
    state = state.model_copy(deep=True)
    if result.resolved_scope:
        # The resolved scope is recorded, but the conversation's book
        # selection is deliberately left alone: answering one turn from one
        # book must not silently narrow the rest of the conversation to it.
        state.active_scope = result.resolved_scope
    state.pending_clarification = question if result.route == "clarify" else None

    turn = sum(message.role == "user" for message in state.messages) + 1
    turn_id = f"{state.conversation_id}-t{turn}"
    state.messages.extend(
        [
            ConversationMessage(
                role="user",
                content=question,
                turn_id=turn_id,
            ),
            ConversationMessage(
                role="assistant",
                content=result.answer,
                turn_id=turn_id,
            ),
        ]
    )
    state.previous_route = result.route
    if result.outcome in {"answer", "abstain"}:
        state.previous_answer = result.answer
        state.previous_evidence = list(result.evidence)
        state.previous_citations = list(result.citations)
    return state


def execute_conversation_turn(
    question: str,
    state: ConversationState | None = None,
    *,
    owner_id: str | UUID,
    database_url: str | None = None,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
    turn_book_ids: Sequence[int] | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
    analysis_model: AnalysisModel | None = None,
    generation_model: ChatModel | None = None,
    token_callback: TokenCallback | None = None,
    prompt_profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
    side_context: SideContext | None = None,
    grounding_policy: GroundingPolicy | None = None,
) -> tuple[TurnResult, ConversationState]:
    load_dotenv()
    # `book_id` remains for the CLI and evaluation entry points, which study
    # one book at a time.
    selection = (
        book_ids
        if book_ids is not None
        else ([book_id] if book_id is not None else None)
    )
    current = _select_state(state, selection)
    from .graph import StudyGraphContext, study_turn_graph

    output = study_turn_graph.invoke(
        {"question": question, "conversation": current},
        config={
            "run_name": "study_turn",
            "tags": ["conversation", "rag"],
            "metadata": {
                "thread_id": current.conversation_id,
                "conversation_id": current.conversation_id,
                "book_ids": current.book_ids,
                "retrieval_mode": retrieval_mode,
                # A side turn is traced with what its priority context
                # actually resolved to, so the inclusion decision is
                # reviewable after the fact rather than only in the answer.
                "side_chat": side_context is not None,
                # The ladder shows up in the trace as the rung a turn started
                # on and what it was allowed to climb to, so a widening in the
                # recorded turn can be read against the policy that permitted
                # it.
                "grounding_policy": (
                    {
                        "source_book_ids": list(grounding_policy.source_book_ids),
                        "library_book_ids": list(grounding_policy.library_book_ids),
                        "allow_model_knowledge": (
                            grounding_policy.allow_model_knowledge
                        ),
                    }
                    if grounding_policy
                    else None
                ),
                **(
                    {
                        "side_chat_anchor_ids": list(
                            side_context.report.anchor_ids
                        ),
                        "side_chat_pinned_chunk_ids": list(
                            side_context.pinned_chunk_ids
                        ),
                        "side_chat_dropped_context": list(
                            side_context.report.dropped
                        ),
                    }
                    if side_context
                    else {}
                ),
            },
        },
        context=StudyGraphContext(
            owner_id=owner_id,
            database_url=database_url,
            retrieval_mode=retrieval_mode,
            analysis_model=analysis_model,
            generation_model=generation_model,
            token_callback=token_callback,
            prompt_profile=prompt_profile or DEFAULT_PROMPT_PROFILE,
            response_depth=response_depth,
            turn_book_ids=(
                tuple(sorted({int(identifier) for identifier in turn_book_ids}))
                if turn_book_ids
                else None
            ),
            side_context=side_context,
            grounding_policy=grounding_policy,
        ),
    )
    return output["result"], output["conversation"]
