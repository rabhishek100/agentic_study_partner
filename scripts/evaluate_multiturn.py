"""Run targeted or complete live evaluation of the current one-turn baseline."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from evals.judge import OpenRouterAnswerJudge
from evals.multiturn import (
    CurrentBaselineExecutor,
    GoldQueryRetriever,
    evaluate_conversations,
)
from evals.report import build_html


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay the frozen multi-turn gold set."
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/multiturn_gold.json"),
    )
    parser.add_argument(
        "--source-database",
        type=Path,
        default=Path("data/books.sqlite3"),
    )
    parser.add_argument(
        "--retrieval-database",
        type=Path,
        default=Path("data/retrieval.sqlite3"),
    )
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("evaluation/runs"),
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--retrieval-mode",
        choices=["bm25", "vector", "hybrid", "hybrid_rerank"],
        default="hybrid",
        help="The unchanged baseline defaults to its current hybrid mode.",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--conversation",
        action="append",
        help="Conversation ID; repeat to build a smoke set.",
    )
    selection.add_argument(
        "--turn",
        action="append",
        help="Score a turn after replaying its earlier conversation turns.",
    )
    selection.add_argument("--limit", type=int)
    selection.add_argument("--all", action="store_true")
    return parser


def _validate_live_environment() -> dict:
    missing = [
        name
        for name in ("OPENROUTER_API_KEY", "LANGSMITH_API_KEY")
        if not os.getenv(name)
    ]
    tracing = os.getenv(
        "LANGSMITH_TRACING",
        os.getenv("LANGCHAIN_TRACING_V2", ""),
    ).casefold()
    if tracing not in {"1", "true", "yes", "on"}:
        missing.append("LANGSMITH_TRACING=true")
    project = os.getenv("LANGSMITH_PROJECT")
    if not project:
        missing.append("LANGSMITH_PROJECT")
    if missing:
        raise ValueError(
            "live evaluation configuration is incomplete: "
            + ", ".join(missing)
        )
    return {
        "project": project,
        "generation_model": os.getenv(
            "OPENROUTER_GENERATION_MODEL",
            os.getenv("OPENROUTER_MODEL", "openai/gpt-5.6-luna"),
        ),
        "control_model": os.getenv(
            "OPENROUTER_CONTROL_MODEL",
            "x-ai/grok-4.5",
        ),
        "control_reasoning": os.getenv(
            "OPENROUTER_CONTROL_REASONING",
            "high",
        ),
    }


def _selection(
    gold: dict,
    args: argparse.Namespace,
) -> tuple[set[str] | None, set[str] | None, dict]:
    conversations = {
        conversation["id"]: conversation
        for conversation in gold["conversations"]
    }
    all_turn_ids = [
        turn["turn_id"]
        for conversation in gold["conversations"]
        for turn in conversation["turns"]
    ]
    if args.all:
        return None, None, {"mode": "all", "turn_count": len(all_turn_ids)}
    if args.conversation:
        unknown = sorted(set(args.conversation) - conversations.keys())
        if unknown:
            raise ValueError(f"unknown conversation IDs: {unknown}")
        selected = set(args.conversation)
        return None, selected, {
            "mode": "conversation",
            "conversation_ids": args.conversation,
        }
    if args.turn:
        unknown = sorted(set(args.turn) - set(all_turn_ids))
        if unknown:
            raise ValueError(f"unknown turn IDs: {unknown}")
        conversation_ids = {
            turn_id.rsplit("-t", 1)[0] for turn_id in args.turn
        }
        return set(args.turn), conversation_ids, {
            "mode": "turn",
            "turn_ids": args.turn,
        }
    if args.limit is None or args.limit <= 0:
        raise ValueError("--limit must be positive")
    selected_turns = set(all_turn_ids[: args.limit])
    conversation_ids = {
        turn_id.rsplit("-t", 1)[0] for turn_id in selected_turns
    }
    return selected_turns, conversation_ids, {
        "mode": "limit",
        "limit": args.limit,
    }


def _execution_plan(
    gold: dict,
    *,
    scored_turn_ids: set[str] | None,
    conversation_ids: set[str] | None,
) -> dict:
    replayed: list[dict] = []
    scored: list[dict] = []
    for conversation in gold["conversations"]:
        if (
            conversation_ids is not None
            and conversation["id"] not in conversation_ids
        ):
            continue
        for turn in conversation["turns"]:
            is_scored = (
                scored_turn_ids is None
                or turn["turn_id"] in scored_turn_ids
            )
            if scored_turn_ids is not None and not is_scored:
                later_selected = any(
                    selected.startswith(conversation["id"] + "-t")
                    and int(selected.rsplit("t", 1)[1])
                    > int(turn["turn_id"].rsplit("t", 1)[1])
                    for selected in scored_turn_ids
                )
                if not later_selected:
                    continue
            replayed.append(turn)
            if is_scored:
                scored.append(turn)
    oracle_queries = sum(
        bool(turn["expected_standalone_query"])
        and turn["expected_route"] in {"retrieval_qa", "abstain"}
        for turn in scored
    )
    generation_upper_bound = sum(
        turn["expected_route"] != "hierarchy_list"
        for turn in replayed
    )
    return {
        "replayed_turn_count": len(replayed),
        "scored_turn_count": len(scored),
        "generation_calls_upper_bound": generation_upper_bound,
        "control_judge_calls": len(scored),
        "local_oracle_retrieval_queries": oracle_queries,
        "estimated_openrouter_calls_upper_bound": (
            generation_upper_bound + len(scored)
        ),
    }


def _print_preflight(
    *,
    run_id: str,
    output_directory: Path,
    environment: dict,
    retrieval_mode: str,
    execution_plan: dict,
) -> None:
    print("Multi-turn evaluation preflight")
    print(f"  Run: {run_id}")
    print(f"  Output: {output_directory}")
    print(
        "  Turns: "
        f"{execution_plan['replayed_turn_count']} replayed, "
        f"{execution_plan['scored_turn_count']} scored"
    )
    print(
        "  Models: "
        f"{environment['generation_model']} (generation), "
        f"{environment['control_model']} "
        f"(judge, reasoning={environment['control_reasoning']})"
    )
    print(
        "  Retrieval: "
        f"{retrieval_mode} baseline, hybrid_rerank gold-query oracle"
    )
    print(
        "  Estimated calls: at most "
        f"{execution_plan['generation_calls_upper_bound']} generation + "
        f"{execution_plan['control_judge_calls']} judge = "
        f"{execution_plan['estimated_openrouter_calls_upper_bound']} "
        "OpenRouter calls; "
        f"{execution_plan['local_oracle_retrieval_queries']} local oracle "
        "queries"
    )


def _progress_reporter(total: int):
    completed = 0

    def report(turn: dict) -> None:
        nonlocal completed
        completed += 1
        prediction = turn["prediction"]
        label = "scored" if turn["scored"] else "setup"
        print(
            f"[{completed}/{total}] {turn['turn_id']} ({label}) "
            f"route={prediction['route']} outcome={prediction['outcome']}",
            flush=True,
        )

    return report


def _trace_runner(project_name: str):
    from langsmith import Client, trace

    client = Client()

    def run(executor, turn: dict, state):
        with trace(
            "multiturn_baseline_turn",
            run_type="chain",
            inputs={
                "turn_id": turn["turn_id"],
                "question": turn["user"],
                "conversation_id": state.conversation_id,
            },
            project_name=project_name,
            tags=["multiturn-eval", "current-baseline"],
            metadata={
                "gold_set": "designing-ml-systems-multiturn-v1",
                "turn_id": turn["turn_id"],
            },
        ) as run_tree:
            result = executor.invoke(turn["user"], state=state)
            run_tree.end(
                outputs={
                    "route": result.route,
                    "outcome": result.outcome,
                    "evidence_nodes": [
                        evidence.node_id for evidence in result.evidence
                    ],
                }
            )
        trace_data = {"run_id": str(run_tree.id)}
        try:
            trace_data["url"] = client.get_run_url(
                run=run_tree,
                project_name=project_name,
            )
        except Exception:
            trace_data["url"] = ""
        return result, trace_data

    return run


def main() -> None:
    args = build_argument_parser().parse_args()
    load_dotenv()
    environment = _validate_live_environment()
    gold = json.loads(args.gold_set.read_text(encoding="utf-8"))
    scored_turns, conversations, selection = _selection(gold, args)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "baseline-%Y%m%dT%H%M%SZ"
    )
    output_directory = args.output_root / run_id
    if output_directory.exists():
        raise ValueError(f"run directory already exists: {output_directory}")
    execution_plan = _execution_plan(
        gold,
        scored_turn_ids=scored_turns,
        conversation_ids=conversations,
    )
    _print_preflight(
        run_id=run_id,
        output_directory=output_directory,
        environment=environment,
        retrieval_mode=args.retrieval_mode,
        execution_plan=execution_plan,
    )

    executor = CurrentBaselineExecutor(
        database_path=str(args.retrieval_database),
        source_path=str(args.source_database),
        chroma_path=str(args.chroma_path),
        book_id=gold["book"]["database_book_id"],
        retrieval_mode=args.retrieval_mode,
    )
    evaluation = evaluate_conversations(
        gold,
        executor=executor,
        source_path=args.source_database,
        scored_turn_ids=scored_turns,
        conversation_ids=conversations,
        answer_judge=OpenRouterAnswerJudge(),
        oracle_retriever=GoldQueryRetriever(
            database_path=str(args.retrieval_database),
            source_path=str(args.source_database),
            chroma_path=str(args.chroma_path),
            book_id=gold["book"]["database_book_id"],
        ),
        trace_runner=_trace_runner(environment["project"]),
        on_turn_complete=_progress_reporter(
            execution_plan["replayed_turn_count"]
        ),
    )
    evaluation["run"] = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system": "current_one_turn_baseline",
        "selection": selection,
        "execution_plan": execution_plan,
        "retrieval_mode": args.retrieval_mode,
        "oracle_retrieval_mode": "hybrid_rerank",
        "models": {
            "generation": environment["generation_model"],
            "control": environment["control_model"],
            "control_reasoning": environment["control_reasoning"],
        },
        "langsmith_project": environment["project"],
    }
    output_directory.mkdir(parents=True)
    results_path = output_directory / "results.json"
    report_path = output_directory / "report.html"
    results_path.write_text(
        json.dumps(evaluation, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(build_html(evaluation), encoding="utf-8")
    print(json.dumps(evaluation["summary"], indent=2))
    print(f"Wrote {results_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
