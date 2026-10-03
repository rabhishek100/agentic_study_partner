"""Run one budgeted child under the shared $5 round reservation."""
import argparse
from pathlib import Path
import subprocess
import sys

from evals.round_budget import ALLOCATIONS, RoundBudget


def bounded_arguments(arguments):
    forwarded = arguments[1:] if arguments[:1] == ["--"] else arguments
    for value in forwarded:
        option = value.split("=", 1)[0]
        if option.startswith("--") and any(reserved.startswith(option) for reserved in ("--output", "--max-usd")):
            # Child argparse accepts abbreviations; those must not override caps.
            raise ValueError("Child output and ceiling belong to the round coordinator")
    return forwarded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--phase", choices=ALLOCATIONS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-usd", required=True)
    parser.add_argument("--module", choices=["scripts.run_evaluations", "scripts.judge_saved_evaluations"],
                        default="scripts.run_evaluations")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    runs = Path(__file__).resolve().parents[1] / "evaluation/runs"
    if not args.round.resolve().is_relative_to(runs.resolve()):
        parser.error("--round must remain inside ignored evaluation/runs/")
    try:
        forwarded = bounded_arguments(args.arguments)
    except ValueError as error:
        parser.error(str(error))
    budget = RoundBudget(args.round)
    with budget.lease(args.output, phase=args.phase, cap=args.max_usd) as descriptor:
        command = [sys.executable, "-m", args.module, "--output", str(args.output.resolve()),
                   "--max-usd", args.max_usd, *forwarded]
        child = subprocess.Popen(command, pass_fds=(descriptor,))
        try:
            returncode = child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()
