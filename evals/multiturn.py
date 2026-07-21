"""Sequential evaluation of the real conversational study flow."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from study.contracts import ConversationState, TurnResult
from study.conversation import execute_conversation_turn, new_conversation_state


class AnswerJudge(Protocol):
    def evaluate(self, **kwargs): ...


class TurnRunner(Protocol):
    def __call__(
        self, question: str, state: ConversationState
    ) -> tuple[TurnResult, ConversationState]: ...


@dataclass
class ProjectRunner:
    database_path: str | Path = "data/retrieval.sqlite3"
    source_path: str | Path = "data/books.sqlite3"
    chroma_path: str | Path = "data/chroma"
    retrieval_mode: str = "hybrid"

    def __call__(self, question, state):
        return execute_conversation_turn(
            question,
            state,
            database_path=self.database_path,
            source_path=self.source_path,
            chroma_path=self.chroma_path,
            retrieval_mode=self.retrieval_mode,
        )


def _required_nodes(turn: dict) -> set[int]:
    return {
        item["node_id"]
        for item in turn.get("expected_evidence", [])
        if item.get("role") == "required"
    }


def _mean(rows: list[dict], field: str) -> float:
    values = [row["checks"][field] for row in rows if field in row["checks"]]
    return sum(values) / len(values) if values else 0.0


def _score(turn: dict, result: TurnResult, state: ConversationState) -> dict:
    expected_scope = turn.get("expected_scope")
    actual_scope = result.resolved_scope or (
        state.active_scope if expected_scope else None
    )
    expected_node = expected_scope.get("node_id") if expected_scope else None
    actual_node = actual_scope.node_id if actual_scope else None

    required = _required_nodes(turn)
    retrieved = {item.node_id for item in result.evidence}
    recall = len(required & retrieved) / len(required) if required else 1.0

    evidence_nodes = {item.node_id for item in result.evidence}
    citations_valid = all(
        citation.node_id in evidence_nodes for citation in result.citations
    )
    expected_outcome = (
        "answer"
        if turn.get("answerable", True)
        else (
            "clarify"
            if turn["expected_route"] == "clarify"
            else "abstain"
        )
    )
    return {
        "route": result.route == turn["expected_route"],
        "history_dependency": (
            result.history_dependency == turn["history_dependency"]
        ),
        "scope": actual_node == expected_node,
        "outcome": result.outcome == expected_outcome,
        "evidence_recall": recall,
        "citations_valid": citations_valid,
        "standalone_exact": (
            (result.standalone_query or "").strip().casefold()
            == turn.get("expected_standalone_query", "").strip().casefold()
        ),
    }


def evaluate_conversations(
    conversations: list[dict],
    runner: TurnRunner,
    *,
    book_id: int,
    answer_judge: AnswerJudge | None = None,
    on_turn: Callable[[str], None] | None = None,
) -> dict:
    """Replay predicted state and score only externally meaningful behavior."""

    rows = []
    errors = []
    for conversation in conversations:
        state = new_conversation_state(
            book_id=book_id,
            conversation_id=conversation["id"],
        )
        for turn in conversation["turns"]:
            turn_id = turn["turn_id"]
            if on_turn:
                on_turn(turn_id)
            try:
                result, state = runner(turn["user"], state)
                checks = _score(turn, result, state)
                judgment: dict[str, Any] | None = None
                if answer_judge is not None:
                    judgment = answer_judge.evaluate(
                        question=turn["user"],
                        reference_answer=turn["reference_answer"],
                        candidate_answer=result.answer,
                        expected_route=turn["expected_route"],
                        answerable=turn["answerable"],
                        turn_id=turn_id,
                    ).model_dump(mode="json")
                rows.append(
                    {
                        "conversation_id": conversation["id"],
                        "conversation_title": conversation["title"],
                        "turn_id": turn_id,
                        "gold": turn,
                        "prediction": result.model_dump(mode="json"),
                        "state": state.model_dump(mode="json"),
                        "checks": checks,
                        "answer_judgment": judgment,
                    }
                )
            except Exception as error:
                errors.append({"turn_id": turn_id, "error": str(error)})
                rows.append(
                    {
                        "conversation_id": conversation["id"],
                        "conversation_title": conversation["title"],
                        "turn_id": turn_id,
                        "gold": turn,
                        "prediction": None,
                        "state": state.model_dump(mode="json"),
                        "checks": {
                            "route": False,
                            "history_dependency": False,
                            "scope": False,
                            "outcome": False,
                            "evidence_recall": 0.0,
                            "citations_valid": False,
                            "standalone_exact": False,
                        },
                        "answer_judgment": None,
                        "error": str(error),
                    }
                )

    return {
        "summary": {
            "turns": len(rows),
            "route_accuracy": _mean(rows, "route"),
            "history_dependency_accuracy": _mean(
                rows, "history_dependency"
            ),
            "scope_accuracy": _mean(rows, "scope"),
            "outcome_accuracy": _mean(rows, "outcome"),
            "required_evidence_recall": _mean(rows, "evidence_recall"),
            "citation_validity": _mean(rows, "citations_valid"),
            "standalone_exact_accuracy": _mean(rows, "standalone_exact"),
            "errors": len(errors),
        },
        "turns": rows,
        "errors": errors,
    }
