"""Hermetic defaults: a developer's .env must never select hosted test services."""
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit

import pytest

# api.main loads .env during collection. Establish safe defaults before imports;
# individual provider/exporter tests explicitly override them with fixtures.
for key, value in {"VIDEO_MEDIA_BACKEND": "filesystem", "BOOK_IMAGE_BACKEND": "filesystem",
                   "SOURCE_STORAGE_BACKEND": "supabase",
                   "LANGSMITH_TRACING": "false", "OTEL_ENABLED": "false",
                   "OPENROUTER_API_KEY": ""}.items():
    os.environ[key] = value
for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.setdefault(key, "")
if os.environ["SUPABASE_URL"] and urlsplit(os.environ["SUPABASE_URL"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise pytest.UsageError("Ordinary tests require loopback Supabase services; hosted credentials are not test fixtures")
if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]


def pytest_collection_modifyitems(items):
    # Queue claims intentionally span owners. Even owner-scoped fixtures cannot
    # isolate them from live jobs. Skip before unittest creates its own queue.
    queue_files = set()
    for source in {Path(str(item.path)) for item in items}:
        if source.name.startswith("test_") and any(name in source.read_text() for name in
                ("claim_next_job", "reclaim_expired_leases", "claim_job")):
            queue_files.add(source)
    if queue_files:
        from tests.postgres import require_empty_ingestion_queue, require_empty_video_queue
        try:
            require_empty_ingestion_queue(unittest.TestCase())
            require_empty_video_queue(unittest.TestCase())
        except unittest.SkipTest as error:
            for item in items:
                if Path(str(item.path)) in queue_files:
                    item.add_marker(pytest.mark.skip(reason=str(error)))
