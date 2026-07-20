"""Isolated evaluation of conversational turn analysis."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Callable, Protocol
import sqlite3

from storage.sqlite import connect_readonly
from study.analyze import analyze_turn
from study.contracts import (
    ConversationMessage,
    ConversationState,
    EvidenceRef,
    ScopeRef,
    TurnAnalysis,
)
from study.scope_candidates import find_scope_candidates

from .judge import QueryMeaningJudge


class TurnAnalyzer(Protocol):
    def invoke(
        self,
        question: str,
        *,
        state: ConversationState,
    ) -> TurnAnalysis: ...


class ProjectTurnAnalyzer:
    """Adapter over the Batch 2 analyzer for evaluation."""

    def __init__(
        self,
        *,
        source_path: str | Path = "data/books.sqlite3",
    ) -> None:
        self.source_path = source_path

    def invoke(
        self,
        question: str,
        *,
        state: ConversationState,
    ) -> TurnAnalysis:
        return analyze_turn(
            question,
            state,
            self.source_path,
        )


def _canonical_nodes(
    connection: sqlite3.Connection,
    book_id: int,
) -> dict[int, dict]:
    return {
        row["id"]: dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM nodes
            WHERE book_id = ?
            ORDER BY toc_index
            """,
            (book_id,),
        )
    }


def _scope_bounds(
    node_id: int,
    nodes: dict[int, dict],
) -> tuple[int, int]:
    descendants = [
        node
        for node in nodes.values()
        if _is_descendant(node["id"], node_id, nodes)
    ]
    return (
        min(node["start_page"] for node in descendants),
        max(node["end_page"] for node in descendants),
    )


def _is_descendant(
    candidate_id: int,
    ancestor_id: int,
    nodes: dict[int, dict],
) -> bool:
    current_id: int | None = candidate_id
    visited: set[int] = set()
    while current_id is not None and current_id not in visited:
        if current_id == ancestor_id:
            return True
        visited.add(current_id)
        node = nodes.get(current_id)
        current_id = node["parent_id"] if node else None
    return False


def _scope_ref(
    expected_scope: dict | None,
    *,
    book_id: int,
    nodes: dict[int, dict],
) -> ScopeRef | None:
    if expected_scope is None:
        return None
    node = nodes[expected_scope["node_id"]]
    start_page, end_page = _scope_bounds(node["id"], nodes)
    return ScopeRef(
        kind=expected_scope["kind"],
        book_id=book_id,
        node_id=node["id"],
        display_path=node["path_text"],
        start_page=start_page,
        end_page=end_page,
    )


def _gold_evidence(
    turn: dict,
    nodes: dict[int, dict],
) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            node_id=item["node_id"],
            pages=item["pages"],
            path=nodes[item["node_id"]]["path_text"],
            retrieval_method="gold_state",
        )
        for item in turn["expected_evidence"]
    ]


def _apply_gold_turn(
    state: ConversationState,
    turn: dict,
    *,
    nodes: dict[int, dict],
) -> ConversationState:
    """Advance with expected state so component errors never cascade."""

    updated = state.model_copy(deep=True)
    updated.messages.extend(
        [
            ConversationMessage(
                role="user",
                content=turn["user"],
                turn_id=turn["turn_id"],
            ),
            ConversationMessage(
                role="assistant",
                content=turn["reference_answer"],
                turn_id=turn["turn_id"],
            ),
        ]
    )
    active_update = turn["state_update"]
    if active_update == "set_active_scope":
        updated.active_scope = _scope_ref(
            turn["expected_scope"],
            book_id=updated.book_id,
            nodes=nodes,
        )
    elif active_update == "clear":
        updated.active_scope = None

    clarification_update = turn.get(
        "pending_clarification_update",
        "none",
    )
    if clarification_update == "set":
        updated.pending_clarification = turn["user"]
    elif clarification_update == "clear":
        updated.pending_clarification = None

    updated.previous_route = turn["expected_route"]
    if turn["answerable"]:
        updated.previous_answer = turn["reference_answer"]
        updated.previous_evidence = _gold_evidence(turn, nodes)
    return updated


