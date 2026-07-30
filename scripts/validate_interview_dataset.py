"""Validate and summarize the interview-answer seed dataset."""

import argparse
import json
from pathlib import Path

from evals.interview_dataset import (
    coverage_summary,
    load_interview_dataset,
    validate_canonical_anchors,
)
from storage.database import connection as database_connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/interview_answer_seed.json"),
    )
    parser.add_argument(
        "--database-url",
        help="Optionally verify source hashes, nodes, paths, and pages in Postgres.",
    )
    args = parser.parse_args()

    dataset = load_interview_dataset(args.dataset)
    anchor_errors: list[str] = []
    if args.database_url:
        with database_connection(args.database_url, readonly=True) as connection:
            anchor_errors = validate_canonical_anchors(dataset, connection)
    print(
        json.dumps(
            {
                "valid": not anchor_errors,
                "dataset_id": dataset.dataset_id,
                "review_status": dataset.review.status,
                "canonical_anchor_errors": anchor_errors,
                **coverage_summary(dataset),
            },
            indent=2,
            sort_keys=True,
        )
    )
    raise SystemExit(1 if anchor_errors else 0)


if __name__ == "__main__":
    main()
