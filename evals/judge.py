"""Optional semantic answer judge; never used as a safety gate."""

import json
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
            max_retries=int(os.getenv("OPENROUTER_JUDGE_MAX_RETRIES", "1")),
            timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
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


class CriterionResult(ContractModel):
    criterion: str
    met: bool
    explanation: str


class InterviewAnswerJudgment(ContractModel):
    grounded_correctness: int = Field(ge=0, le=4)
    interview_readiness: int = Field(ge=0, le=4)
    coverage: int = Field(ge=0, le=4)
    depth_adherence: int = Field(ge=0, le=4)
    clarity_memorability: int = Field(ge=0, le=4)
    follow_up_quality: int = Field(ge=0, le=4)
    citation_quality: int = Field(ge=0, le=4)
    must_cover_results: list[CriterionResult]
    failure_guard_violations: list[str]
    unsupported_claims: list[str]
    explanation: str


class OpenRouterInterviewJudge:
    """Rubric judge for the knowledge-authored interview-answer seed."""

    def __init__(self):
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY is required for live judging")
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=os.getenv("OPENROUTER_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL,
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=int(os.getenv("OPENROUTER_JUDGE_MAX_RETRIES", "1")),
            timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
            reasoning={
                "effort": os.getenv(
                    "OPENROUTER_JUDGE_REASONING",
                    "high",
                ),
                "exclude": True,
            },
        )
        self.model = model.with_structured_output(
            InterviewAnswerJudgment,
            method="json_schema",
        )

    def evaluate(self, **values):
        case = values["case"]
        rubric = {
            "answerable": case["answerable"],
            "expected_depth": case["expected_depth"],
            "expected_archetype": case["expected_archetype"],
            "must_cover": case["must_cover"],
            "should_cover": case["should_cover"],
            "must_avoid": case["must_avoid"],
            "expected_follow_ups": case["expected_follow_ups"],
            "scoring_focus": case["scoring_focus"],
        }
        grounding = {
            "evidence": values.get("evidence", []),
            "citations": values.get("citations", []),
            "deterministic_citation_validity": values.get(
                "deterministic_citation_validity"
            ),
        }
        prompt = (
            "Evaluate an interview-preparation answer against the supplied "
            "case rubric. Score every dimension from 0 to 4, but focus the "
            "explanation on scoring_focus. Judge semantic coverage rather "
            "than matching wording. Treat must_cover as the reference answer "
            "contract and must_avoid as failure guards. Do not assume that a "
            "claim is supported merely because it is generally plausible. "
            "The answer was generated from retrieved book evidence. Evidence "
            "entries below map citation markers to book paths, pages, and "
            "available excerpts. Do not penalize the candidate for referring "
            "to the book, source, chapters, or evidence when those references "
            "are supported by this grounding payload. Deterministic citation "
            "validity has already checked marker-to-evidence mappings; use it "
            "as a hard signal while still judging whether citations are "
            "placed naturally and sufficiently. "
            "For an unanswerable case, a concise grounded abstention is the "
            "correct response. Verify every must-cover criterion separately. "
            "List concrete unsupported claims and guard violations; use empty "
            "lists when none are present.\n\n"
            f"Case ID: {case['id']}\n"
            f"Question: {case['prompt']}\n"
            f"Rubric: {json.dumps(rubric, ensure_ascii=False)}\n\n"
            f"Grounding payload: {json.dumps(grounding, ensure_ascii=False)}\n\n"
            f"Candidate answer:\n{values['candidate_answer']}"
        )
        return self.model.invoke(
            prompt,
            config={
                "run_name": "interview_answer_judge",
                "metadata": {
                    "case_id": case["id"],
                    "dataset_id": values["dataset_id"],
                },
            },
        )
