"""Execute and score the interview-answer seed through the real study flow."""

from __future__ import annotations

import re
import signal
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from evals.interview_dataset import InterviewCase, InterviewDataset
from storage.database import connection as database_connection
from storage.database import parse_owner_id
from study.contracts import ConversationState, PromptProfile, TurnResult
from study.conversation import execute_conversation_turn, new_conversation_state
from study.prompts import DEFAULT_PROMPT_PROFILE, profile_version

EVIDENCE_PREFACE = re.compile(
    r"^\s*(?:#+\s*)?(?:here(?:'s| is)\b.{0,120}\b|this answer\b.{0,80}\b)?"
    r"(?:based on (?:(?:the|this|provided)\s+)*(?:book\s+)?evidence|"
    r"according to (?:the |this )?(?:book|source|evidence))",
    re.IGNORECASE | re.DOTALL,
)


class CaseDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def _case_deadline(seconds: float | None):
    """Interrupt one synchronous case on Unix so checkpoints can progress."""

    if seconds is None or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum, _frame):
        raise CaseDeadlineExceeded(
            f"case exceeded the {seconds:g}-second evaluation deadline"
        )

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


class InterviewAnswerJudge(Protocol):
    def evaluate(self, **kwargs): ...


class InterviewCaseRunner(Protocol):
    def __call__(
        self,
        case: InterviewCase,
        *,
        book_id: int,
    ) -> tuple[TurnResult, ConversationState]: ...


@dataclass
class ProjectInterviewRunner:
    owner_id: str | UUID
    database_url: str | None = None
    retrieval_mode: str = "hybrid_rerank"
    prompt_profile: PromptProfile = field(
        default_factory=lambda: DEFAULT_PROMPT_PROFILE
    )

    def _hydrate_retrieval_evidence(self, result: TurnResult) -> TurnResult:
        """Give the evaluation judge the full chunks seen by generation.

        Reader-facing results intentionally keep short excerpts, but judging a
        complete answer against 400-character previews creates false
        unsupported-claim findings for text later in an otherwise retrieved
        chunk. This evaluation-only copy preserves the production API shape.
        """

        chunk_ids = [
            item.chunk_id for item in result.evidence if item.chunk_id is not None
        ]
        if not chunk_ids:
            return result
        with database_connection(self.database_url, readonly=True) as source:
            rows = source.execute(
                """
                select id, text
                from chunks
                where owner_id = %s and id = any(%s)
                """,
                (parse_owner_id(self.owner_id), chunk_ids),
            ).fetchall()
        text_by_id = {row["id"]: row["text"] for row in rows}
        return result.model_copy(
            update={
                "evidence": [
                    item.model_copy(
                        update={
                            "excerpt": text_by_id.get(item.chunk_id, item.excerpt),
                        }
                    )
                    for item in result.evidence
                ]
            }
        )

    def __call__(self, case: InterviewCase, *, book_id: int):
        state = new_conversation_state(
            book_ids=[book_id],
            conversation_id=f"interview-eval-{case.id}",
        )
        for prior in case.prior_turns:
            _, state = execute_conversation_turn(
                prior.user,
                state,
                owner_id=self.owner_id,
                database_url=self.database_url,
                retrieval_mode=self.retrieval_mode,
                prompt_profile=self.prompt_profile,
                response_depth="interview",
            )
        result, state = execute_conversation_turn(
            case.prompt,
            state,
            owner_id=self.owner_id,
            database_url=self.database_url,
            retrieval_mode=self.retrieval_mode,
            prompt_profile=self.prompt_profile,
            response_depth=case.ui_requested_depth,
        )
        return self._hydrate_retrieval_evidence(result), state


def _mean(rows: list[dict], field: str) -> float:
    values = [row["checks"][field] for row in rows if field in row["checks"]]
    return sum(values) / len(values) if values else 0.0


def _citation_validity(result: TurnResult) -> bool:
    evidence_pages: dict[int, set[int]] = {}
    for item in result.evidence:
        evidence_pages.setdefault(item.node_id, set()).update(item.pages)
    return all(
        citation.page in evidence_pages.get(citation.node_id, set())
        for citation in result.citations
    )


