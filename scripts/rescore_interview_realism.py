"""Re-score saved interview-realism generations without hosted calls."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from evals.interview_realism import (
    load_interview_realism_dataset,
    judge_saved_interview_realism,
    rescore_interview_realism,
    serialize_evaluation,
)
from evals.judge import OpenRouterInterviewSequenceJudge


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/interview_realism_seed.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--case",
        action="append",
        help="Restrict re-scoring or judging to one saved case; repeat as needed.",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run the semantic judge over saved questions without regenerating them.",
    )
    args = parser.parse_args()
    dataset = load_interview_realism_dataset(args.dataset)
    saved = json.loads(args.input.read_text(encoding="utf-8"))
    if saved.get("dataset_id") != dataset.dataset_id:
        raise SystemExit("saved evaluation does not match the dataset")
    if args.case:
        wanted = set(args.case)
        available = {row.get("case_id") for row in saved.get("cases", [])}
        missing = wanted - available
        if missing:
            raise SystemExit("Unknown saved case: " + ", ".join(sorted(missing)))
        saved = {
            **saved,
            "cases": [
                row for row in saved.get("cases", []) if row.get("case_id") in wanted
            ],
        }
    rescored = rescore_interview_realism(dataset, saved)
    if args.judge:
        os.environ["OPENROUTER_JUDGE_MAX_RETRIES"] = "0"
        rescored = judge_saved_interview_realism(
            dataset,
            saved,
            judge=OpenRouterInterviewSequenceJudge(),
        )
    output = args.output or args.input.with_name(f"{args.input.stem}-rescored.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_evaluation(rescored), encoding="utf-8")
    print(serialize_evaluation({"output": str(output), **rescored["summary"]}))
    raise SystemExit(0 if rescored["summary"]["pass_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
