"""Evaluate conversational turn analysis without retrieval or generation."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from evals.judge import OpenRouterQueryMeaningJudge
from evals.turn_analysis import ProjectTurnAnalyzer, evaluate_turn_analysis
from evals.turn_analysis_report import build_turn_analysis_html


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Batch 2 turn analysis against gold state."
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
        "--output-root",
        type=Path,
        default=Path("evaluation/runs"),
    )
    parser.add_argument("--run-id")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--conversation",
        action="append",
        help="Conversation ID; repeat to build a targeted set.",
    )
    selection.add_argument(
        "--turn",
        action="append",
        help="Turn ID; repeat to select specific analysis decisions.",
    )
    selection.add_argument("--limit", type=int)
    selection.add_argument("--all", action="store_true")
    return parser


def validate_live_environment() -> dict:
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
        "control_model": os.getenv(
            "OPENROUTER_CONTROL_MODEL",
            "x-ai/grok-4.5",
        ),
        "control_reasoning": os.getenv(
            "OPENROUTER_CONTROL_REASONING",
            "high",
        ),
    }


def select_turns(
    gold: dict,
    args: argparse.Namespace,
) -> tuple[set[str] | None, set[str] | None, dict]:
    conversations = {
        conversation["id"]: conversation
        for conversation in gold["conversations"]
    }
    turn_to_conversation = {
        turn["turn_id"]: conversation["id"]
        for conversation in gold["conversations"]
        for turn in conversation["turns"]
    }
    all_turn_ids = list(turn_to_conversation)
    if args.all:
        return None, None, {
            "mode": "all",
            "turn_count": len(all_turn_ids),
        }
    if args.conversation:
        unknown = sorted(set(args.conversation) - conversations.keys())
        if unknown:
            raise ValueError(f"unknown conversation IDs: {unknown}")
        selected_conversations = set(args.conversation)
        return None, selected_conversations, {
            "mode": "conversation",
            "conversation_ids": args.conversation,
        }
    if args.turn:
        unknown = sorted(set(args.turn) - turn_to_conversation.keys())
        if unknown:
            raise ValueError(f"unknown turn IDs: {unknown}")
        selected_turns = set(args.turn)
        selected_conversations = {
            turn_to_conversation[turn_id] for turn_id in selected_turns
        }
        return selected_turns, selected_conversations, {
            "mode": "turn",
            "turn_ids": args.turn,
        }
    if args.limit is None or args.limit <= 0:
        raise ValueError("--limit must be positive")
    selected_turns = set(all_turn_ids[: args.limit])
    selected_conversations = {
        turn_to_conversation[turn_id] for turn_id in selected_turns
    }
    return selected_turns, selected_conversations, {
        "mode": "limit",
        "limit": args.limit,
    }


def execution_plan(
    gold: dict,
    *,
    scored_turn_ids: set[str] | None,
    conversation_ids: set[str] | None,
) -> dict:
    selected = [
        turn
        for conversation in gold["conversations"]
        if (
            conversation_ids is None
            or conversation["id"] in conversation_ids
        )
        for turn in conversation["turns"]
        if (
            scored_turn_ids is None
            or turn["turn_id"] in scored_turn_ids
        )
    ]
    semantic_candidates = sum(
        turn["expected_standalone_query"] is not None
        for turn in selected
    )
    return {
        "scored_turn_count": len(selected),
        "analysis_calls_upper_bound": len(selected),
        "semantic_judge_calls_upper_bound": semantic_candidates,
        "openrouter_calls_upper_bound": (
            len(selected) + semantic_candidates
        ),
        "note": (
            "Deterministic hierarchy analysis and exact query matches "
            "reduce the actual call count."
        ),
    }


def print_preflight(
    *,
    run_id: str,
    output_directory: Path,
    environment: dict,
    plan: dict,
) -> None:
    print("Turn-analysis evaluation preflight")
    print(f"  Run: {run_id}")
    print(f"  Output: {output_directory}")
    print(f"  Turns: {plan['scored_turn_count']} isolated decisions")
    print(
        "  Control: "
        f"{environment['control_model']} "
        f"(reasoning={environment['control_reasoning']})"
    )
    print(
        "  Estimated calls: at most "
        f"{plan['analysis_calls_upper_bound']} analysis + "
        f"{plan['semantic_judge_calls_upper_bound']} semantic judge = "
        f"{plan['openrouter_calls_upper_bound']} OpenRouter calls"
    )
    print(f"  Note: {plan['note']}")


def progress_reporter(total: int):
    completed = 0

    def report(turn: dict) -> None:
        nonlocal completed
        completed += 1
        prediction = turn["prediction"]
        route = prediction["route"] if prediction else "analysis_error"
        status = (
            "pass"
            if turn["judgment"]["all_components_correct"]
            else "inspect"
        )
        print(
            f"[{completed}/{total}] {turn['turn_id']} "
            f"route={route} result={status}",
            flush=True,
        )

    return report


def trace_runner(project_name: str):
    from langsmith import Client, trace

    client = Client()

    def run(turn: dict, state, operation):
        with trace(
            "turn_analysis_component",
            run_type="chain",
            inputs={
                "turn_id": turn["turn_id"],
                "question": turn["user"],
                "conversation_id": state.conversation_id,
                "active_scope": (
                    state.active_scope.model_dump(mode="json")
                    if state.active_scope
                    else None
                ),
                "pending_clarification": state.pending_clarification,
            },
            project_name=project_name,
            tags=["turn-analysis-eval", "batch-2c"],
            metadata={
                "gold_set": "designing-ml-systems-multiturn-v1",
                "turn_id": turn["turn_id"],
            },
        ) as run_tree:
            analysis, query_meaning = operation()
            run_tree.end(
                outputs={
                    "route": analysis.route,
                    "history_dependency": analysis.history_dependency,
                    "standalone_query": analysis.standalone_query,
                    "resolved_scope_node_id": (
                        analysis.resolved_scope.node_id
                        if analysis.resolved_scope
                        else None
                    ),
                    "query_meaning_preserved": query_meaning.get(
                        "preserves_meaning"
                    ),
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
        return (analysis, query_meaning), trace_data

    return run


def main() -> None:
    args = build_argument_parser().parse_args()
    load_dotenv()
    environment = validate_live_environment()
    gold = json.loads(args.gold_set.read_text(encoding="utf-8"))
    scored_turns, conversations, selection = select_turns(gold, args)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "turn-analysis-%Y%m%dT%H%M%SZ"
    )
    output_directory = args.output_root / run_id
    if output_directory.exists():
        raise ValueError(f"run directory already exists: {output_directory}")
    plan = execution_plan(
        gold,
        scored_turn_ids=scored_turns,
        conversation_ids=conversations,
    )
    print_preflight(
        run_id=run_id,
        output_directory=output_directory,
        environment=environment,
        plan=plan,
    )

    evaluation = evaluate_turn_analysis(
        gold,
        analyzer=ProjectTurnAnalyzer(
            source_path=args.source_database,
        ),
        source_path=args.source_database,
        scored_turn_ids=scored_turns,
        conversation_ids=conversations,
        query_judge=OpenRouterQueryMeaningJudge(),
        trace_runner=trace_runner(environment["project"]),
        on_turn_complete=progress_reporter(
            plan["scored_turn_count"]
        ),
    )
    evaluation["run"] = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system": "batch_2_turn_analysis",
        "selection": selection,
        "execution_plan": plan,
        "model": environment["control_model"],
        "reasoning": environment["control_reasoning"],
        "langsmith_project": environment["project"],
    }
    output_directory.mkdir(parents=True)
    results_path = output_directory / "results.json"
    report_path = output_directory / "report.html"
    results_path.write_text(
        json.dumps(evaluation, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        build_turn_analysis_html(evaluation),
        encoding="utf-8",
    )
    print(json.dumps(evaluation["summary"], indent=2))
    print(f"Wrote {results_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
