"""Grade frozen weak/mixed/strong interview answers with the production rubric."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from observability import traced
from evals.interview_candidate_calibration import (
    evaluate_candidate_calibration,
    load_candidate_calibration_dataset,
    serialize_candidate_calibration,
)
from interviews.contracts import AnswerEvaluation
from interviews.models import structured_model


DEFAULT_DATASET = Path("evaluation/interview_candidate_profiles.json")


@traced("scripts.evaluate_interview_candidate_calibration.main", flow="evaluation")
def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--case", action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--request-timeout-seconds", type=float, default=45)
    parser.add_argument("--max-tokens", type=int, default=1400)
    args = parser.parse_args()

    dataset = load_candidate_calibration_dataset(args.dataset)
    selected = dataset.profiles
    if args.case:
        wanted = set(args.case)
        selected = [profile for profile in selected if profile.id in wanted]
        found = {profile.id for profile in selected}
        if found != wanted:
            raise SystemExit("Unknown selection: " + ", ".join(sorted(wanted - found)))
    if not selected:
        raise SystemExit("No candidate profiles selected.")

    os.environ["OPENROUTER_INTERVIEW_TIMEOUT_SECONDS"] = str(
        args.request_timeout_seconds
    )
    os.environ["OPENROUTER_INTERVIEW_MAX_TOKENS"] = str(args.max_tokens)
    os.environ["OPENROUTER_INTERVIEW_MAX_RETRIES"] = "0"
    evaluation = evaluate_candidate_calibration(
        dataset,
        selected,
        model=structured_model(AnswerEvaluation),
    )
    output = args.output or (
        Path("evaluation/runs/interview-candidate-calibration")
        / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_candidate_calibration(evaluation), encoding="utf-8")
    print(serialize_candidate_calibration({"output": str(output), **evaluation["summary"]}))
    raise SystemExit(0 if evaluation["summary"]["passed"] else 1)


if __name__ == "__main__":
    load_dotenv()
    main()
