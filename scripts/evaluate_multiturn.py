"""Run the frozen conversations through the real application coordinator."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from dotenv import load_dotenv

from evals.judge import OpenRouterAnswerJudge
from evals.multiturn import ProjectRunner, evaluate_conversations
from evals.report import render_report


DEFAULT_GOLD = Path("evaluation/multiturn_gold.json")


def _arguments():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--conversation", action="append")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--retrieval-mode", default="hybrid")
    parser.add_argument(
        "--judge-answers",
        action="store_true",
        help="Add an optional LLM quality rubric (extra calls and cost).",
    )
    return parser.parse_args()


def _select(dataset, args):
    conversations = dataset["conversations"]
    if args.all:
        return conversations
    if args.conversation:
        wanted = set(args.conversation)
        selected = [item for item in conversations if item["id"] in wanted]
    found = {conversation["id"] for conversation in selected}
    if found != wanted:
        raise SystemExit("Unknown selection: " + ", ".join(sorted(wanted - found)))
    return selected


def main():
    load_dotenv()
    args = _arguments()
    dataset = json.loads(args.gold.read_text(encoding="utf-8"))
    selected = _select(dataset, args)
    book_id = args.book_id or dataset["book"]["database_book_id"]
    output = args.output or Path("evaluation/runs") / datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )
    output.mkdir(parents=True, exist_ok=True)

    evaluation = evaluate_conversations(
        selected,
        ProjectRunner(retrieval_mode=args.retrieval_mode),
        book_id=book_id,
        answer_judge=OpenRouterAnswerJudge() if args.judge_answers else None,
        on_turn=lambda turn_id: print(f"Running {turn_id}...", flush=True),
    )
    results = output / "results.json"
    results.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    report = render_report(evaluation, output / "report.html")

    print(json.dumps(evaluation["summary"], indent=2))
    print(f"Results: {results}")
    print(f"Report:  {report}")


if __name__ == "__main__":
    main()
