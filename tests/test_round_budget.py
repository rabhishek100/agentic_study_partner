"""Round coordination checks: no paid requests or application database writes."""
from decimal import Decimal
import json
import subprocess
import sys

import pytest

from evals.budget import Budget, BudgetStop
from evals.round_budget import RoundBudget
from tests.test_eval_budget import PRICES
from scripts.run_round_experiment import bounded_arguments


def spend(round_budget, name, *, phase="screening", cap="0.50", amount="0.10", receipt=True):
    directory = round_budget.directory / name
    with round_budget.lease(directory, phase=phase, cap=cap):
        child = Budget(directory, cap=cap, prices=PRICES)
        call = child.reserve(amount, model="fixture/model")
        if receipt:
            child.settle(call, {"cost": str(amount)})
    return directory


def ledger(round_budget):
    return json.loads(round_budget.path.read_text())


def test_only_settled_unused_child_capacity_is_released(tmp_path):
    parent = RoundBudget(tmp_path)
    spend(parent, "a", cap="1.25", amount="0.10")
    spend(parent, "b", cap="1.15", amount="0.05")
    assert sum(Decimal(e["committed_usd"]) for e in ledger(parent)["runs"].values()) == Decimal("0.15")
    assert ledger(parent)["runs"]["a"]["reported_usd"] == "0.10"


def test_phase_reservation_protects_other_phases(tmp_path):
    parent = RoundBudget(tmp_path)
    spend(parent, "a", amount="0.50")
    with pytest.raises(BudgetStop, match="allocation"):
        with parent.lease(tmp_path / "b", phase="screening", cap="0.76"):
            pytest.fail("Must not launch an over-allocation child")
    spend(parent, "production", phase="production", cap="0.75", amount="0.05")


def test_unknown_receipts_remain_charged_on_resume(tmp_path):
    parent = RoundBudget(tmp_path)
    directory = spend(parent, "a", cap="1.25", amount="1.00", receipt=False)
    row = ledger(parent)["runs"]["a"]
    assert row["committed_usd"] == "1.00" and row["unknown_reservations"] == 1
    with pytest.raises(BudgetStop):
        with RoundBudget(tmp_path).lease(tmp_path / "b", phase="screening", cap="0.26"):
            pytest.fail("Unknown spend cannot become free")
    Budget(directory, cap="1.25").settle(Budget(directory, cap="1.25").data["calls"][0]["id"], {"cost": "0.10"})
    spend(parent, "b", cap="1.15", amount="0.01")


def test_crash_before_child_ledger_holds_full_allocation(tmp_path):
    parent = RoundBudget(tmp_path)
    with pytest.raises(RuntimeError):
        with parent.lease(tmp_path / "a", phase="screening", cap="1.25"):
            raise RuntimeError("Simulated interruption before child budget exists")
    assert ledger(parent)["runs"]["a"]["committed_usd"] == "1.25"
    with pytest.raises(BudgetStop):
        with parent.lease(tmp_path / "b", phase="screening", cap="0.01"):
            pytest.fail("Missing ledger is not proof of zero spend")
    # Same registered child can resume and establish its empty guarded ledger.
    with parent.lease(tmp_path / "a", phase="screening", cap="1.25"):
        Budget(tmp_path / "a", cap="1.25", prices=PRICES)
    assert ledger(parent)["runs"]["a"]["committed_usd"] == "0"


def test_process_lock_is_inherited_by_child(tmp_path):
    parent = RoundBudget(tmp_path)
    with parent.lease(tmp_path / "a", phase="screening", cap="0.50") as descriptor:
        code = """
import fcntl, sys
with open(sys.argv[1], 'a') as lock:
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit(0)
    raise SystemExit(2)
"""
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path / ".round.lock")],
                                pass_fds=(descriptor,))
        assert result.returncode == 0
        with pytest.raises(BudgetStop, match="still running"):
            with RoundBudget(tmp_path).lease(tmp_path / "b", phase="repeat", cap="0.50"):
                pytest.fail("Parallel child cannot start")


def test_resume_preserves_phase_and_ceiling(tmp_path):
    parent = RoundBudget(tmp_path)
    spend(parent, "a")
    for phase, cap in [("repeat", "0.50"), ("screening", "0.60")]:
        with pytest.raises(BudgetStop, match="preserve child"):
            with parent.lease(tmp_path / "a", phase=phase, cap=cap):
                pass
    with pytest.raises(BudgetStop, match="preserve round"):
        with RoundBudget(tmp_path, allocations={"screening": "5"}).lease(
                tmp_path / "b", phase="screening", cap="0.50"):
            pass


def test_child_price_violation_blocks_whole_round(tmp_path):
    parent = RoundBudget(tmp_path)
    with pytest.raises(BudgetStop):
        with parent.lease(tmp_path / "a", phase="screening", cap="0.50"):
            child = Budget(tmp_path / "a", cap="0.50", prices=PRICES)
            call = child.reserve("0.01", model="fixture/model")
            child.settle(call, {"cost": "0.02"})
    assert ledger(parent).get("blocked")
    with pytest.raises(BudgetStop):
        with parent.lease(tmp_path / "b", phase="repeat", cap="0.10"):
            pytest.fail("Do not continue after pricing violation")


def test_existing_unregistered_budget_cannot_be_hidden(tmp_path):
    parent = RoundBudget(tmp_path)
    Budget(tmp_path / "a", cap="0.50", prices=PRICES)
    with pytest.raises(BudgetStop, match="unregistered"):
        with parent.lease(tmp_path / "a", phase="screening", cap="0.50"):
            pass


@pytest.mark.parametrize("phase, cap", [("other", "0.10"), ("screening", "0"),
                                       ("screening", "2.01"), ("screening", "NaN")])
def test_invalid_child_request_is_rejected(tmp_path, phase, cap):
    with pytest.raises(BudgetStop):
        with RoundBudget(tmp_path).lease(tmp_path / "a", phase=phase, cap=cap):
            pass


def test_child_output_cannot_escape_round(tmp_path):
    parent = RoundBudget(tmp_path / "round")
    for output in [tmp_path / "outside", tmp_path / "round"]:
        with pytest.raises(BudgetStop):
            with parent.lease(output, phase="screening", cap="0.10"):
                pass


def test_global_total_cannot_be_bypassed_with_another_phase(tmp_path):
    parent = RoundBudget(tmp_path)
    # Preserve a crashed child allocation from every phase.
    for phase, cap in parent.allocations.items():
        with parent.lease(tmp_path / phase, phase=phase, cap=cap):
            pass
    assert sum(Decimal(e["committed_usd"]) for e in ledger(parent)["runs"].values()) == 5
    with pytest.raises(BudgetStop):
        with parent.lease(tmp_path / "extra", phase="contingency", cap="0.01"):
            pytest.fail("Cannot bypass global ceiling")


@pytest.mark.parametrize("option", ["--output", "--output=/tmp/escape", "--out", "--o",
                                    "--max-usd", "--max-usd=99", "--max-u", "--max"])
def test_forwarded_options_cannot_override_budget_with_argparse_abbreviations(option):
    with pytest.raises(ValueError):
        bounded_arguments(["--", "--live", option, "99"])


def test_normal_child_arguments_are_preserved():
    assert bounded_arguments(["--", "--live", "--case", "rag-weak"]) == ["--live", "--case", "rag-weak"]
