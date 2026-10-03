"""Independent model-role screens through the shared round coordinator."""
import argparse
from decimal import Decimal
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from evals.budget import atomic_json
from evals.round_budget import ALLOCATIONS


LUNA = "openai/gpt-6-luna"
DEEPSEEK = "deepseek/deepseek-v4-flash"
QWEN = "qwen/qwen3.5-flash-02-23"
GEMINI = "google/gemini-3.1-flash-lite"
TEXT_CASES = ["paper-scaled-attention", "mt-008-t1", "course-spanner-indexed", "paper-complete-summary"]
MODEL_SCREENS = {
    "01": ({"OPENROUTER_GENERATION_MODEL": DEEPSEEK, "OPENROUTER_VIDEO_ANSWER_MODEL": DEEPSEEK},
           {DEEPSEEK: ["streamlake/fp8"]}, TEXT_CASES),
    "02": ({"OPENROUTER_GENERATION_MODEL": QWEN, "OPENROUTER_VIDEO_ANSWER_MODEL": QWEN},
           {QWEN: ["alibaba"]}, TEXT_CASES),
    "03": ({"OPENROUTER_GENERATION_MODEL": "openai/gpt-6-luna-pro"},
           {"openai/gpt-6-luna-pro": ["openai"]}, ["mt-008-t1", "paper-complete-summary"]),
    "04": ({"OPENROUTER_GENERATION_MODEL": GEMINI, "OPENROUTER_VIDEO_ANSWER_MODEL": GEMINI},
           {GEMINI: ["google-ai-studio"]}, TEXT_CASES),
    "05": ({"OPENROUTER_CONTROL_MODEL": DEEPSEEK, "OPENROUTER_CONTROL_REASONING": "none"},
           {DEEPSEEK: ["streamlake/fp8"]}, ["mt-003-t4", "mt-009-t2"]),
    "06": ({"OPENROUTER_INTERVIEW_GRADER_MODEL": QWEN}, {QWEN: ["alibaba"]},
           ["proximity-weak", "proximity-mixed", "proximity-strong", "rag-weak", "rag-mixed", "rag-strong"]),
    "07": ({"OPENROUTER_REVISION_AUTHOR_MODEL": QWEN}, {QWEN: ["alibaba"]}, ["sheet-chapter-6"]),
    "08": ({"OPENROUTER_REVISION_FIGURE_MODEL": GEMINI}, {GEMINI: ["google-ai-studio"]}, ["sheet-attention-paper"]),
}


FLOW_SCREENS = {
    "09": ["mt-008-t1", "mt-008-t2", "mt-005-t2"],
    "10": ["mt-008-t1", "mt-003-t4", "mt-005-t1"],
    "11": ["mt-008-t1", "mt-008-t2", "mt-003-t4"],
    "12": ["mt-005-t2", "mt-008-t1", "paper-scaled-attention"],
    "13": ["course-spanner-indexed", "course-followup", "vc-011-t1"],
    "14": ["paper-complete-summary", "mt-006-t1"],
    "15": ["sheet-chapter-6"],
    "19": ["proximity-weak", "proximity-mixed", "proximity-strong", "rag-weak", "rag-mixed", "rag-strong"],
    "20": ["ideal-notification-system", "ideal-rag-platform"],
}


def candidate_environment(candidate, base=None):
    env = dict(os.environ if base is None else base)
    # Named stages remain fixed across candidates, irrespective of legacy .env.
    env.update({"OPENROUTER_GENERATION_MODEL": LUNA, "OPENROUTER_VIDEO_ANSWER_MODEL": LUNA,
                "OPENROUTER_CONTROL_MODEL": LUNA, "OPENROUTER_CONTROL_REASONING": "low",
                "OPENROUTER_GENERATION_REASONING": "none", "OPENROUTER_INTERVIEW_MODEL": LUNA,
                "OPENROUTER_INTERVIEW_GRADER_MODEL": LUNA, "OPENROUTER_REVISION_MODEL": LUNA,
                "OPENROUTER_REVISION_AUTHOR_MODEL": LUNA, "OPENROUTER_REVISION_INVENTORY_MODEL": LUNA,
                "OPENROUTER_REVISION_FIGURE_MODEL": LUNA, "OPENROUTER_REVISION_JUDGE_MODEL": LUNA,
                "OPENROUTER_PROVIDER_ROUTES": "{}"})
    changes, routes, cases = MODEL_SCREENS.get(candidate, ({}, {}, []))
    env.update(changes)
    env["OPENROUTER_PROVIDER_ROUTES"] = json.dumps(routes, sort_keys=True)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, default=Path("evaluation/runs/round3"))
    parser.add_argument("--candidate", choices=[*MODEL_SCREENS, *FLOW_SCREENS], action="append")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--tag", default="", help="New immutable attempt suffix, e.g. json-fix")
    args = parser.parse_args()
    if args.tag and not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.tag):
        parser.error("Tag must be a short lowercase attempt name")
    root = Path(__file__).resolve().parents[1]
    if not args.round.resolve().is_relative_to((root / "evaluation/runs").resolve()):
        parser.error("Round directory must remain inside ignored evaluation/runs/")
    for candidate in args.candidate or MODEL_SCREENS:
        kind = "model" if candidate in MODEL_SCREENS else "flow"
        output = args.round.resolve() / (f"{kind}-{candidate}" + (f"-{args.tag}" if args.tag else ""))
        changes, routes, cases = MODEL_SCREENS.get(candidate, ({}, {}, FLOW_SCREENS.get(candidate)))
        if not args.execute:
            print(json.dumps({"candidate": candidate, "changes": changes, "routes": routes, "cases": cases}))
            continue
        plan_path = output / "screen-plan.json"
        if plan_path.exists():
            cap = json.loads(plan_path.read_text())["child_cap_usd"]
        else:
            ledger_path = args.round.resolve() / "round-budget.json"
            spent = Decimal(0)
            if ledger_path.exists():
                ledger = json.loads(ledger_path.read_text())
                spent = sum((Decimal(v["committed_usd"]) for v in ledger["runs"].values()
                             if v["phase"] == "screening"), Decimal(0))
            cap = str(Decimal(ALLOCATIONS["screening"]) - spent)
            if Decimal(cap) <= 0:
                print("Screening allocation exhausted; remaining candidates not launched", flush=True)
                break
            atomic_json(plan_path, {"candidate": candidate, "child_cap_usd": cap,
                                    "changes": changes, "routes": routes, "cases": cases})
        command = [sys.executable, "-m", "scripts.run_round_experiment", "--round", str(args.round.resolve()),
                   "--phase", "screening", "--output", str(output), "--max-usd", cap,
                   "--", "--live", "--retrieval-mode", "hybrid" if candidate == "12" else "bm25", "--judge-model", LUNA]
        if candidate in FLOW_SCREENS and candidate != "12":
            command.extend(["--candidate", candidate])
        for case in cases:
            command.extend(["--case", case])
        print(f"Screening {kind} candidate {candidate}; guarded child ceiling ${cap}", flush=True)
        result = subprocess.run(command, env=candidate_environment(candidate), cwd=root)
        print(f"Candidate {candidate} command exit {result.returncode}; saved failures remain visible", flush=True)
        ledger_path = args.round.resolve() / "round-budget.json"
        if ledger_path.exists() and json.loads(ledger_path.read_text()).get("blocked"):
            raise SystemExit("Round blocked by a pricing/integrity violation; further candidates not launched")
        if result.returncode < 0:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
