"""Replay frozen conversations and score the current structured interface."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Protocol
import sqlite3

from study.contracts import (
    ConversationMessage,
    ConversationState,
    EvidenceRef,
    StateUpdate,
    SufficiencyDecision,
    TurnResult,
)
from study.query import QueryExecutionError, execute_query
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError

from .judge import AnswerJudge


class TurnExecutor(Protocol):
    def invoke(
        self,
        question: str,
        *,
        state: ConversationState,
    ) -> TurnResult: ...


class OracleRetriever(Protocol):
    def invoke(self, turn: dict) -> list: ...


class CurrentBaselineExecutor:
    """Adapter over the unchanged one-turn behavior."""

    def __init__(
        self,
        *,
        database_path: str = "data/retrieval.sqlite3",
        source_path: str = "data/books.sqlite3",
        chroma_path: str = "data/chroma",
        book_id: int | None = None,
        retrieval_mode: str = "hybrid",
        model=None,
    ) -> None:
        self.database_path = database_path
        self.source_path = source_path
        self.chroma_path = chroma_path
        self.book_id = book_id
        self.retrieval_mode = retrieval_mode
        self.model = model

    def invoke(
        self,
        question: str,
        *,
        state: ConversationState,
    ) -> TurnResult:
        try:
            return execute_query(
                question,
                database_path=self.database_path,
                source_path=self.source_path,
                chroma_path=self.chroma_path,
                book_id=self.book_id,
                retrieval_mode=self.retrieval_mode,
                model=self.model,
                state=state,
            )
        except (
            ContextWindowExceededError,
            QueryExecutionError,
            ScopeResolutionError,
        ) as error:
            return TurnResult(
                question=question,
                answer=f"Unable to complete request: {error}",
                route="error",
                history_dependency="independent",
                standalone_query=question,
                scope_behavior="global",
                state_update=StateUpdate(),
                sufficiency=SufficiencyDecision(
                    status="not_checked",
                    reason=str(error),
                ),
                outcome="error",
                errors=[str(error)],
            )


class GoldQueryRetriever:
    """Isolate retrieval with the gold query and a post-filtered gold scope."""

    def __init__(
        self,
        *,
        database_path: str = "data/retrieval.sqlite3",
        source_path: str = "data/books.sqlite3",
        chroma_path: str = "data/chroma",
        book_id: int,
        mode: str = "hybrid_rerank",
    ) -> None:
        self.database_path = database_path
        self.source_path = source_path
        self.chroma_path = chroma_path
        self.book_id = book_id
        self.mode = mode

    def invoke(self, turn: dict) -> list[EvidenceRef]:
        query = turn["expected_standalone_query"]
        if (
            not query
            or turn["expected_route"]
            not in {"retrieval_qa", "abstain"}
        ):
            return []

        from retrieval.langchain import BookRetriever

        scope = turn["expected_scope"]
        scoped = (
            scope is not None
            and turn["scope_behavior"] in {"hard_filter", "prefer_scope"}
        )
        fetch_count = 20 if scoped else 5
        documents = BookRetriever(
            database_path=self.database_path,
            chroma_path=self.chroma_path,
            mode=self.mode,
            book_id=self.book_id,
            k=fetch_count,
        ).invoke(query)
        if scoped:
            connection = _readonly(self.source_path)
            try:
                allowed = _descendants(connection, scope["node_id"])
            finally:
                connection.close()
            documents = [
                document
                for document in documents
                if document.metadata["node_id"] in allowed
            ]
        return [
            EvidenceRef(
                node_id=document.metadata["node_id"],
                pages=list(
                    range(
                        document.metadata["start_page"],
                        document.metadata["end_page"] + 1,
                    )
                ),
                path=document.metadata["path"],
                rank=rank,
                chunk_id=document.metadata["chunk_id"],
                chunk_index=document.metadata["chunk_index"],
                retrieval_method=document.metadata["retrieval_method"],
                score=document.metadata["score"],
                excerpt=" ".join(document.page_content.split())[:400],
            )
            for rank, document in enumerate(documents[:5], start=1)
        ]


def _readonly(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _canonical_nodes(
    connection: sqlite3.Connection,
    book_id: int,
) -> dict[int, dict]:
    nodes: dict[int, dict] = {}
    for row in connection.execute(
        """
        SELECT
            nodes.id,
            nodes.parent_id,
            nodes.path_text,
            nodes.start_page,
            nodes.end_page,
            GROUP_CONCAT(DISTINCT content_blocks.page_number) AS pages
        FROM nodes
        LEFT JOIN content_blocks ON content_blocks.node_id = nodes.id
        WHERE nodes.book_id = ?
        GROUP BY nodes.id
        """,
        (book_id,),
    ):
        item = dict(row)
        item["pages"] = {
            int(page)
            for page in (item["pages"] or "").split(",")
            if page
        }
        nodes[item["id"]] = item
    return nodes


def _descendants(
    connection: sqlite3.Connection,
    node_id: int,
) -> set[int]:
    return {
        row[0]
        for row in connection.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                SELECT id FROM nodes WHERE id = ?
                UNION ALL
                SELECT nodes.id
                FROM nodes
                JOIN subtree ON nodes.parent_id = subtree.id
            )
            SELECT id FROM subtree
            """,
            (node_id,),
        )
    }


