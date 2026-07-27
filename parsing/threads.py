"""Size inference thread pools to the container rather than to the host.

`os.cpu_count()` reports the machine's processors; a cgroup quota decides how
many of them this process may actually consume. The deployed worker sees 48
and is allowed 8. ONNX Runtime and Torch both size their thread pools from
the former, and parsing runs four processes at once, so the default is a pool
of roughly 48 threads per process contending for two cores' worth of quota
each.

ONNX Runtime takes its width from `SessionOptions`, not from the environment:
`OMP_NUM_THREADS` reaches Torch and leaves the layout model alone. Since
`unstructured_inference` constructs its sessions without options, the only
way in is to supply a default as they are built.

Opt in with `PARSER_INFERENCE_THREADS`. Whether this helps is a question
about a particular container, so it is measured per deployment rather than
assumed - see docs/parser-performance.md.
"""

import logging
import os
from pathlib import Path


logger = logging.getLogger("study_partner.parsing")

CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")

_applied = False


def allowed_cores() -> float | None:
    """Cores this container's cgroup permits, or None if it is unrestricted."""

    try:
        quota, period = CGROUP_CPU_MAX.read_text().split()
    except (OSError, ValueError):
        return None
    if quota == "max":
        return None
    try:
        return int(quota) / int(period)
    except (ValueError, ZeroDivisionError):
        return None


def requested_threads() -> int | None:
    """Threads each inference pool should use, or None to leave the default."""

    raw = os.getenv("PARSER_INFERENCE_THREADS", "").strip()
    if not raw:
        return None
    try:
        threads = int(raw)
    except ValueError as error:
        raise ValueError("PARSER_INFERENCE_THREADS must be an integer") from error
    if threads < 1:
        raise ValueError("PARSER_INFERENCE_THREADS must be at least 1")
    return threads


def limit_inference_threads() -> int | None:
    """Cap this process's inference pools. Returns the cap, or None.

    Applied once per process and before any model is built, since both
    libraries size their pools when a session is constructed and ignore the
    setting afterwards.
    """

    global _applied
    if _applied:
        return requested_threads()

    threads = requested_threads()
    if threads is None:
        return None

    _applied = True
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)

    try:
        import torch

        torch.set_num_threads(threads)
    except ImportError:  # pragma: no cover - torch ships with the parser
        pass

    _limit_onnxruntime(threads)
    logger.info(
        "inference pools capped at %s threads (cgroup allows %s cores, "
        "os.cpu_count reports %s)",
        threads,
        allowed_cores(),
        os.cpu_count(),
    )
    return threads


def _limit_onnxruntime(threads: int) -> None:
    """Give every later InferenceSession a bounded default.

    `unstructured_inference` builds its sessions with a model path and a
    provider list and nothing else, so the width has to be supplied here. A
    caller that passes its own options keeps them.
    """

    import onnxruntime

    original = onnxruntime.InferenceSession
    if getattr(original, "_bounded", False):
        return

    def bounded(*args, **kwargs):
        if "sess_options" not in kwargs:
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = threads
            # One session runs at a time per process; parallelism across
            # operators would compete with the other parse processes.
            options.inter_op_num_threads = 1
            kwargs["sess_options"] = options
        return original(*args, **kwargs)

    bounded._bounded = True
    bounded._original = original
    onnxruntime.InferenceSession = bounded


__all__ = ["allowed_cores", "limit_inference_threads", "requested_threads"]
