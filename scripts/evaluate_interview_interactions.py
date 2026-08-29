"""Run transcript-derived adaptive interview simulations."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from evals.interview_interaction import (
    evaluate_interview_interactions,
    load_interview_interaction_dataset,
    serialize_interaction_evaluation,
)
from evals.judge import OpenRouterInterviewInteractionJudge
from interviews.contracts import InterviewQuestion
from interviews.models import structured_model


DEFAULT_DATASET = Path("evaluation/interview_transcript_eval.json")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--split",
        choices=("development", "held_out", "all"),
        required=True,
    )
    parser.add_argument("--case", action="append")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--request-timeout-seconds", type=float, default=45)
    args = parser.parse_args()

    dataset = load_interview_interaction_dataset(args.dataset)
    selected = [
        case
        for case in dataset.cases
        if args.split == "all" or case.split == args.split
    ]
    if args.case:
        wanted = set(args.case)
        selected = [case for case in selected if case.id in wanted]
        found = {case.id for case in selected}
        if found != wanted:
            raise SystemExit("Unknown selection: " + ", ".join(sorted(wanted - found)))
    if not selected:
        raise SystemExit("No cases matched the requested split.")

    os.environ["OPENROUTER_INTERVIEW_TIMEOUT_SECONDS"] = str(
        args.request_timeout_seconds
    )
    os.environ["OPENROUTER_INTERVIEW_MAX_RETRIES"] = "0"
    os.environ["OPENROUTER_JUDGE_MAX_RETRIES"] = "0"
    evaluation = evaluate_interview_interactions(
        dataset,
        selected,
        question_model=structured_model(InterviewQuestion),
        judge=OpenRouterInterviewInteractionJudge() if args.judge else None,
    )
    output = args.output or (
        Path("evaluation/runs/interview-interactions")
        / f"{args.split}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        serialize_interaction_evaluation(evaluation),
        encoding="utf-8",
    )
    print(
        serialize_interaction_evaluation(
            {"output": str(output), **evaluation["summary"]}
        )
    )
    raise SystemExit(0 if evaluation["summary"]["pass_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