def _scope_matches(expected: dict | None, result: TurnResult) -> bool:
    predicted = result.resolved_scope
    if expected is None:
        return predicted is None
    return (
        predicted is not None
        and predicted.kind == expected["kind"]
        and predicted.node_id == expected["node_id"]
    )


def _outcome_matches(turn: dict, result: TurnResult) -> bool:
    expected = (
        "answer"
        if turn["answerable"]
        else "clarify"
        if turn["expected_route"] == "clarify"
        else "abstain"
    )
    return result.outcome == expected


def _apply_result(
    state: ConversationState,
    *,
    turn_id: str,
    question: str,
    result: TurnResult,
) -> ConversationState:
    updated = state.model_copy(deep=True)
    updated.messages.extend(
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
    if result.state_update.active_scope == "set_active_scope":
        updated.active_scope = result.resolved_scope
    elif result.state_update.active_scope == "clear":
        updated.active_scope = None
    if result.state_update.pending_clarification == "set":
        updated.pending_clarification = question
    elif result.state_update.pending_clarification == "clear":
        updated.pending_clarification = None
    if result.outcome == "answer":
        updated.previous_answer = result.answer
        updated.previous_evidence = result.evidence
        updated.previous_route = result.route
    return updated


def _state_snapshot(state: ConversationState) -> dict:
    """Keep run artifacts inspectable without repeating full long answers."""

    def excerpt(value: str | None, limit: int = 500) -> str | None:
        if value is None or len(value) <= limit:
            return value
        return value[:limit].rstrip() + "…"

    return {
        "conversation_id": state.conversation_id,
        "book_id": state.book_id,
        "messages": [
            {
                "role": message.role,
                "turn_id": message.turn_id,
                "content": excerpt(message.content, 300),
            }
            for message in state.recent_messages(turns=3)
        ],
        "active_scope": (
            state.active_scope.model_dump(mode="json")
            if state.active_scope
            else None
        ),
        "pending_clarification": state.pending_clarification,
        "previous_answer": excerpt(state.previous_answer),
        "previous_evidence_nodes": [
            evidence.node_id for evidence in state.previous_evidence
        ],
        "previous_route": state.previous_route,
    }


def _blocked_result(question: str, dependency_ids: list[str]) -> TurnResult:
    dependencies = ", ".join(dependency_ids)
    return TurnResult(
        question=question,
        answer=f"Blocked because required prior turns failed: {dependencies}",
        route="error",
        history_dependency="dependent",
        standalone_query=None,
        scope_behavior="global",
        outcome="blocked",
        errors=[f"failed dependencies: {dependencies}"],
    )


def _safety_violations(
    connection: sqlite3.Connection,
    nodes: dict[int, dict],
    turn: dict,
    result: TurnResult,
    previous_results: dict[str, TurnResult],
) -> list[str]:
    violations: list[str] = []
    evidence_pages = {
        (item.node_id, page)
        for item in result.evidence
        for page in item.pages
    }
    for citation in result.citations:
        node = nodes.get(citation.node_id)
        if node is None or citation.page not in node["pages"]:
            violations.append(
                f"fabricated canonical citation {citation.marker}"
            )
        if (citation.node_id, citation.page) not in evidence_pages:
            violations.append(
                f"citation outside supplied evidence {citation.marker}"
            )

    scope = turn["expected_scope"]
    if (
        turn["scope_behavior"] == "hard_filter"
        and scope is not None
        and result.evidence
    ):
        allowed = _descendants(connection, scope["node_id"])
        escaped = sorted(
            {item.node_id for item in result.evidence} - allowed
        )
        if escaped:
            violations.append(
                f"hard-scope evidence escaped to nodes {escaped}"
            )

    if (
        turn["expected_route"] == "hierarchy_summary"
        and result.outcome == "answer"
    ):
        required = {
            evidence["node_id"]
            for evidence in turn["expected_evidence"]
            if evidence["role"] == "required"
        }
        present = {item.node_id for item in result.evidence}
        missing = sorted(required - present)
        if missing:
            violations.append(
                f"summary context silently omitted required nodes {missing}"
            )

    if (
        result.sufficiency.status == "insufficient"
        and result.outcome == "answer"
    ):
        violations.append("answered after an insufficient-evidence decision")

    if turn["expected_route"] == "prior_answer_transform":
        dependencies = turn.get("depends_on_turn_ids", [])
        if len(dependencies) == 1 and dependencies[0] in previous_results:
            source_nodes = {
                item.node_id
                for item in previous_results[dependencies[0]].evidence
            }
            added = sorted(
                {item.node_id for item in result.evidence} - source_nodes
            )
            if added:
                violations.append(
                    f"pure transformation introduced nodes {added}"
                )
    return violations


def _turn_judgment(
    connection: sqlite3.Connection,
    nodes: dict[int, dict],
    turn: dict,
    result: TurnResult,
    previous_results: dict[str, TurnResult],
) -> dict:
    required_nodes = {
        evidence["node_id"]
        for evidence in turn["expected_evidence"]
        if evidence["role"] == "required"
    }
    retrieved_nodes = [item.node_id for item in result.evidence]
    found_required = required_nodes.intersection(retrieved_nodes)
    evidence_recall = (
        len(found_required) / len(required_nodes)
        if required_nodes
        else None
    )
    cited_nodes = {citation.node_id for citation in result.citations}
    citation_coverage = (
        len(required_nodes.intersection(cited_nodes)) / len(required_nodes)
        if required_nodes
        else None
    )
    expected_update = {
        "active_scope": turn["state_update"],
        "pending_clarification": turn.get(
            "pending_clarification_update",
            "none",
        ),
    }
    predicted_update = result.state_update.model_dump(mode="json")
    outline_expected = turn.get("expected_outline_node_ids", [])
    outline_correct = (
        result.outline_node_ids == outline_expected
        if turn["expected_route"] == "hierarchy_list"
        else None
    )
    return {
        "route_correct": result.route == turn["expected_route"],
        "history_dependency_correct": (
            result.history_dependency == turn["history_dependency"]
        ),
        "standalone_query_exact_match": (
            result.standalone_query == turn["expected_standalone_query"]
        ),
        "standalone_query_note": (
            "Diagnostic only; semantic query preservation is not exact-match "
            "scored."
        ),
        "scope_correct": _scope_matches(turn["expected_scope"], result),
        "scope_behavior_correct": (
            result.scope_behavior == turn["scope_behavior"]
        ),
        "state_update_correct": predicted_update == expected_update,
        "outcome_correct": _outcome_matches(turn, result),
        "outline_correct": outline_correct,
        "required_nodes": sorted(required_nodes),
        "retrieved_nodes": retrieved_nodes,
        "missing_required_nodes": sorted(required_nodes - found_required),
        "required_evidence_recall": evidence_recall,
        "full_required_evidence_coverage": (
            evidence_recall == 1.0 if evidence_recall is not None else None
        ),
        "citation_required_node_coverage": citation_coverage,
        "safety_violations": _safety_violations(
            connection,
            nodes,
            turn,
            result,
            previous_results,
        ),
    }


def _oracle_judgment(turn: dict, evidence: list[EvidenceRef]) -> dict | None:
    if (
        not turn["expected_standalone_query"]
        or turn["expected_route"] not in {"retrieval_qa", "abstain"}
    ):
        return None
    required_nodes = {
        item["node_id"]
        for item in turn["expected_evidence"]
        if item["role"] == "required"
    }
    retrieved_nodes = [item.node_id for item in evidence]
    if not required_nodes:
        return {
            "query": turn["expected_standalone_query"],
            "retrieved_nodes": retrieved_nodes,
            "required_evidence_recall": None,
            "full_required_evidence_coverage": None,
            "note": (
                "Unanswerable retrieval probe; evidence sufficiency is scored "
                "downstream."
            ),
        }
    found = required_nodes.intersection(retrieved_nodes)
    recall = len(found) / len(required_nodes)
    return {
        "query": turn["expected_standalone_query"],
        "retrieved_nodes": retrieved_nodes,
        "missing_required_nodes": sorted(required_nodes - found),
        "required_evidence_recall": recall,
        "full_required_evidence_coverage": recall == 1.0,
        "note": (
            "Uses the gold standalone query. Scoped cases post-filter the "
            "first 20 book-wide reranked nodes in this baseline harness."
        ),
    }


def _mean_boolean(turns: list[dict], field: str) -> float:
    values = [
        turn["judgment"][field]
        for turn in turns
        if turn["judgment"][field] is not None
    ]
    return sum(values) / len(values) if values else 0.0


def _mean_optional(turns: list[dict], field: str) -> float | None:
    values = [
        turn["judgment"][field]
        for turn in turns
        if turn["judgment"][field] is not None
    ]
    return sum(values) / len(values) if values else None


def _aggregate(turns: list[dict]) -> dict:
    scored = [turn for turn in turns if turn["scored"]]
    routes = Counter(turn["prediction"]["route"] for turn in scored)
    judged_answers = [
        turn["answer_quality"]
        for turn in scored
        if turn["answer_quality"] is not None
        and "error" not in turn["answer_quality"]
    ]
    answer_metrics = {}
    if judged_answers:
        for field in (
            "correctness",
            "required_point_coverage",
            "usefulness",
        ):
            answer_metrics[f"mean_{field}_out_of_4"] = sum(
                judgment[field] for judgment in judged_answers
            ) / len(judged_answers)
    oracle_turns = [
        turn
        for turn in scored
        if turn["oracle_retrieval"] is not None
        and turn["oracle_retrieval"]["judgment"] is not None
        and turn["oracle_retrieval"]["judgment"][
            "required_evidence_recall"
        ]
        is not None
    ]
    return {
        "scored_turn_count": len(scored),
        "route_accuracy": _mean_boolean(scored, "route_correct"),
        "history_dependency_accuracy": _mean_boolean(
            scored,
            "history_dependency_correct",
        ),
        "scope_accuracy": _mean_boolean(scored, "scope_correct"),
        "scope_behavior_accuracy": _mean_boolean(
            scored,
            "scope_behavior_correct",
        ),
        "state_update_accuracy": _mean_boolean(
            scored,
            "state_update_correct",
        ),
        "outcome_accuracy": _mean_boolean(scored, "outcome_correct"),
        "outline_accuracy": _mean_optional(scored, "outline_correct"),
        "mean_required_evidence_recall": _mean_optional(
            scored,
            "required_evidence_recall",
        ),
        "full_required_evidence_coverage_rate": _mean_optional(
            scored,
            "full_required_evidence_coverage",
        ),
        "mean_citation_required_node_coverage": _mean_optional(
            scored,
            "citation_required_node_coverage",
        ),
        "safety_violation_count": sum(
            len(turn["judgment"]["safety_violations"])
            for turn in scored
        ),
        "predicted_route_counts": dict(sorted(routes.items())),
        "answer_quality": answer_metrics,
        "oracle_query_retrieval": {
            "scored_turn_count": len(oracle_turns),
            "mean_required_evidence_recall": (
                sum(
                    turn["oracle_retrieval"]["judgment"][
                        "required_evidence_recall"
                    ]
                    for turn in oracle_turns
                )
                / len(oracle_turns)
                if oracle_turns
                else None
            ),
            "full_required_evidence_coverage_rate": (
                sum(
                    turn["oracle_retrieval"]["judgment"][
                        "full_required_evidence_coverage"
                    ]
                    for turn in oracle_turns
                )
                / len(oracle_turns)
                if oracle_turns
                else None
            ),
        },
    }


def evaluate_conversations(
    gold: dict,
    *,
    executor: TurnExecutor,
    source_path: str | Path = "data/books.sqlite3",
    scored_turn_ids: set[str] | None = None,
    conversation_ids: set[str] | None = None,
    answer_judge: AnswerJudge | None = None,
    oracle_retriever: OracleRetriever | None = None,
    trace_runner=None,
    on_turn_complete=None,
) -> dict:
    """Replay selected conversations and return a serializable run payload."""

    book_id = gold["book"]["database_book_id"]
    connection = _readonly(source_path)
    nodes = _canonical_nodes(connection, book_id)
    conversation_results = []
    all_turns = []
    try:
        for conversation in gold["conversations"]:
            if (
                conversation_ids is not None
                and conversation["id"] not in conversation_ids
            ):
                continue
            state = ConversationState(
                conversation_id=conversation["id"],
                book_id=book_id,
            )
            previous_results: dict[str, TurnResult] = {}
            turn_results = []
            for turn in conversation["turns"]:
                turn_id = turn["turn_id"]
                scored = (
                    scored_turn_ids is None
                    or turn_id in scored_turn_ids
                )
                if scored_turn_ids is not None and not scored:
                    later_selected = any(
                        selected.startswith(conversation["id"] + "-t")
                        and int(selected.rsplit("t", 1)[1])
                        > int(turn_id.rsplit("t", 1)[1])
                        for selected in scored_turn_ids
                    )
                    if not later_selected:
                        continue
                dependencies = turn.get("depends_on_turn_ids", [])
                failed_dependencies = [
                    dependency
                    for dependency in dependencies
                    if dependency in previous_results
                    and previous_results[dependency].outcome
                    in {"error", "blocked"}
                ]
                before = _state_snapshot(state)
                if failed_dependencies:
                    result = _blocked_result(
                        turn["user"],
                        failed_dependencies,
                    )
                    trace = None
                elif trace_runner is not None:
                    result, trace = trace_runner(
                        executor,
                        turn,
                        state,
                    )
                else:
                    result = executor.invoke(turn["user"], state=state)
                    trace = None
                state = _apply_result(
                    state,
                    turn_id=turn_id,
                    question=turn["user"],
                    result=result,
                )
                previous_results[turn_id] = result
                judgment = _turn_judgment(
                    connection,
                    nodes,
                    turn,
                    result,
                    previous_results,
                )
                oracle_retrieval = None
                if oracle_retriever is not None and scored:
                    oracle_evidence = oracle_retriever.invoke(turn)
                    oracle_retrieval = {
                        "evidence": [
                            item.model_dump(mode="json")
                            for item in oracle_evidence
                        ],
                        "judgment": _oracle_judgment(
                            turn,
                            oracle_evidence,
                        ),
                    }
                answer_quality = None
                if answer_judge is not None and scored:
                    try:
                        answer_quality = answer_judge.evaluate(
                            question=turn["user"],
                            reference_answer=turn["reference_answer"],
                            candidate_answer=result.answer,
                            expected_route=turn["expected_route"],
                            answerable=turn["answerable"],
                            turn_id=turn_id,
                        ).model_dump(mode="json")
                    except Exception as error:
                        answer_quality = {"error": str(error)}
                item = {
                    "turn_id": turn_id,
                    "scored": scored,
                    "gold": deepcopy(turn),
                    "prediction": result.model_dump(mode="json"),
                    "state_before": before,
                    "state_after": _state_snapshot(state),
                    "judgment": judgment,
                    "oracle_retrieval": oracle_retrieval,
                    "answer_quality": answer_quality,
                    "trace": trace,
                }
                turn_results.append(item)
                all_turns.append(item)
                if on_turn_complete is not None:
                    on_turn_complete(item)
            conversation_results.append(
                {
                    "conversation_id": conversation["id"],
                    "title": conversation["title"],
                    "category": conversation["category"],
                    "tags": conversation["tags"],
                    "turns": turn_results,
                }
            )
    finally:
        connection.close()
    return {
        "set_id": gold["set_id"],
        "source_file_sha256": gold["book"]["source_file_sha256"],
        "book": deepcopy(gold["book"]),
        "summary": _aggregate(all_turns),
        "conversations": conversation_results,
    }
