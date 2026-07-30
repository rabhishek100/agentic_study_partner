"""Run interview-answer cases through the real grounded study coordinator."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from evals.interview import (
    ProjectInterviewRunner,
    evaluate_interview_cases,
    evaluated_profile_version,
)
from evals.interview_dataset import load_interview_dataset
from evals.interview_report import render_interview_report
from evals.judge import OpenRouterInterviewJudge
from storage.database import (
    connection as database_connection,
)
from storage.database import (
    environment_owner_id,
    parse_owner_id,
)
from study.contracts import PromptProfile
from study.prompts import DEFAULT_PROMPT_PROFILE

DEFAULT_DATASET = Path("evaluation/interview_answer_seed.json")
SMOKE_CASES = ("int-001", "int-011", "int-030")


def _arguments():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--smoke", action="store_true")
    selection.add_argument("--case", action="append")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--resume",
        type=Path,
        help="Reuse successful cases from an earlier results or checkpoint file.",
    )
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL")
    parser.add_argument(
        "--retrieval-mode",
        choices=("bm25", "vector", "hybrid", "hybrid_rerank"),
        default="hybrid_rerank",
    )
    parser.add_argument("--owner-id", help="Owner UUID; defaults to DEFAULT_OWNER_ID")
    parser.add_argument(
        "--prompt-profile",
        type=Path,
        help="Raw PromptProfile JSON or an API response containing a profile field.",
    )
    parser.add_argument(
        "--judge-answers",
        action="store_true",
        help="Add rubric judge calls after generation (extra calls and cost).",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=90,
        help="Per hosted model request; evaluation disables generation/judge retries.",
    )
    parser.add_argument(
        "--case-timeout-seconds",
        type=float,
        default=240,
        help="Hard total deadline across setup, answer, repairs, and judge calls.",
    )
    return parser.parse_args()


def _select(dataset, args):
    if args.all:
        return list(dataset.cases)
    wanted = set(SMOKE_CASES if args.smoke else args.case or ())
    selected = [case for case in dataset.cases if case.id in wanted]
    found = {case.id for case in selected}
    if found != wanted:
        raise SystemExit("Unknown selection: " + ", ".join(sorted(wanted - found)))
    return selected


def _profile(path: Path | None) -> PromptProfile:
    if path is None:
        return DEFAULT_PROMPT_PROFILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PromptProfile.model_validate(payload.get("profile", payload))


def _resolve_book_ids(dataset, selected, *, database_url, owner_id):
    wanted = {case.book_key for case in selected}
    books = {book.key: book for book in dataset.books if book.key in wanted}
    resolved = {}
    with database_connection(database_url, readonly=True) as connection:
        for key, book in books.items():
            rows = connection.execute(
                """
                select id
                from books
                where owner_id = %s and file_hash = %s and status = 'ready'
                order by id
                """,
                (owner_id, book.source_file_sha256),
            ).fetchall()
            hinted = [row for row in rows if row["id"] == book.production_book_id_hint]
            if len(hinted) == 1:
                resolved[key] = hinted[0]["id"]
            elif len(rows) == 1:
                resolved[key] = rows[0]["id"]
            else:
                raise SystemExit(
                    f"Could not uniquely resolve ready book {key!r} for this owner"
                )
    return resolved


def main():
    load_dotenv()
    args = _arguments()
    if args.request_timeout_seconds <= 0:
        raise SystemExit("--request-timeout-seconds must be positive")
    if args.case_timeout_seconds <= 0:
        raise SystemExit("--case-timeout-seconds must be positive")
    os.environ["OPENROUTER_REQUEST_TIMEOUT_SECONDS"] = str(args.request_timeout_seconds)
    os.environ["OPENROUTER_GENERATION_MAX_RETRIES"] = "0"
    os.environ["OPENROUTER_JUDGE_MAX_RETRIES"] = "0"
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    dataset = load_interview_dataset(args.dataset)
    selected = _select(dataset, args)
    profile = _profile(args.prompt_profile)
    book_ids = _resolve_book_ids(
        dataset,
        selected,
        database_url=args.database_url,
        owner_id=owner_id,
    )
    output = args.output or Path("evaluation/runs/interview") / datetime.now(
        UTC
    ).strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "checkpoint.json"
    initial_rows = []
    if args.resume:
        prior = json.loads(args.resume.read_text(encoding="utf-8"))
        if prior.get("dataset_id") != dataset.dataset_id:
            raise SystemExit("--resume dataset does not match the selected dataset")
        initial_rows = [
            row
            for row in prior.get("cases", [])
            if row.get("prediction") is not None
            and row["case_id"] in {case.id for case in selected}
        ]
    run_metadata = {
        "created_at": datetime.now(UTC).isoformat(),
        "retrieval_mode": args.retrieval_mode,
        "prompt_profile_version": evaluated_profile_version(profile),
        "judge_enabled": args.judge_answers,
        "generation_model": os.getenv("OPENROUTER_GENERATION_MODEL"),
        "control_model": os.getenv("OPENROUTER_CONTROL_MODEL"),
        "judge_model": (
            os.getenv("OPENROUTER_JUDGE_MODEL") if args.judge_answers else None
        ),
        "case_ids": [case.id for case in selected],
        "request_timeout_seconds": args.request_timeout_seconds,
        "case_timeout_seconds": args.case_timeout_seconds,
        "resumed_from": str(args.resume) if args.resume else None,
    }
    evaluation = evaluate_interview_cases(
        dataset,
        selected,
        ProjectInterviewRunner(
            owner_id=owner_id,
            database_url=args.database_url,
            retrieval_mode=args.retrieval_mode,
            prompt_profile=profile,
        ),
        book_ids=book_ids,
        answer_judge=OpenRouterInterviewJudge() if args.judge_answers else None,
        on_case=lambda case_id: print(f"Running {case_id}...", flush=True),
        on_checkpoint=lambda snapshot: checkpoint.write_text(
            json.dumps(snapshot, indent=2),
            encoding="utf-8",
        ),
        initial_rows=initial_rows,
        case_timeout_seconds=args.case_timeout_seconds,
        run_metadata=run_metadata,
    )
    results = output / "results.json"
    results.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    report = render_interview_report(evaluation, output / "report.html")

    print(json.dumps(evaluation["summary"], indent=2))
    print(f"Results: {results}")
    print(f"Report:  {report}")
    raise SystemExit(1 if evaluation["summary"]["errors"] else 0)


if __name__ == "__main__":
    main()
