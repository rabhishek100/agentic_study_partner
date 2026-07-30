"""Render or rerender a saved interview-answer evaluation report."""

import argparse
import json
from pathlib import Path

from evals.interview import normalize_interview_evaluation
from evals.interview_report import render_interview_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    evaluation = normalize_interview_evaluation(
        json.loads(args.results.read_text(encoding="utf-8"))
    )
    args.results.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    output = args.output or args.results.with_name("report.html")
    report = render_interview_report(evaluation, output)
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
