"""Run the API and the ingestion worker as one supervised service.

Video ingestion writes canonical sources, frames, and diagram crops to a
filesystem media root, and the API serves those same bytes back to the
browser. A Railway volume attaches to exactly one service, so the two
processes have to share a container to share a volume. That is the tradeoff
`AGENTS.md` asks for by name: production-shaped, not production-scaled — one
service and one volume instead of an object store to operate and pay for.

The supervisor is deliberately small and strict. If either process exits, the
other is stopped and the container exits too, so the platform restarts a known
state rather than leaving a half-running service that still answers health
checks while nothing processes the queue.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from types import FrameType


logger = logging.getLogger("study_partner.serve")

# How long a child gets to finish its current job after SIGTERM. The video
# worker checks for cancellation between stages, and a book job renews its
# lease well inside the window, so a stage in flight ends cleanly here.
DEFAULT_GRACE_SECONDS = 25.0
POLL_INTERVAL_SECONDS = 0.25


def api_command() -> list[str]:
    port = os.getenv("PORT", "8000")
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        port,
    ]


def worker_command() -> list[str]:
    return [sys.executable, "-m", "worker.main"]


def run(
    commands: dict[str, list[str]],
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> int:
    """Start every command, and stop all of them when the first one exits."""

    if not commands:
        raise ValueError("at least one command is required")
    children: dict[str, subprocess.Popen] = {}
    stopping = False

    def request_stop(number: int, frame: FrameType | None) -> None:
        del frame
        nonlocal stopping
        if stopping:
            return
        stopping = True
        logger.info("shutdown requested", extra={"signal": number})
        _terminate(children, grace_seconds=grace_seconds)

    previous = {
        number: signal.signal(number, request_stop)
        for number in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        for name, command in commands.items():
            logger.info("starting %s", name)
            children[name] = subprocess.Popen(command)

        while True:
            for name, child in children.items():
                code = child.poll()
                if code is None:
                    continue
                if not stopping:
                    logger.error("%s exited with code %s", name, code)
                    _terminate(children, grace_seconds=grace_seconds)
                # A clean stop reports success; a child that fell over on its
                # own must not look like a healthy shutdown to the platform.
                return 0 if stopping and code in (0, -signal.SIGTERM) else (code or 1)
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        _terminate(children, grace_seconds=grace_seconds)


def _terminate(
    children: dict[str, subprocess.Popen], *, grace_seconds: float
) -> None:
    for child in children.values():
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + grace_seconds
    for name, child in children.items():
        remaining = max(0.0, deadline - time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            logger.warning("%s ignored the shutdown request; killing it", name)
            child.kill()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    )
    return run({"api": api_command(), "worker": worker_command()})


if __name__ == "__main__":
    raise SystemExit(main())
