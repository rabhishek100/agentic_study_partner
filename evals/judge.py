"""Optional semantic answer judge used only by live evaluations."""

import os
from typing import Protocol

from pydantic import Field

from study.contracts import ContractModel


class AnswerQualityJudgment(ContractModel):
    correctness: int = Field(ge=0, le=4)
    required_point_coverage: int = Field(ge=0, le=4)
    usefulness: int = Field(ge=0, le=4)
    unsupported_claims: list[str]
    explanation: str


class AnswerJudge(Protocol):
    def evaluate(
        self,
        *,
        question: str,
        reference_answer: str,
        candidate_answer: str,
        expected_route: str,
        answerable: bool,
        turn_id: str,
    ) -> AnswerQualityJudgment: ...


class QueryMeaningJudgment(ContractModel):
    preserves_meaning: bool
    missing_concepts: list[str]
    added_assumptions: list[str]
    explanation: str


class QueryMeaningJudge(Protocol):
    def evaluate(
        self,
        *,
        current_message: str,
        expected_query: str,
        candidate_query: str,
        turn_id: str,
    ) -> QueryMeaningJudgment: ...


def _control_chat_model():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for live judging")

    from langchain_openai import ChatOpenAI

    model_name = os.getenv(
        "OPENROUTER_CONTROL_MODEL",
        "x-ai/grok-4.5",
    )
    reasoning_effort = os.getenv(
        "OPENROUTER_CONTROL_REASONING",
        "high",
    )
    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        reasoning={
            "effort": reasoning_effort,
            "exclude": True,
        },
    )
    return model, model_name, reasoning_effort


class OpenRouterQueryMeaningJudge:
    """Diagnostic semantic comparison for standalone retrieval meaning."""

    def __init__(self) -> None:
        model, self.model_name, self.reasoning_effort = (
            _control_chat_model()
        )
        self.structured = model.with_structured_output(
            QueryMeaningJudgment,
            method="json_schema",
        )

    def evaluate(
        self,
        *,
        current_message: str,
        expected_query: str,
        candidate_query: str,
        turn_id: str,
    ) -> QueryMeaningJudgment:
        system = (
            "Compare two standalone search questions by meaning, not exact "
            "wording. The candidate must preserve every important entity, "
            "relationship, constraint, scope, and requested comparison in "
            "the expected query without materially changing the retrieval "
            "target. Allow harmless descriptive redundancy, including "
            "ordinal labels alongside already named referents, when it does "
            "not change what evidence should be retrieved. Do not reject a "
            "candidate merely because it is more explicit. Treat a missing "
            "book, chapter, named referent, relationship, or constraint as "
            "material when that omission could retrieve different evidence. "
            "Do not judge whether either question is factually answerable. "
            "Return preserves_meaning=true only when the candidate is a "
            "faithful search formulation."
        )
        human = f"""
Turn: {turn_id}
Original conversational message:
{current_message}

Expected standalone meaning:
{expected_query}

Candidate standalone query:
{candidate_query}
""".strip()
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                return self.structured.invoke(
                    [("system", system), ("human", human)],
                    config={
                        "run_name": "standalone_query_meaning_judge",
                        "tags": ["turn-analysis-eval", "query-judge"],
                        "metadata": {
                            "turn_id": turn_id,
                            "attempt": attempt,
                        },
                    },
                )
            except Exception as error:
                last_error = error
        raise RuntimeError(
            "control model failed to judge standalone query meaning"
        ) from last_error


class OpenRouterAnswerJudge:
    """Structured Grok rubric judge; its scores are never safety gates."""

    def __init__(self) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for live judging")

        from langchain_openai import ChatOpenAI

        self.model_name = os.getenv(
            "OPENROUTER_CONTROL_MODEL",
            "x-ai/grok-4.5",
        )
        self.reasoning_effort = os.getenv(
            "OPENROUTER_CONTROL_REASONING",
            "high",
        )
        model = ChatOpenAI(
            model=self.model_name,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=2,
            reasoning={
                "effort": self.reasoning_effort,
                "exclude": True,
            },
        )
        self.structured = model.with_structured_output(
            AnswerQualityJudgment,
            method="json_schema",
        )

    def evaluate(
        self,
        *,
        question: str,
        reference_answer: str,
        candidate_answer: str,
        expected_route: str,
        answerable: bool,
        turn_id: str,
    ) -> AnswerQualityJudgment:
        system = (
            "Evaluate a study assistant response against a semantic reference. "
            "Do not require exact wording. Score correctness, coverage of the "
            "reference's required points, and usefulness from 0 to 4. For an "
            "unanswerable request, a correct clarification or evidence-based "
            "abstention is the target. List claims that are unsupported by the "
            "reference; an empty list means none were found. Citations are "
            "evaluated separately, so judge their attached prose rather than "
            "citation formatting."
        )
        human = f"""
Turn: {turn_id}
Expected route: {expected_route}
Expected answerable: {answerable}

Question:
{question}

Semantic reference:
{reference_answer}

Candidate response:
{candidate_answer}
""".strip()
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                return self.structured.invoke(
                    [("system", system), ("human", human)],
                    config={
                        "run_name": "multiturn_answer_judge",
                        "tags": ["multiturn-eval", "answer-judge"],
                        "metadata": {
                            "turn_id": turn_id,
                            "attempt": attempt,
                        },
                    },
                )
            except Exception as error:  # Provider/schema errors are recorded.
                last_error = error
        raise RuntimeError(
            "control model failed to return a valid answer judgment"
        ) from last_error
