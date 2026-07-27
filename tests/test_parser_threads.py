"""Capping inference thread pools to the container's share.

Whether the cap helps is a measurement, recorded in
`docs/parser-performance.md`. These tests cover what must hold regardless of
the answer: it is off unless asked for, it reaches both libraries, and it
never silently ignores a bad setting.
"""

import unittest
from unittest.mock import patch

import parsing.threads as threads_module
from parsing.threads import allowed_cores, limit_inference_threads, requested_threads


class RequestedThreadsTests(unittest.TestCase):
    def test_no_setting_leaves_the_libraries_alone(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(requested_threads())

    def test_a_setting_is_read(self):
        with patch.dict("os.environ", {"PARSER_INFERENCE_THREADS": "2"}):
            self.assertEqual(requested_threads(), 2)

    def test_a_meaningless_setting_fails_loudly(self):
        # Silently parsing at the wrong width would be attributed to the
        # hardware rather than to a typo.
        for value in ("two", "0", "-1"):
            with self.subTest(value=value):
                with patch.dict("os.environ", {"PARSER_INFERENCE_THREADS": value}):
                    with self.assertRaises(ValueError):
                        requested_threads()


class _FakeCgroupFile:
    """Stands in for /sys/fs/cgroup/cpu.max, which this laptop does not have."""

    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error

    def read_text(self):
        if self._error is not None:
            raise self._error
        return self._text


class AllowedCoresTests(unittest.TestCase):
    def _reading(self, text):
        with patch.object(threads_module, "CGROUP_CPU_MAX", _FakeCgroupFile(text)):
            return allowed_cores()

    def test_a_quota_is_reported_in_cores(self):
        # The deployed worker: 800000us of every 100000us period.
        self.assertEqual(self._reading("800000 100000"), 8.0)

    def test_an_unrestricted_container_reports_nothing(self):
        self.assertIsNone(self._reading("max 100000"))

    def test_a_host_without_cgroup_v2_reports_nothing(self):
        fake = _FakeCgroupFile(error=OSError("no cgroup"))
        with patch.object(threads_module, "CGROUP_CPU_MAX", fake):
            self.assertIsNone(allowed_cores())

    def test_an_unreadable_quota_reports_nothing(self):
        self.assertIsNone(self._reading("garbage"))


class LimitTests(unittest.TestCase):
    def setUp(self):
        threads_module._applied = False
        self.addCleanup(setattr, threads_module, "_applied", False)

    def test_nothing_happens_without_a_setting(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(threads_module, "_limit_onnxruntime") as onnx:
                self.assertIsNone(limit_inference_threads())
        onnx.assert_not_called()

    def test_the_cap_reaches_onnxruntime_and_the_openmp_variables(self):
        with patch.dict("os.environ", {"PARSER_INFERENCE_THREADS": "2"}):
            with patch.object(threads_module, "_limit_onnxruntime") as onnx:
                applied = limit_inference_threads()

                self.assertEqual(applied, 2)
                onnx.assert_called_once_with(2)
                # Torch and MKL read these at import and session construction.
                import os

                self.assertEqual(os.environ["OMP_NUM_THREADS"], "2")
                self.assertEqual(os.environ["MKL_NUM_THREADS"], "2")

    def test_it_is_applied_once_per_process(self):
        """Both libraries fix pool width at construction; re-applying is noise."""

        with patch.dict("os.environ", {"PARSER_INFERENCE_THREADS": "2"}):
            with patch.object(threads_module, "_limit_onnxruntime") as onnx:
                limit_inference_threads()
                limit_inference_threads()

        self.assertEqual(onnx.call_count, 1)


class OnnxDefaultTests(unittest.TestCase):
    def test_sessions_are_given_a_bounded_width_and_callers_keep_theirs(self):
        import onnxruntime

        original = onnxruntime.InferenceSession
        self.addCleanup(setattr, onnxruntime, "InferenceSession", original)

        seen = []
        onnxruntime.InferenceSession = lambda *a, **k: seen.append(k) or "session"
        threads_module._limit_onnxruntime(3)

        onnxruntime.InferenceSession("model.onnx", providers=["CPUExecutionProvider"])
        self.assertEqual(seen[0]["sess_options"].intra_op_num_threads, 3)
        self.assertEqual(seen[0]["sess_options"].inter_op_num_threads, 1)

        mine = onnxruntime.SessionOptions()
        mine.intra_op_num_threads = 9
        onnxruntime.InferenceSession("model.onnx", sess_options=mine)
        self.assertEqual(seen[1]["sess_options"].intra_op_num_threads, 9)

    def test_wrapping_twice_does_not_nest(self):
        import onnxruntime

        original = onnxruntime.InferenceSession
        self.addCleanup(setattr, onnxruntime, "InferenceSession", original)

        threads_module._limit_onnxruntime(3)
        wrapped = onnxruntime.InferenceSession
        threads_module._limit_onnxruntime(3)

        self.assertIs(onnxruntime.InferenceSession, wrapped)


if __name__ == "__main__":
    unittest.main()
