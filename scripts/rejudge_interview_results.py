"""Rejudge a saved interview run without repeating generation calls."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from evals.interview import rejudge_interview_evaluation
from evals.interview_report import render_interview_report
from evals.judge import OpenRouterInterviewJudge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_dotenv()

    evaluation = json.loads(args.results.read_text(encoding="utf-8"))
    evaluation = rejudge_interview_evaluation(
        evaluation,
        OpenRouterInterviewJudge(),
    )
    output = args.output or args.results.with_name("results-rejudged.json")
    output.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    report = render_interview_report(
        evaluation,
        output.with_name("report-rejudged.html"),
    )
    print(json.dumps(evaluation["summary"]["judge"], indent=2))
    print(f"Results: {output}")
    print(f"Report:  {report}")
    raise SystemExit(1 if evaluation["summary"]["judge_errors"] else 0)


if __name__ == "__main__":
    main()
