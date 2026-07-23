"""Optional semantic answer judge; never used as a safety gate."""

import os

from pydantic import Field

from study.contracts import ContractModel


DEFAULT_JUDGE_MODEL = "google/gemini-3-flash-preview"


class AnswerQualityJudgment(ContractModel):
    correctness: int = Field(ge=0, le=4)
    coverage: int = Field(ge=0, le=4)
    usefulness: int = Field(ge=0, le=4)
    unsupported_claims: list[str]
    explanation: str


class OpenRouterAnswerJudge:
    def __init__(self):
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY is required for live judging")
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=os.getenv("OPENROUTER_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL,
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=1,
            reasoning={
                "effort": os.getenv(
                    "OPENROUTER_JUDGE_REASONING",
                    "high",
                ),
                "exclude": True,
            },
        )
        self.model = model.with_structured_output(
            AnswerQualityJudgment, method="json_schema"
        )

    def evaluate(self, **values):
        prompt = (
            "Score the candidate response against the semantic reference. "
            "Use 0–4 for correctness, coverage, and study usefulness. "
            "For an unanswerable request, clarification or abstention is the "
            "target. List unsupported claims. Judge meaning, not wording.\n\n"
            f"Turn: {values['turn_id']}\n"
            f"Question: {values['question']}\n"
            f"Answerable: {values['answerable']}\n"
            f"Reference: {values['reference_answer']}\n"
            f"Candidate: {values['candidate_answer']}"
        )
        return self.model.invoke(
            prompt,
            config={
                "run_name": "conversation_answer_judge",
                "metadata": {"turn_id": values["turn_id"]},
            },
        )
