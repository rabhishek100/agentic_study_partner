"""Generate and evaluate role-varied interview question sequences."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from observability import traced
from evals.interview_realism import (
    evaluate_interview_realism,
    load_interview_realism_dataset,
    serialize_evaluation,
)
from evals.judge import OpenRouterInterviewSequenceJudge
from interviews.contracts import InterviewQuestion
from interviews.models import structured_model


DEFAULT_DATASET = Path("evaluation/interview_realism_seed.json")


@traced("scripts.evaluate_interview_realism.main", flow="evaluation")
def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--case", action="append")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--request-timeout-seconds", type=float, default=45)
    args = parser.parse_args()
    if not args.all and not args.case:
        raise SystemExit("Choose --all or at least one --case.")

    dataset = load_interview_realism_dataset(args.dataset)
    wanted = {case.id for case in dataset.cases} if args.all else set(args.case)
    selected = [case for case in dataset.cases if case.id in wanted]
    found = {case.id for case in selected}
    if found != wanted:
        raise SystemExit("Unknown selection: " + ", ".join(sorted(wanted - found)))

    os.environ["OPENROUTER_INTERVIEW_TIMEOUT_SECONDS"] = str(
        args.request_timeout_seconds
    )
    os.environ["OPENROUTER_INTERVIEW_MAX_RETRIES"] = "0"
    os.environ["OPENROUTER_JUDGE_MAX_RETRIES"] = "0"
    evaluation = evaluate_interview_realism(
        dataset,
        selected,
        model=structured_model(InterviewQuestion),
        judge=OpenRouterInterviewSequenceJudge() if args.judge else None,
    )
    output = args.output or (
        Path("evaluation/runs/interview-realism")
        / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_evaluation(evaluation), encoding="utf-8")
    print(serialize_evaluation({"output": str(output), **evaluation["summary"]}))
    raise SystemExit(0 if evaluation["summary"]["pass_rate"] == 1.0 else 1)


if __name__ == "__main__":
    load_dotenv()
    main()
