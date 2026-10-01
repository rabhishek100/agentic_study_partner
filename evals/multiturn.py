"""Sequential evaluation of the real conversational study flow."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from study.contracts import ConversationState, TurnResult
from study.conversation import execute_conversation_turn, new_conversation_state
from evals.scoring import BOOK_ANSWER_ROUTES, SCORING_VERSION, book_citations, mean, metric_counts


class AnswerJudge(Protocol):
    def evaluate(self, **kwargs): ...


class TurnRunner(Protocol):
    def __call__(
        self, question: str, state: ConversationState
    ) -> tuple[TurnResult, ConversationState]: ...


@dataclass
class ProjectRunner:
    owner_id: str | UUID
    database_url: str | None = None
    retrieval_mode: str = "hybrid_rerank"

    def __call__(self, question, state):
        return execute_conversation_turn(
            question,
            state,
            owner_id=self.owner_id,
            database_url=self.database_url,
            retrieval_mode=self.retrieval_mode,
        )


def _required_nodes(turn: dict) -> set[int]:
    return {
        item["node_id"]
        for item in turn.get("expected_evidence", [])
        if item.get("role") == "required"
    }


_mean = mean


def _score(turn: dict, result: TurnResult, state: ConversationState) -> dict:
    expected_scope = turn.get("expected_scope")
    actual_scope = result.resolved_scope or (
        state.active_scope if expected_scope else None
    )
    expected_node = expected_scope.get("node_id") if expected_scope else None
    actual_node = actual_scope.node_id if actual_scope else None

    required = _required_nodes(turn)
    retrieved = {item.node_id for item in result.evidence}
    recall = len(required & retrieved) / len(required) if required else None
    citations_valid = book_citations(result, required=turn.get("answerable", True)
                                    and turn["expected_route"] in BOOK_ANSWER_ROUTES)
    expected_outcome = (
        "answer"
        if turn.get("answerable", True)
        else ("clarify" if turn["expected_route"] == "clarify" else "abstain")
    )
    checks = {
        "route": result.route == turn["expected_route"],
        "history_dependency": (result.history_dependency == turn["history_dependency"]),
        "scope": actual_node == expected_node,
        "outcome": result.outcome == expected_outcome,
        "citations_valid": citations_valid,
        "standalone_exact": (
            (result.standalone_query or "").strip().casefold()
            == (turn.get("expected_standalone_query") or "").strip().casefold()
        ),
    }
    if turn.get("answerable", True):
        checks["evidence_recall"] = recall
    return checks


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
    judge_errors = []
    for conversation in conversations:
        state = new_conversation_state(
            # The multi-turn gold set is defined against one book at a time.
            book_ids=[book_id] if book_id is not None else None,
            conversation_id=conversation["id"],
        )
        for turn in conversation["turns"]:
            turn_id = turn["turn_id"]
            if on_turn:
                on_turn(turn_id)
            try:
                previous_state = state.model_dump(mode="json")
                result, state = runner(turn["user"], state)
                checks = _score(turn, result, state)
                judgment: dict[str, Any] | None = None
                judge_error = None
                if answer_judge is not None:
                    try:
                        judgment = answer_judge.evaluate(
                            question=turn["user"], reference_answer=turn["reference_answer"],
                            candidate_answer=result.answer, expected_route=turn["expected_route"],
                            answerable=turn.get("answerable", True), turn_id=turn_id,
                            evidence=[item.model_dump(mode="json") for item in result.evidence],
                            citations=[item.model_dump(mode="json") for item in result.citations],
                            history=previous_state, expected_evidence=turn.get("expected_evidence", []),
                            deterministic_citation_validity=checks["citations_valid"],
                        ).model_dump(mode="json")
                    except Exception as error:
                        judge_error = str(error)
                        judge_errors.append({"turn_id": turn_id, "error": judge_error})
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
                        "judge_error": judge_error,
                    }
                )
            except Exception as error:
                errors.append({"turn_id": turn_id, "error": str(error)})
                checks = {
                    "route": False,
                    "history_dependency": False,
                    "scope": False,
                    "outcome": False,
                    "citations_valid": False if turn.get("answerable", True) and turn["expected_route"] in BOOK_ANSWER_ROUTES else None,
                    "standalone_exact": False,
                }
                if turn.get("answerable", True):
                    checks["evidence_recall"] = 0.0 if _required_nodes(turn) else None
                rows.append(
                    {
                        "conversation_id": conversation["id"],
                        "conversation_title": conversation["title"],
                        "turn_id": turn_id,
                        "gold": turn,
                        "prediction": None,
                        "state": state.model_dump(mode="json"),
                        "checks": checks,
                        "answer_judgment": None,
                        "error": str(error),
                    }
                )

    return {
        "scoring_version": SCORING_VERSION,
        "metric_counts": metric_counts(rows),
        "summary": {
            "turns": len(rows),
            "route_accuracy": _mean(rows, "route"),
            "history_dependency_accuracy": _mean(rows, "history_dependency"),
            "scope_accuracy": _mean(rows, "scope"),
            "outcome_accuracy": _mean(rows, "outcome"),
            "required_evidence_recall": _mean(rows, "evidence_recall"),
            "citation_validity": _mean(rows, "citations_valid"),
            "standalone_exact_accuracy": _mean(rows, "standalone_exact"),
            "errors": len(errors),
            "judge_errors": len(judge_errors),
            "generated": sum(row["prediction"] is not None for row in rows),
            "judged": sum(row["answer_judgment"] is not None for row in rows),
        },
        "turns": rows,
        "errors": errors,
        "judge_errors": judge_errors,
    }
