"""Real Postgres regression for overlapping, paid chapter generations."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
import unittest
from unittest.mock import patch

from interviews.contracts import InterviewCitation
from interviews.ideal_contracts import IdealInterviewExchange
from interviews.ideal_service import CreateIdealInterview, create_ideal_interview
from interviews.planning import load_source
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.scope import resolve_chapter
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class IdealGenerationConcurrencyTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self):
        self.setUpPostgresOwner()
        self.addCleanup(self.tearDownPostgresOwner)
        # Commit canonical fixtures before opening independent request connections.
        with database_connection(self.database_url) as database:
            book = sample_book()
            book.sections[0].texts[0].text = (
                "Requirements include throughput, durability, latency and failure handling. " * 10
            )
            self.book_id = ingest_book(database, book, owner_id=self.owner_id,
                title="Sample Book", author="Test Author", file_hash=FILE_HASH,
                page_count=5, parser_version="test-v1")
            self.node_id = resolve_chapter(database, 1, owner_id=self.owner_id,
                book_id=self.book_id).root_node_id
            self.inventory = load_source(database, owner_id=self.owner_id,
                source_kind="book", book_id=self.book_id, node_id=self.node_id).inventory

    def generate(self, level="mid"):
        with database_connection(self.database_url) as database:
            return create_ideal_interview(database, owner_id=self.owner_id,
                request=CreateIdealInterview(self.book_id, self.node_id, level))

    @staticmethod
    def exchange(**kwargs):
        topic = kwargs["topic"]
        return IdealInterviewExchange(exchange_index=kwargs["index"], phase="deep_dive",
            topic_key=topic.key, topic_label=topic.label,
            interviewer_text="Explain this source-backed decision.",
            candidate_text="The source describes the decision and its trade-offs.",
            citations=[InterviewCitation(marker=sorted(topic.allowed_markers)[0],
                node_id=topic.node_id, page=topic.start_page)]), 0.01

    def test_overlapping_retries_share_one_generation_and_other_settings_do_not_wait(self):
        started, release, retry_loaded = Event(), Event(), Event()

        def provider(**kwargs):
            if kwargs["target_level"] == "mid" and kwargs["index"] == 0:
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test failed to release generation")
            return self.exchange(**kwargs)

        def observed_load(*args, **kwargs):
            result = load_source(*args, **kwargs)
            if started.is_set():
                retry_loaded.set()
            return result

        with patch("interviews.ideal_service.generate_ideal_exchange", side_effect=provider) as invoke, \
             patch("interviews.ideal_service.load_source", side_effect=observed_load), \
             ThreadPoolExecutor(max_workers=3) as workers:
            first = workers.submit(self.generate)
            try:
                self.assertTrue(started.wait(3))
                retry = workers.submit(self.generate)
                self.assertTrue(retry_loaded.wait(3))
                with self.assertRaises(TimeoutError):
                    retry.result(timeout=0.1)
                self.assertEqual(invoke.call_count, 1)
                # A different unique generation key must not share the lock.
                senior = workers.submit(self.generate, "senior").result(timeout=3)
            finally:
                release.set()
            winner = first.result(timeout=3)
            reused = retry.result(timeout=3)
            self.assertEqual(winner.flow_id, reused.flow_id)
            self.assertNotEqual(winner.flow_id, senior.flow_id)
            self.assertEqual(invoke.call_count, 2 * len(self.inventory.topics))
            self.assertEqual(winner.coverage_ratio, 1)
            self.assertEqual(winner.exchanges, reused.exchanges)
            self.assertEqual(self.generate().flow_id, winner.flow_id)
            self.assertEqual(invoke.call_count, 2 * len(self.inventory.topics))

    def test_failed_generation_releases_lock_and_retry_can_generate(self):
        with patch("interviews.ideal_service.generate_ideal_exchange",
                   side_effect=ValueError("invalid citations")) as invoke:
            with self.assertRaisesRegex(ValueError, "invalid citations"):
                self.generate()
            self.assertEqual(invoke.call_count, 2)
        with patch("interviews.ideal_service.generate_ideal_exchange",
                   side_effect=self.exchange) as invoke:
            result = self.generate()
            self.assertEqual(result.covered_topic_count, len(self.inventory.topics))
            self.assertEqual(invoke.call_count, len(self.inventory.topics))
