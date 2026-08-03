"""Content-addressed artifact and budget helpers for resumable pilot runs."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, TypeVar


T = TypeVar("T")


def sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json)
    return sha256(payload.encode("utf-8")).hexdigest()


def _json(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=_json)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default: T | None = None) -> Any | T | None:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class CostLimitExceeded(RuntimeError):
    """A model request would exceed an experiment budget."""


class CostLedger:
    def __init__(self, path: Path, *, maximum_usd: float) -> None:
        self.path = path
        self.maximum_usd = maximum_usd
        self.entries: list[dict[str, Any]] = list(read_json(path, []) or [])

    @property
    def spent_usd(self) -> float:
        return sum(float(entry.get("cost_usd") or 0.0) for entry in self.entries)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.maximum_usd - self.spent_usd)

    def reserve(self, estimated_cost_usd: float, *, operation: str) -> None:
        if self.spent_usd + estimated_cost_usd > self.maximum_usd:
            raise CostLimitExceeded(
                f"{operation} estimated ${estimated_cost_usd:.4f}; "
                f"only ${self.remaining_usd:.4f} remains"
            )

    def record(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        write_json(self.path, self.entries)
        if self.spent_usd > self.maximum_usd + 1e-9:
            raise CostLimitExceeded(
                f"model cost ${self.spent_usd:.4f} exceeded "
                f"${self.maximum_usd:.4f} cap"
            )


def records(path: Path, constructor):
    return [constructor(**item) for item in (read_json(path, []) or [])]


def as_records(values: Iterable[Any]) -> list[dict[str, Any]]:
    return [asdict(value) if is_dataclass(value) else dict(value) for value in values]