def _state_snapshot(state: ConversationState) -> dict:
    def excerpt(value: str | None, limit: int = 500) -> str | None:
        if value is None or len(value) <= limit:
            return value
        return value[:limit].rstrip() + "…"

    return {
        "conversation_id": state.conversation_id,
        "book_id": state.book_id,
        "recent_messages": [
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
        "previous_route": state.previous_route,
        "previous_answer_excerpt": excerpt(state.previous_answer),
        "previous_evidence_nodes": [
            evidence.node_id for evidence in state.previous_evidence
        ],
    }


def _scope_matches(
    expected: dict | None,
    predicted: ScopeRef | None,
) -> bool:
    if expected is None:
        return predicted is None
    return (
        predicted is not None
        and predicted.kind == expected["kind"]
        and predicted.node_id == expected["node_id"]
    )


def _query_meaning(
    turn: dict,
    analysis: TurnAnalysis,
    judge: QueryMeaningJudge | None,
) -> dict:
    expected = turn["expected_standalone_query"]
    candidate = analysis.standalone_query
    presence_correct = (expected is None) == (candidate is None)
    if expected is None:
        return {
            "applicable": False,
            "presence_correct": presence_correct,
            "method": "not_applicable",
            "preserves_meaning": None,
            "judgment": None,
        }
    if not candidate:
        return {
            "applicable": True,
            "presence_correct": False,
            "method": "missing",
            "preserves_meaning": False,
            "judgment": None,
        }
    normalized_expected = " ".join(expected.casefold().split())
    normalized_candidate = " ".join(candidate.casefold().split())
    if normalized_expected == normalized_candidate:
        return {
            "applicable": True,
            "presence_correct": True,
            "method": "exact",
            "preserves_meaning": True,
            "judgment": {
                "preserves_meaning": True,
                "missing_concepts": [],
                "added_assumptions": [],
                "explanation": "The standalone query is an exact text match.",
            },
        }
    if judge is None:
        return {
            "applicable": True,
            "presence_correct": True,
            "method": "not_run",
            "preserves_meaning": None,
            "judgment": None,
        }
    try:
        judgment = judge.evaluate(
            current_message=turn["user"],
            expected_query=expected,
            candidate_query=candidate,
            turn_id=turn["turn_id"],
        )
        return {
            "applicable": True,
            "presence_correct": True,
            "method": "judge",
            "preserves_meaning": judgment.preserves_meaning,
            "judgment": judgment.model_dump(mode="json"),
        }
    except Exception as error:
        return {
            "applicable": True,
            "presence_correct": True,
            "method": "error",
            "preserves_meaning": None,
            "judgment": {"error": str(error)},
        }


def _judgment(
    turn: dict,
    analysis: TurnAnalysis | None,
    *,
    nodes: dict[int, dict],
    query_meaning: dict | None,
) -> dict:
    if analysis is None:
        return {
            "route_correct": False,
            "history_dependency_correct": False,
            "scope_correct": False,
            "scope_behavior_correct": False,
            "state_update_correct": False,
            "clarification_correct": False,
            "standalone_presence_correct": False,
            "standalone_query_exact_match": False,
            "invalid_scope_node_ids": [],
            "all_components_correct": False,
        }

    expected_update = {
        "active_scope": turn["state_update"],
        "pending_clarification": turn.get(
            "pending_clarification_update",
            "none",
        ),
    }
    clarification_expected = turn["expected_route"] == "clarify"
    clarification_present = bool(
        (analysis.clarification_question or "").strip()
    )
    clarification_correct = (
        analysis.route == "clarify" and clarification_present
        if clarification_expected
        else not clarification_present
    )
    invalid_scope_ids = []
    if (
        analysis.resolved_scope is not None
        and analysis.resolved_scope.node_id is not None
        and analysis.resolved_scope.node_id not in nodes
    ):
        invalid_scope_ids.append(analysis.resolved_scope.node_id)

    checks = {
        "route_correct": analysis.route == turn["expected_route"],
        "history_dependency_correct": (
            analysis.history_dependency == turn["history_dependency"]
        ),
        "scope_correct": _scope_matches(
            turn["expected_scope"],
            analysis.resolved_scope,
        ),
        "scope_behavior_correct": (
            analysis.scope_behavior == turn["scope_behavior"]
        ),
        "state_update_correct": (
            analysis.state_update.model_dump(mode="json")
            == expected_update
        ),
        "clarification_correct": clarification_correct,
        "standalone_presence_correct": (
            query_meaning["presence_correct"]
            if query_meaning is not None
            else False
        ),
        "standalone_query_exact_match": (
            analysis.standalone_query
            == turn["expected_standalone_query"]
        ),
        "invalid_scope_node_ids": invalid_scope_ids,
    }
    semantic = (
        query_meaning["preserves_meaning"]
        if query_meaning is not None
        else None
    )
    required_checks = [
        checks["route_correct"],
        checks["history_dependency_correct"],
        checks["scope_correct"],
        checks["scope_behavior_correct"],
        checks["state_update_correct"],
        checks["clarification_correct"],
        checks["standalone_presence_correct"],
        not invalid_scope_ids,
    ]
    if semantic is not None:
        required_checks.append(semantic)
    checks["all_components_correct"] = all(required_checks)
    return checks


def _mean(turns: list[dict], field: str) -> float:
    values = [turn["judgment"][field] for turn in turns]
    return sum(values) / len(values) if values else 0.0


def _aggregate(turns: list[dict]) -> dict:
    successful = [
        turn for turn in turns if turn["prediction"] is not None
    ]
    semantic = [
        turn["query_meaning"]["preserves_meaning"]
        for turn in turns
        if turn["query_meaning"] is not None
        and turn["query_meaning"]["applicable"]
        and turn["query_meaning"]["preserves_meaning"] is not None
    ]
    methods = Counter(
        turn["query_meaning"]["method"]
        for turn in turns
        if turn["query_meaning"] is not None
    )
    sources = Counter(
        turn["prediction"]["decision_source"]
        for turn in successful
    )
    return {
        "scored_turn_count": len(turns),
        "analysis_success_rate": (
            len(successful) / len(turns) if turns else 0.0
        ),
        "route_accuracy": _mean(turns, "route_correct"),
        "history_dependency_accuracy": _mean(
            turns,
            "history_dependency_correct",
        ),
        "scope_accuracy": _mean(turns, "scope_correct"),
        "scope_behavior_accuracy": _mean(
            turns,
            "scope_behavior_correct",
        ),
        "state_update_accuracy": _mean(
            turns,
            "state_update_correct",
        ),
        "clarification_accuracy": _mean(
            turns,
            "clarification_correct",
        ),
        "standalone_presence_accuracy": _mean(
            turns,
            "standalone_presence_correct",
        ),
        "standalone_exact_match_rate": _mean(
            turns,
            "standalone_query_exact_match",
        ),
        "semantic_query_preservation_rate": (
            sum(semantic) / len(semantic) if semantic else None
        ),
        "semantic_query_judged_count": len(semantic),
        "all_components_accuracy": _mean(
            turns,
            "all_components_correct",
        ),
        "analysis_error_count": len(turns) - len(successful),
        "query_judge_error_count": methods.get("error", 0),
        "invalid_scope_count": sum(
            len(turn["judgment"]["invalid_scope_node_ids"])
            for turn in turns
        ),
        "decision_source_counts": dict(sorted(sources.items())),
        "query_judgment_method_counts": dict(sorted(methods.items())),
    }


def evaluate_turn_analysis(
    gold: dict,
    *,
    analyzer: TurnAnalyzer,
    source_path: str | Path = "data/books.sqlite3",
    scored_turn_ids: set[str] | None = None,
    conversation_ids: set[str] | None = None,
    query_judge: QueryMeaningJudge | None = None,
    trace_runner=None,
    on_turn_complete: Callable[[dict], None] | None = None,
) -> dict:
    """Evaluate analysis with expected pre-turn state and no route execution."""

    book_id = gold["book"]["database_book_id"]
    with connect_readonly(source_path) as connection:
        nodes = _canonical_nodes(connection, book_id)

    conversation_results = []
    all_turns = []
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
        turn_results = []
        for turn in conversation["turns"]:
            selected = (
                scored_turn_ids is None
                or turn["turn_id"] in scored_turn_ids
            )
            before = _state_snapshot(state)
            if selected:
                candidates = find_scope_candidates(
                    turn["user"],
                    state,
                    source_path,
                )

                def operation():
                    analysis = analyzer.invoke(
                        turn["user"],
                        state=state.model_copy(deep=True),
                    )
                    meaning = _query_meaning(
                        turn,
                        analysis,
                        query_judge,
                    )
                    return analysis, meaning

                analysis = None
                query_meaning = None
                trace = None
                error = None
                try:
                    if trace_runner is None:
                        analysis, query_meaning = operation()
                    else:
                        (
                            (analysis, query_meaning),
                            trace,
                        ) = trace_runner(turn, state, operation)
                except Exception as caught:
                    error = str(caught)
                judgment = _judgment(
                    turn,
                    analysis,
                    nodes=nodes,
                    query_meaning=query_meaning,
                )
            state = _apply_gold_turn(state, turn, nodes=nodes)
            if not selected:
                continue
            item = {
                "turn_id": turn["turn_id"],
                "gold": deepcopy(turn),
                "prediction": (
                    analysis.model_dump(mode="json")
                    if analysis is not None
                    else None
                ),
                "analysis_error": error,
                "judgment": judgment,
                "query_meaning": query_meaning,
                "scope_candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in candidates
                ],
                "gold_state_before": before,
                "gold_state_after": _state_snapshot(state),
                "trace": trace,
            }
            turn_results.append(item)
            all_turns.append(item)
            if on_turn_complete is not None:
                on_turn_complete(item)
        if turn_results:
            conversation_results.append(
                {
                    "conversation_id": conversation["id"],
                    "title": conversation["title"],
                    "category": conversation["category"],
                    "tags": conversation["tags"],
                    "turns": turn_results,
                }
            )

    return {
        "set_id": gold["set_id"],
        "source_file_sha256": gold["book"]["source_file_sha256"],
        "book": deepcopy(gold["book"]),
        "evaluation_mode": "isolated_gold_state_turn_analysis",
        "summary": _aggregate(all_turns),
        "conversations": conversation_results,
    }