def score_case(case: InterviewCase, result: TurnResult) -> dict:
    required_evidence = [
        item for item in case.candidate_evidence if item.role == "required"
    ]

    def anchor_is_covered(anchor) -> bool:
        return any(
            item.node_id == anchor.node_id
            or item.path.startswith(anchor.path.rstrip() + " :: ")
            for item in result.evidence
        )

    expected_outcome = "answer" if case.answerable else "abstain"
    checks: dict[str, bool | float] = {
        "route": result.route == case.expected_route,
        "outcome": result.outcome == expected_outcome,
        "depth": result.response_depth == case.expected_depth,
        "archetype": result.answer_archetype == case.expected_archetype,
        "citations_valid": _citation_validity(result),
        "prompt_version_present": bool(result.prompt_profile_version),
    }
    if case.answerable:
        checks["avoids_evidence_preface"] = not bool(
            EVIDENCE_PREFACE.search(result.answer[:300])
        )
        checks["evidence_recall"] = (
            sum(anchor_is_covered(anchor) for anchor in required_evidence)
            / len(required_evidence)
            if required_evidence
            else 1.0
        )
        checks["citations_present"] = bool(result.citations)
    else:
        checks["abstention_has_no_citations"] = not result.citations
    return checks


def _judge_summary(rows: list[dict]) -> dict:
    dimensions = (
        "grounded_correctness",
        "interview_readiness",
        "coverage",
        "depth_adherence",
        "clarity_memorability",
        "follow_up_quality",
        "citation_quality",
    )
    means: dict[str, float] = {}
    focused_scores: list[int] = []
    judged = 0
    for dimension in dimensions:
        values = [
            row["answer_judgment"][dimension]
            for row in rows
            if row.get("answer_judgment") is not None
            and dimension in row["case"]["scoring_focus"]
        ]
        if values:
            means[dimension] = sum(values) / len(values)
    for row in rows:
        judgment = row.get("answer_judgment")
        if judgment is None:
            continue
        judged += 1
        focused_scores.extend(
            judgment[dimension] for dimension in row["case"]["scoring_focus"]
        )
    return {
        "judged_cases": judged,
        "dimension_means_0_to_4": means,
        "focused_mean_0_to_4": (
            sum(focused_scores) / len(focused_scores) if focused_scores else None
        ),
    }


def _judge_prediction(
    *,
    answer_judge: InterviewAnswerJudge,
    dataset_id: str,
    case: dict,
    prediction: dict,
    deterministic_citation_validity: bool,
) -> dict:
    return answer_judge.evaluate(
        case=case,
        candidate_answer=prediction["answer"],
        dataset_id=dataset_id,
        evidence=prediction.get("evidence", []),
        citations=prediction.get("citations", []),
        deterministic_citation_validity=deterministic_citation_validity,
    ).model_dump(mode="json")


def normalize_interview_evaluation(evaluation: dict) -> dict:
    """Backfill derived checks when rendering or rejudging older run files."""

    for row in evaluation.get("cases", []):
        case = row.get("case", {})
        prediction = row.get("prediction")
        checks = row.setdefault("checks", {})
        if case.get("answerable") and prediction is not None:
            checks["avoids_evidence_preface"] = not bool(
                EVIDENCE_PREFACE.search(prediction.get("answer", "")[:300])
            )
    evaluation.setdefault("summary", {})["evidence_preface_avoidance"] = _mean(
        evaluation.get("cases", []),
        "avoids_evidence_preface",
    )
    return evaluation


def rejudge_interview_evaluation(
    evaluation: dict,
    answer_judge: InterviewAnswerJudge,
) -> dict:
    """Re-score saved generations without paying to generate them again."""

    evaluation = normalize_interview_evaluation(
        {
            **evaluation,
            "cases": [dict(row) for row in evaluation["cases"]],
            "judge_errors": [],
        }
    )
    for row in evaluation["cases"]:
        prediction = row.get("prediction")
        if prediction is None:
            row["answer_judgment"] = None
            continue
        try:
            row["answer_judgment"] = _judge_prediction(
                answer_judge=answer_judge,
                dataset_id=evaluation["dataset_id"],
                case=row["case"],
                prediction=prediction,
                deterministic_citation_validity=bool(
                    row["checks"].get("citations_valid")
                ),
            )
            row.pop("judge_error", None)
        except Exception as error:  # noqa: BLE001 - retain every saved generation
            message = str(error)
            row["answer_judgment"] = None
            row["judge_error"] = message
            evaluation["judge_errors"].append(
                {"case_id": row["case_id"], "error": message}
            )
    evaluation["summary"] = {
        **evaluation["summary"],
        "judge_errors": len(evaluation["judge_errors"]),
        "judge": _judge_summary(evaluation["cases"]),
    }
    return normalize_interview_evaluation(evaluation)


