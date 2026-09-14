"""Deterministic quality gates for complete ideal-interview transcripts."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from interviews.contracts import ContractModel


SOURCE_DEPENDENT = re.compile(
    r"\b(?:the|this) (?:book|chapter|source|author|section)\b|according to",
    re.IGNORECASE,
)
MARKER = re.compile(r"\[(?:N\d+:P\d+|S\d+)\]")
INTERVIEW_SIGNALS = re.compile(
    r"\b(?:I(?:'d| would)|let me|we(?:'d| should| can)|assum|trade[ -]?off|"
    r"because|so that|first|if .* then|failure|measure|validate)\b",
    re.IGNORECASE,
)
PHASE_ORDER = {
    "opening": 0,
    "requirements": 1,
    "estimation": 2,
    "architecture": 3,
    "deep_dive": 4,
    "tradeoffs": 5,
    "reliability": 6,
    "evaluation": 7,
    "closing": 8,
}


def score_ideal_flow(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, float]:
    exchanges = outputs.get("exchanges") or []
    expected = list(reference_outputs.get("topic_keys") or [])
    found = [item.get("topic_key") for item in exchanges]
    citations = [citation for item in exchanges for citation in item.get("citations", [])]
    expected_markers = set(reference_outputs.get("citation_markers") or [])
    found_markers = {item.get("marker") for item in citations}
    spoken = " ".join(
        f"{item.get('interviewer_text', '')} {item.get('candidate_text', '')}"
        for item in exchanges
    )
    questions = [item.get("interviewer_text", "") for item in exchanges]
    answers = [item.get("candidate_text", "") for item in exchanges]
    phases = [PHASE_ORDER.get(item.get("phase"), -1) for item in exchanges]
    system_design = reference_outputs.get("interview_format") == "system_design"

    return {
        "exact_topic_coverage": float(found == expected and len(found) == len(set(found))),
        "citation_presence": float(bool(exchanges) and all(item.get("citations") for item in exchanges)),
        "citation_marker_coverage": float(
            bool(expected_markers) and found_markers == expected_markers
        ),
        "citation_locator_validity": float(bool(citations) and all(
            bool(item.get("marker"))
            and ((item.get("node_id") is not None and item.get("page") is not None)
                 != (item.get("evidence_rank") is not None))
            for item in citations
        )),
        "spoken_text_has_no_markers": float(not MARKER.search(spoken)),
        "questions_are_interview_shaped": float(bool(questions) and all(
            value.endswith("?") and 5 <= len(value.split()) <= 40
            and not SOURCE_DEPENDENT.search(value)
            for value in questions
        )),
        "answers_are_speakable": float(bool(answers) and all(
            50 <= len(value.split()) <= 260 for value in answers
        )),
        "human_reasoning_signals": float(bool(answers) and all(
            INTERVIEW_SIGNALS.search(value) for value in answers
        )),
        "system_design_progression": float(
            not system_design
            or (phases == sorted(phases) and "requirements" in [item.get("phase") for item in exchanges])
        ),
    }


def langsmith_evaluators():
    """Return one LangSmith-compatible evaluator per inspectable quality gate."""

    keys = (
        "exact_topic_coverage",
        "citation_presence",
        "citation_marker_coverage",
        "citation_locator_validity",
        "spoken_text_has_no_markers",
        "questions_are_interview_shaped",
        "answers_are_speakable",
        "human_reasoning_signals",
        "system_design_progression",
    )

    def evaluator_for(key: str):
        def evaluate(*, outputs, reference_outputs, **_kwargs):
            return {"key": key, "score": score_ideal_flow(outputs, reference_outputs)[key]}
        evaluate.__name__ = key
        return evaluate

    return [evaluator_for(key) for key in keys]


class IdealFlowJudgment(ContractModel):
    interview_realism: int = Field(ge=0, le=4)
    answer_naturalness: int = Field(ge=0, le=4)
    cross_turn_coherence: int = Field(ge=0, le=4)
    pedagogical_memorability: int = Field(ge=0, le=4)
    grounded_correctness: int = Field(ge=0, le=4)
    explanation: str = Field(max_length=1_200)


def semantic_quality_evaluator():
    """One bounded LLM judge for qualities that deterministic rules cannot see."""

    import os
    from langchain_openai import ChatOpenAI

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required for semantic judging")
    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_JUDGE_MODEL") or "google/gemini-3-flash-preview",
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        max_tokens=1_200,
        max_retries=int(os.getenv("OPENROUTER_JUDGE_MAX_RETRIES", "0")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=0,
    ).with_structured_output(IdealFlowJudgment, method="json_schema")

    def evaluate(*, inputs, outputs, **_kwargs):
        judgment = model.invoke(
            """Judge this ideal technical-interview dialogue from 0 to 4 on each
dimension. Realism means authentic interviewer questions and decision-oriented
candidate behavior, not a study quiz. Naturalness means speakable, concise,
human answers without fake filler. Coherence means later turns continue one
problem and acknowledge earlier decisions. Memorability means clear mental
models and causal explanations. Grounded correctness means every technical
claim is supported by the supplied topic evidence. Do not reward extra facts
that the evidence cannot support. Return a concise explanation.

Inputs and evidence:
"""
            + repr(inputs)
            + "\n\nGenerated dialogue:\n"
            + repr(outputs),
            config={"run_name": "ideal_interview_semantic_judge"},
        )
        dimensions = [
            judgment.interview_realism,
            judgment.answer_naturalness,
            judgment.cross_turn_coherence,
            judgment.pedagogical_memorability,
            judgment.grounded_correctness,
        ]
        return {
            "key": "semantic_quality",
            "score": sum(dimensions) / (4 * len(dimensions)),
            "comment": judgment.model_dump_json(),
        }

    evaluate.__name__ = "semantic_quality"
    return evaluate
