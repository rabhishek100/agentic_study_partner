"""The combined service stops as one unit rather than half-running."""

import os
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from scripts.serve import api_command, run, worker_command


def sleeper(seconds: float) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def failing(code: int) -> list[str]:
    return [sys.executable, "-c", f"raise SystemExit({code})"]


class ServeSupervisorTests(unittest.TestCase):
    def test_one_child_failing_stops_the_other_and_reports_failure(self) -> None:
        started = time.monotonic()
        code = run(
            {"worker": failing(3), "api": sleeper(30)},
            grace_seconds=5,
        )
        elapsed = time.monotonic() - started

        # The container must exit so the platform restarts a known state,
        # instead of answering health checks with a dead queue behind it.
        self.assertEqual(code, 3)
        self.assertLess(elapsed, 15)

    def test_a_child_leaving_on_its_own_fails_even_with_a_zero_code(self) -> None:
        # Neither process is supposed to finish. A worker that exits 0 has
        # stopped draining the queue, which is an outage however politely it
        # left, so the service must not report success and stay up.
        self.assertEqual(run({"worker": failing(0), "api": sleeper(30)}), 1)

    def test_shutdown_signals_reach_both_children(self) -> None:
        marker = sleeper(30)
        # SIGTERM is delivered to this process while the supervisor waits,
        # exactly as the platform delivers it during a redeploy.
        def stop_soon() -> None:
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        import threading

        threading.Thread(target=stop_soon, daemon=True).start()
        started = time.monotonic()
        code = run({"api": marker, "worker": sleeper(30)}, grace_seconds=5)

        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - started, 15)

    def test_a_child_that_ignores_shutdown_is_killed(self) -> None:
        stubborn = [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, lambda *a: None); "
            "time.sleep(30)",
        ]
        started = time.monotonic()
        code = run({"worker": failing(2), "api": stubborn}, grace_seconds=1)

        self.assertEqual(code, 2)
        self.assertLess(time.monotonic() - started, 12)

    def test_commands_carry_the_configured_port_and_module_entrypoints(self) -> None:
        with patch.dict(os.environ, {"PORT": "9123"}):
            api = api_command()
        self.assertIn("api.main:app", api)
        self.assertIn("9123", api)
        self.assertEqual(worker_command()[-2:], ["-m", "worker.main"])

    def test_no_commands_is_a_configuration_error(self) -> None:
        with self.assertRaises(ValueError):
            run({})


if __name__ == "__main__":
    unittest.main()