def _evaluation_snapshot(
    *,
    dataset: InterviewDataset,
    rows: list[dict],
    errors: list[dict],
    judge_errors: list[dict],
    run_metadata: dict[str, Any] | None,
) -> dict:
    latencies = [row["latency_seconds"] for row in rows]
    evaluation = {
        "dataset_id": dataset.dataset_id,
        "dataset_review_status": dataset.review.status,
        "run_metadata": run_metadata or {},
        "summary": {
            "cases": len(rows),
            "route_accuracy": _mean(rows, "route"),
            "outcome_accuracy": _mean(rows, "outcome"),
            "depth_accuracy": _mean(rows, "depth"),
            "archetype_accuracy": _mean(rows, "archetype"),
            "required_evidence_recall": _mean(rows, "evidence_recall"),
            "citation_validity": _mean(rows, "citations_valid"),
            "citation_presence": _mean(rows, "citations_present"),
            "evidence_preface_avoidance": _mean(rows, "avoids_evidence_preface"),
            "prompt_version_presence": _mean(rows, "prompt_version_present"),
            "mean_latency_seconds": (
                sum(latencies) / len(latencies) if latencies else 0.0
            ),
            "errors": len(errors),
            "judge_errors": len(judge_errors),
        },
        "cases": rows,
        "errors": errors,
        "judge_errors": judge_errors,
    }
    evaluation["summary"]["judge"] = _judge_summary(rows)
    return normalize_interview_evaluation(evaluation)


def evaluate_interview_cases(
    dataset: InterviewDataset,
    cases: list[InterviewCase],
    runner: InterviewCaseRunner,
    *,
    book_ids: dict[str, int],
    answer_judge: InterviewAnswerJudge | None = None,
    on_case: Callable[[str], None] | None = None,
    on_checkpoint: Callable[[dict], None] | None = None,
    initial_rows: list[dict] | None = None,
    case_timeout_seconds: float | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> dict:
    rows: list[dict] = [dict(row) for row in initial_rows or ()]
    errors: list[dict] = []
    judge_errors: list[dict] = []
    completed = {row["case_id"] for row in rows}

    for case in cases:
        if case.id in completed:
            continue
        if on_case:
            on_case(case.id)
        started = perf_counter()
        try:
            with _case_deadline(case_timeout_seconds):
                result, state = runner(case, book_id=book_ids[case.book_key])
                elapsed = perf_counter() - started
                judgment: dict[str, Any] | None = None
                judge_error: str | None = None
                if answer_judge is not None:
                    try:
                        deterministic_citation_validity = _citation_validity(result)
                        judgment = _judge_prediction(
                            answer_judge=answer_judge,
                            dataset_id=dataset.dataset_id,
                            case=case.model_dump(mode="json"),
                            prediction=result.model_dump(mode="json"),
                            deterministic_citation_validity=(
                                deterministic_citation_validity
                            ),
                        )
                    except CaseDeadlineExceeded:
                        raise
                    except Exception as error:  # noqa: BLE001 - preserve generation
                        judge_error = str(error)
                        judge_errors.append({"case_id": case.id, "error": judge_error})
            row = {
                "case_id": case.id,
                "case": case.model_dump(mode="json"),
                "prediction": result.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "checks": score_case(case, result),
                "latency_seconds": elapsed,
                "answer_judgment": judgment,
            }
            if judge_error:
                row["judge_error"] = judge_error
            rows.append(row)
        except Exception as error:  # noqa: BLE001 - one case must not stop the run
            elapsed = perf_counter() - started
            message = str(error)
            errors.append({"case_id": case.id, "error": message})
            checks: dict[str, bool | float] = {
                "route": False,
                "outcome": False,
                "depth": False,
                "archetype": False,
                "citations_valid": False,
                "prompt_version_present": False,
            }
            if case.answerable:
                checks["evidence_recall"] = 0.0
                checks["citations_present"] = False
                checks["avoids_evidence_preface"] = False
            else:
                checks["abstention_has_no_citations"] = False
            rows.append(
                {
                    "case_id": case.id,
                    "case": case.model_dump(mode="json"),
                    "prediction": None,
                    "state": None,
                    "checks": checks,
                    "latency_seconds": elapsed,
                    "answer_judgment": None,
                    "error": message,
                }
            )
        if on_checkpoint:
            on_checkpoint(
                _evaluation_snapshot(
                    dataset=dataset,
                    rows=rows,
                    errors=errors,
                    judge_errors=judge_errors,
                    run_metadata=run_metadata,
                )
            )

    return _evaluation_snapshot(
        dataset=dataset,
        rows=rows,
        errors=errors,
        judge_errors=judge_errors,
        run_metadata=run_metadata,
    )


def evaluated_profile_version(profile: PromptProfile) -> str:
    return profile_version(profile)
