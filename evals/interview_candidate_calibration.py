"""Calibrate the production answer grader on frozen human-written answers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from decks.topics import ScopeInventory, Topic
from interviews.contracts import (
    AnswerEvaluation,
    ContractModel,
    InterviewQuestion,
)
from interviews.evaluation import sanitize_evaluation, validate_question
from interviews.models import invoke_structured
from interviews.prompts import build_evaluation_messages


CandidateLevel = Literal["weak", "mixed", "strong"]
CalibrationRoute = Literal["clarifying", "depth_follow_up", "advance", "continue"]
LEVEL_ORDER = ("weak", "mixed", "strong")


class CandidateCalibrationScenario(ContractModel):
    id: str
    source_title: str
    scope_title: str
    topic_key: str
    topic_label: str
    evidence: str
    allowed_markers: list[str] = Field(min_length=1)
    question: InterviewQuestion

    @model_validator(mode="after")
    def valid_question(self) -> "CandidateCalibrationScenario":
        if self.question.topic_key != self.topic_key:
            raise ValueError("scenario question must use its topic")
        validate_question(self.question, self.topic())
        return self

    def topic(self) -> Topic:
        return Topic(
            key=self.topic_key,
            ordinal=0,
            label=self.topic_label,
            required=True,
            evidence_text=self.evidence,
            allowed_markers=frozenset(self.allowed_markers),
        )

    def inventory(self) -> ScopeInventory:
        return ScopeInventory(
            source_kind="book",
            scope_key=f"candidate-calibration:{self.id}",
            title=self.scope_title,
            source_title=self.source_title,
            outline=f"- {self.topic_label}",
            topics=(self.topic(),),
        )


class CandidateCalibrationProfile(ContractModel):
    id: str
    scenario_id: str
    level: CandidateLevel
    answer: str = Field(min_length=1)
    minimum_score: float = Field(ge=1, le=5)
    maximum_score: float = Field(ge=1, le=5)
    question_complete: bool
    allowed_routes: list[CalibrationRoute] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_score_band(self) -> "CandidateCalibrationProfile":
        if self.maximum_score < self.minimum_score:
            raise ValueError("maximum score must be at least minimum score")
        return self


class InterviewCandidateCalibrationDataset(ContractModel):
    dataset_id: str
    version: int = Field(ge=1)
    review_status: str
    methodology: str
    scenarios: list[CandidateCalibrationScenario] = Field(min_length=2)
    profiles: list[CandidateCalibrationProfile] = Field(min_length=6)

    @model_validator(mode="after")
    def complete_ladders(self) -> "InterviewCandidateCalibrationDataset":
        scenarios = {scenario.id for scenario in self.scenarios}
        if len(scenarios) != len(self.scenarios):
            raise ValueError("scenario ids must be unique")
        if len({profile.id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("profile ids must be unique")
        for profile in self.profiles:
            if profile.scenario_id not in scenarios:
                raise ValueError(f"profile {profile.id} references an unknown scenario")
        for scenario_id in scenarios:
            levels = {
                profile.level
                for profile in self.profiles
                if profile.scenario_id == scenario_id
            }
            if levels != set(LEVEL_ORDER):
                raise ValueError(f"scenario {scenario_id} needs weak/mixed/strong")
        return self


def load_candidate_calibration_dataset(
    path: str | Path,
) -> InterviewCandidateCalibrationDataset:
    return InterviewCandidateCalibrationDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def evaluation_route(evaluation: AnswerEvaluation) -> CalibrationRoute:
    if evaluation.needs_clarifying_probe:
        return "clarifying"
    if evaluation.needs_depth_follow_up:
        return "depth_follow_up"
    if evaluation.question_complete:
        return "advance"
    return "continue"


def evaluate_candidate_profile(
    dataset: InterviewCandidateCalibrationDataset,
    scenario: CandidateCalibrationScenario,
    profile: CandidateCalibrationProfile,
    *,
    model: Any,
) -> dict[str, Any]:
    topic = scenario.topic()
    try:
        evaluation, cost = invoke_structured(
            model,
            build_evaluation_messages(
                inventory=scenario.inventory(),
                topic=topic,
                question=scenario.question,
                answer=profile.answer,
                mode="realistic",
                target_level="senior",
                attempts=1,
                hints_used=0,
            ),
            AnswerEvaluation,
        )
        evaluation = sanitize_evaluation(
            evaluation,
            question=scenario.question,
            topic=topic,
        )
    except Exception as error:
        return {
            "profile_id": profile.id,
            "scenario_id": scenario.id,
            "level": profile.level,
            "answer": profile.answer,
            "provider_error": str(error),
            "passed": False,
        }

    score = evaluation.scores.weighted_score
    route = evaluation_route(evaluation)
    checks = {
        "score_in_predeclared_band": (
            profile.minimum_score <= score <= profile.maximum_score
        ),
        "question_completion_matches": (
            evaluation.question_complete == profile.question_complete
        ),
        "route_allowed": route in profile.allowed_routes,
        "citations_resolve": bool(evaluation.citation_markers)
        and set(evaluation.citation_markers) <= set(scenario.allowed_markers),
    }
    return {
        "profile_id": profile.id,
        "scenario_id": scenario.id,
        "level": profile.level,
        "answer": profile.answer,
        "score": score,
        "route": route,
        "classification": evaluation.classification,
        "question_complete": evaluation.question_complete,
        "scores": evaluation.scores.model_dump(mode="json"),
        "checks": checks,
        "evaluation": evaluation.model_dump(mode="json"),
        "cost_usd": cost,
        "passed": all(checks.values()),
    }


def evaluate_candidate_calibration(
    dataset: InterviewCandidateCalibrationDataset,
    profiles: list[CandidateCalibrationProfile],
    *,
    model: Any,
) -> dict[str, Any]:
    scenarios = {scenario.id: scenario for scenario in dataset.scenarios}
    rows = [
        evaluate_candidate_profile(
            dataset,
            scenarios[profile.scenario_id],
            profile,
            model=model,
        )
        for profile in profiles
    ]
    monotonicity: list[dict[str, Any]] = []
    for scenario_id in dict.fromkeys(profile.scenario_id for profile in profiles):
        ladder = {
            row["level"]: row
            for row in rows
            if row["scenario_id"] == scenario_id and "score" in row
        }
        complete = all(level in ladder for level in LEVEL_ORDER)
        if not complete:
            continue
        scores = {level: ladder[level]["score"] for level in LEVEL_ORDER}
        passed = scores["weak"] < scores["mixed"] <= scores["strong"]
        monotonicity.append(
            {
                "scenario_id": scenario_id,
                "scores": scores,
                "check": "weak < mixed <= strong",
                "passed": passed,
            }
        )

    provider_failures = sum("provider_error" in row for row in rows)
    profile_passes = sum(row["passed"] for row in rows)
    monotonic_passes = sum(item["passed"] for item in monotonicity)
    all_passed = (
        bool(rows)
        and profile_passes == len(rows)
        and monotonic_passes == len(monotonicity)
    )
    return {
        "dataset_id": dataset.dataset_id,
        "review_status": dataset.review_status,
        "profiles": rows,
        "monotonicity": monotonicity,
        "summary": {
            "profile_count": len(rows),
            "profile_passes": profile_passes,
            "profile_pass_rate": profile_passes / len(rows) if rows else 0.0,
            "monotonic_ladders": len(monotonicity),
            "monotonic_passes": monotonic_passes,
            "provider_failures": provider_failures,
            "evaluation_cost_usd": round(
                sum(float(row.get("cost_usd", 0)) for row in rows), 6
            ),
            "passed": all_passed,
        },
    }


def serialize_candidate_calibration(evaluation: dict[str, Any]) -> str:
    return json.dumps(evaluation, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
