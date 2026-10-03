"""Shared worker queues require an empty test database, including stale leases."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest

import pytest

from tests.postgres import require_empty_video_queue


@pytest.mark.parametrize("count", [1, 15])
def test_foreign_video_jobs_prevent_global_test_claims(monkeypatch, count):
    @contextmanager
    def database(*args, **kwargs):
        assert kwargs["readonly"] is True
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: {"busy": count}))
    monkeypatch.setattr("tests.postgres.connection", database)
    with pytest.raises(unittest.SkipTest, match="isolated TEST_DATABASE_URL"):
        require_empty_video_queue(unittest.TestCase())
