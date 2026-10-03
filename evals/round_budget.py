"""Serial parent reservations for a round of individually guarded experiments."""
from contextlib import contextmanager
from decimal import Decimal
from datetime import UTC, datetime
import fcntl
import json
from pathlib import Path

from .budget import BudgetStop, atomic_json, money


ALLOCATIONS = {"screening": "1.25", "repeat": "1.00", "final": "1.50",
               "production": "0.75", "contingency": "0.50"}


class RoundBudget:
    def __init__(self, directory, *, cap="5", allocations=None):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "round-budget.json"
        self.cap = money(cap)
        self.allocations = {key: str(money(value)) for key, value in
                            (allocations or ALLOCATIONS).items()}
        if not 0 < self.cap <= 5 or sum(map(money, self.allocations.values())) != self.cap:
            raise BudgetStop("Round allocations must equal a positive ceiling at most $5")

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data["cap_usd"] != str(self.cap) or data["allocations"] != self.allocations:
                raise BudgetStop("Resume must preserve round ceiling and allocations")
            return data
        return {"version": 1, "cap_usd": str(self.cap), "allocations": self.allocations,
                "runs": {}}

    def phase_limits(self, data):
        """Immutable base allocations plus explicit, audited transfers."""
        limits = {key: money(value) for key, value in self.allocations.items()}
        for item in data.get("transfers", []):
            source, destination = item["from"], item["to"]
            amount = money(item["usd"])
            if (source not in limits or destination not in limits or source == destination
                    or amount <= 0 or amount > limits[source] or not item.get("reason")):
                raise BudgetStop("Invalid recorded phase transfer")
            limits[source] -= amount
            limits[destination] += amount
        return limits

    def transfer(self, source, destination, amount, *, reason):
        """Reallocate unused capacity, with no change to the round ceiling.

        Active child leases hold this same lock, so no running experiment can
        lose its reserved capacity. Unknown request reservations remain spent.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".round.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BudgetStop("Another round experiment is still running") from error
            try:
                data = self._load()
                if data.get("blocked"):
                    raise BudgetStop(data["blocked"])
                for entry in data["runs"].values():
                    self._reconcile(entry)
                item = {"from": source, "to": destination, "usd": str(money(amount)),
                        "reason": reason, "at": datetime.now(UTC).isoformat()}
                proposed = {**data, "transfers": [*data.get("transfers", []), item]}
                limits = self.phase_limits(proposed)
                for phase, ceiling in limits.items():
                    used = sum((money(v["committed_usd"]) for v in data["runs"].values()
                                if v["phase"] == phase), Decimal(0))
                    if used > ceiling:
                        raise BudgetStop("Transfer would consume spent or unresolved capacity")
                atomic_json(self.path, proposed)
                return {key: str(value) for key, value in limits.items()}
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _reconcile(self, entry):
        path = self.directory / entry["directory"] / "budget.json"
        if not path.exists():
            # A killed process may have started before its budget file was saved.
            # Never assume it sent nothing merely because that file is absent.
            return
        data = json.loads(path.read_text())
        if money(data["cap_usd"]) != money(entry["cap_usd"]):
            raise BudgetStop("Child budget ceiling changed")
        calls = data["calls"]
        committed = sum((money(call.get("cost_usd", call["reserved_usd"]))
                         for call in calls), Decimal(0))
        entry.update(committed_usd=str(committed),
                     reported_usd=str(sum((money(c.get("cost_usd", 0)) for c in calls), Decimal(0))),
                     unknown_reservations=sum(c["status"] == "reserved" for c in calls),
                     status="reconciled")
        if committed > money(entry["cap_usd"]) or data.get("blocked"):
            raise BudgetStop("Child pricing violation; stop the round and investigate")

    @contextmanager
    def lease(self, directory, *, phase, cap):
        """Reserve before launching; pass the lock fd to the child process.

        Inheritance keeps the lock held if the coordinator dies while its child
        is still running. An acquired lock therefore permits reconciling prior
        child ledgers; unresolved request reservations remain charged.
        """
        directory = Path(directory).resolve()
        if directory == self.directory or not directory.is_relative_to(self.directory):
            raise BudgetStop("Child output must be inside the round directory")
        requested = money(cap)
        if not 0 < requested <= 2 or phase not in self.allocations:
            raise BudgetStop("Unknown phase or invalid child ceiling")
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".round.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BudgetStop("Another round experiment is still running") from error
            try:
                data = self._load()
                if data.get("blocked"):
                    raise BudgetStop(data["blocked"])
                key = str(directory.relative_to(self.directory))
                previous = data["runs"].get(key)
                if previous and (previous["phase"] != phase or money(previous["cap_usd"]) != requested):
                    raise BudgetStop("Resume must preserve child phase and ceiling")
                if not previous and (directory / "budget.json").exists():
                    raise BudgetStop("Cannot adopt an unregistered paid run")
                try:
                    for entry in data["runs"].values():
                        self._reconcile(entry)
                except BudgetStop:
                    data["blocked"] = "Child budget integrity/pricing violation"
                    atomic_json(self.path, data)
                    raise
                others = [v for k, v in data["runs"].items() if k != key]
                total = sum((money(v["committed_usd"]) for v in others), Decimal(0))
                phase_total = sum((money(v["committed_usd"]) for v in others if v["phase"] == phase), Decimal(0))
                if total + requested > self.cap or phase_total + requested > self.phase_limits(data)[phase]:
                    atomic_json(self.path, data)
                    raise BudgetStop("Next child ceiling would exceed round or phase allocation")
                entry = {"directory": key, "phase": phase, "cap_usd": str(requested),
                         "committed_usd": str(requested), "status": "leased"}
                data["runs"][key] = entry
                atomic_json(self.path, data)
                try:
                    yield lock.fileno()
                finally:
                    try:
                        self._reconcile(entry)
                    except BudgetStop:
                        data["blocked"] = "Child budget integrity/pricing violation"
                        raise
                    finally:
                        atomic_json(self.path, data)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
