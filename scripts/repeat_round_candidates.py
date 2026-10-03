"""Matched controls and shortlist repeats, serialized by the round budget."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

from evals.budget import atomic_json
from evals.round_budget import RoundBudget
from scripts.screen_model_candidates import candidate_environment, LUNA

SUMMARY_AND_HARD_SHEETS = ["mt-001-t1", "mt-002-t2", "mt-004-t1", "mt-006-t1",
                          "mt-011-t1", "mt-011-t2", "paper-complete-summary",
                          "sheet-attention-paper", "sheet-chapter-6"]
TRIALS = {
    "control": ("09", None, [*SUMMARY_AND_HARD_SHEETS, "mt-008-t1", "mt-003-t4", "mt-005-t2"]),
    "figures-all": ("08", None, ["sheet-attention-paper", "sheet-chapter-1", "sheet-chapter-3",
                                "sheet-chapter-6", "sheet-chapter-8", "sheet-chapter-10"]),
    "summary-a": ("09", "14", SUMMARY_AND_HARD_SHEETS[:7]),
    "summary-b": ("09", "14", SUMMARY_AND_HARD_SHEETS[:7]),
    "figures-b": ("08", None, ["sheet-attention-paper", "sheet-chapter-6"]),
    "pro-hard-a": ("03", None, ["mt-008-t1", "mt-003-t4", "mt-005-t2", "paper-complete-summary"]),
    "pro-hard-b": ("03", None, ["mt-008-t1", "mt-003-t4", "mt-005-t2", "paper-complete-summary"]),
    "control-text": ("09", None, [*SUMMARY_AND_HARD_SHEETS[:7], "mt-008-t1", "mt-003-t4", "mt-005-t2"]),
    "figures-tail": ("08", None, ["sheet-chapter-8", "sheet-chapter-10"]),
}


def remaining(round):
    ledger = round._load()
    used = sum((Decimal(v["committed_usd"]) for v in ledger["runs"].values()
                if v["phase"] == "repeat"), Decimal(0))
    return min(Decimal("2"), round.phase_limits(ledger)["repeat"] - used)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, default=Path("evaluation/runs/round3"))
    parser.add_argument("--trial", choices=TRIALS, action="append")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    if args.tag and (not args.tag.replace("-", "").isalnum() or len(args.tag) > 40):
        parser.error("--tag must contain only letters, digits or hyphens, at most 40 characters")
    root = Path(__file__).resolve().parents[1]
    if not args.round.resolve().is_relative_to((root / "evaluation/runs").resolve()):
        parser.error("Round directory must remain inside ignored evaluation/runs/")
    round = RoundBudget(args.round)
    for trial in args.trial or TRIALS:
        role, candidate, cases = TRIALS[trial]
        spec = {"trial": trial, "role_settings": role, "candidate": candidate, "cases": cases}
        if not args.execute:
            print(json.dumps(spec))
            continue
        output = args.round.resolve() / f"repeat-{trial}{'-' + args.tag if args.tag else ''}"
        plan_path = output / "repeat-plan.json"
        if plan_path.exists():
            prior = json.loads(plan_path.read_text())
            if any(prior[k] != value for k, value in spec.items()):
                raise SystemExit("Repeat specification changed; preserve the old attempt and use a new trial")
            cap = prior["child_cap_usd"]
        else:
            cap = str(remaining(round))
            if Decimal(cap) <= 0:
                print("Repeat allocation exhausted; no further request launched", flush=True)
                break
            atomic_json(plan_path, {**spec, "child_cap_usd": cap})
        command = [sys.executable, "-m", "scripts.run_round_experiment", "--round", str(args.round.resolve()),
                   "--phase", "repeat", "--output", str(output), "--max-usd", cap,
                   "--", "--live", "--retrieval-mode", "bm25", "--judge-model", LUNA]
        if candidate:
            command.extend(["--candidate", candidate])
        for case in cases:
            command.extend(["--case", case])
        print(f"Repeat {trial}; guarded child ceiling ${cap}", flush=True)
        result = subprocess.run(command, cwd=root, env=candidate_environment(role))
        print(f"Repeat {trial} exit {result.returncode}; unsuccessful outputs remain recorded", flush=True)
        if round._load().get("blocked") or result.returncode < 0:
            raise SystemExit("Repeat work stopped for a budget/integrity violation or interruption")
        if result.returncode and trial == "control":
            raise SystemExit("Matched control did not complete; inspect it before launching paid candidates")


if __name__ == "__main__":
    main()
