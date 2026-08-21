"""Deck persistence, the generation queue, and the pipeline, against Postgres."""

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from decks import jobs, store
from decks.contracts import CardBack, DeckCard, DeckCitation, DeckPreferences
from decks.generate import GeneratedDeck
from decks.pipeline import DeckSourceError, load_inventory, run_deck_job
from decks.topics import Topic, book_scope_key
from decks.validate import ValidationTally, build_metrics
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.scope import resolve_chapter
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


def topic(key: str, *, ordinal: int, node_id: int, required: bool = True) -> Topic:
    return Topic(
        key=key,
        ordinal=ordinal,
        label=f"Chapter 1 > {key}",
        required=required,
        evidence_text="evidence",
        allowed_markers=frozenset({f"[N{node_id}:P1]"}),
        node_id=node_id,
        start_page=1,
        end_page=2,
    )


def card(topic_key: str, *, index: int, node_id: int, front: str) -> DeckCard:
    return DeckCard(
        topic_key=topic_key,
        card_index=index,
        card_type="qa",
        front=front,
        back=CardBack(answer="Because of the invariant.", say_it_aloud="Invariants."),
        citations=[DeckCitation(marker=f"[N{node_id}:P1]", node_id=node_id, page=1)],
        interview_priority=4,
    )


class DeckStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        self.scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )
        self.node_id = self.scope.root_node_id
        self.scope_key = book_scope_key(self.book_id, self.node_id)

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _store(
        self,
        *,
        fronts: tuple[str, ...] = ("What is an invariant?",),
        generation_mode: str = "topic_generated",
        scope_key: str | None = None,
    ):
        deck_id, version = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=scope_key or self.scope_key,
            title=self.scope.display_path,
            source_title=self.scope.book_title,
            book_id=self.book_id,
            node_id=self.node_id,
            generation_mode=generation_mode,
        )
        topics = (topic("node:a", ordinal=0, node_id=self.node_id),)
        cards = tuple(
            card("node:a", index=index, node_id=self.node_id, front=front)
            for index, front in enumerate(fronts)
        )
        tally = ValidationTally(generated=len(cards), kept=len(cards))
        for item in cards:
            tally.keep(item)
        store.store_deck(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
            topics=topics,
            generated=GeneratedDeck(
                inventory=None,  # unused by the store
                cards=cards,
                metrics=build_metrics(
                    tally,
                    topics=topics,
                    covered_keys={"node:a"},
                    repair_attempted=False,
                ),
                model_name="test/model",
                prompt_version="decks-v1:test",
            ),
        )
        return deck_id, version

    def test_a_stored_deck_is_readable_with_its_metrics(self) -> None:
        deck_id, version = self._store()
        self.assertEqual(version, 1)

        summary = store.get_deck(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        self.assertEqual(summary.status, "ready")
        self.assertEqual(summary.set_number, 1)
        self.assertEqual(summary.card_count, 1)
        self.assertEqual(summary.metrics.topics_covered, 1)
        self.assertEqual(summary.new_count, 1)

        cards = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        self.assertEqual(cards[0].review.state, "new")
        self.assertEqual(cards[0].card.citations[0].page, 1)

    def test_another_generation_publishes_a_cumulative_numbered_set(self) -> None:
        first, _ = self._store()
        second, version = self._store(fronts=("What is a lapse?",))
        self.assertEqual(version, 2)

        library = store.list_decks(self.connection, owner_id=self.owner_id)
        by_set = {deck.set_number: deck.deck_id for deck in library}
        self.assertEqual(by_set, {1: str(first), 2: str(second)})
        previous = store.get_deck(
            self.connection, owner_id=self.owner_id, deck_id=first
        )
        self.assertEqual(previous.status, "ready")
        self.assertEqual(len(store.new_cards(self.connection, owner_id=self.owner_id)), 2)

    def test_a_failed_attempt_does_not_consume_a_set_number(self) -> None:
        failed, version = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=self.scope_key,
            title=self.scope.display_path,
            source_title=self.scope.book_title,
            book_id=self.book_id,
            node_id=self.node_id,
        )
        self.assertEqual(version, 1)
        store.fail_deck(self.connection, owner_id=self.owner_id, deck_id=failed)

        published, version = self._store()
        self.assertEqual(version, 2)
        self.assertEqual(
            store.get_deck(
                self.connection, owner_id=self.owner_id, deck_id=published
            ).set_number,
            1,
        )

    def test_extracted_questions_remain_one_replaceable_deck(self) -> None:
        extracted_scope = book_scope_key(
            self.book_id, self.node_id, generation_mode="book_extracted"
        )
        first, _ = self._store(
            generation_mode="book_extracted", scope_key=extracted_scope
        )
        second, _ = self._store(
            fronts=("What does the exercise ask?",),
            generation_mode="book_extracted",
            scope_key=extracted_scope,
        )

        self.assertEqual(
            store.get_deck(
                self.connection, owner_id=self.owner_id, deck_id=first
            ).status,
            "failed",
        )
        replacement = store.get_deck(
            self.connection, owner_id=self.owner_id, deck_id=second
        )
        self.assertEqual(replacement.set_number, 1)

    def test_previous_generated_fronts_are_available_to_the_next_set(self) -> None:
        self._store(fronts=("First question?", "Second question?"))
        self.assertEqual(
            store.generated_fronts(
                self.connection,
                owner_id=self.owner_id,
                scope_key=self.scope_key,
            ),
            ("First question?", "Second question?"),
        )

    def test_grading_moves_the_card_and_records_the_event(self) -> None:
        deck_id, _ = self._store()
        card_id = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )[0].card.card_id

        state = store.record_review(
            self.connection, owner_id=self.owner_id, card_id=card_id, rating=3
        )
        self.assertEqual(state.state, "review")
        self.assertEqual(state.reps, 1)

        reviewed, introduced = store.counts_today(
            self.connection, owner_id=self.owner_id
        )
        self.assertEqual((reviewed, introduced), (1, 1))

    def test_todays_count_uses_the_database_clock(self) -> None:
        """A review recorded moments ago always counts as today's.

        Deriving the day boundary from Python's local date and comparing it
        against UTC timestamps made this false for the hours where the two
        dates disagree — the daily cap silently reset just after local
        midnight. Asserting against the database's own clock is what makes the
        test independent of where it runs.
        """

        deck_id, _ = self._store()
        card_id = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )[0].card.card_id
        store.record_review(
            self.connection, owner_id=self.owner_id, card_id=card_id, rating=3
        )

        boundary = self.connection.execute(
            "select date_trunc('day', now()) as start, now() as current"
        ).fetchone()
        self.assertGreaterEqual(boundary["current"], boundary["start"])
        reviewed, _ = store.counts_today(self.connection, owner_id=self.owner_id)
        self.assertEqual(reviewed, 1)

    def test_a_failed_card_comes_back_and_a_known_one_does_not(self) -> None:
        deck_id, _ = self._store(fronts=("A?", "B?"))
        cards = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        now = datetime.now(UTC)

        store.record_review(
            self.connection,
            owner_id=self.owner_id,
            card_id=cards[0].card.card_id,
            rating=1,
            now=now,
        )
        store.record_review(
            self.connection,
            owner_id=self.owner_id,
            card_id=cards[1].card.card_id,
            rating=4,
            now=now,
        )

        soon = store.due_cards(self.connection, owner_id=self.owner_id, limit=50)
        self.assertEqual(soon, [])  # neither is due this second

        # Ten minutes on, the failed card is back and the easy one is not.
        self.connection.execute(
            """
            update public.deck_card_reviews
            set due_at = due_at - interval '15 minutes'
            where owner_id = %s
            """,
            (self.owner_id,),
        )
        due = store.due_cards(self.connection, owner_id=self.owner_id, limit=50)
        self.assertEqual([item.card.front for item in due], ["A?"])

    def test_reset_sends_every_card_back_to_new(self) -> None:
        deck_id, _ = self._store()
        card_id = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )[0].card.card_id
        store.record_review(
            self.connection, owner_id=self.owner_id, card_id=card_id, rating=3
        )
        store.reset_deck_progress(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        card_state = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )[0].review
        self.assertEqual(card_state.state, "new")
        self.assertEqual(card_state.reps, 0)

    def test_preferences_default_then_persist(self) -> None:
        self.assertEqual(
            store.get_preferences(self.connection, owner_id=self.owner_id),
            DeckPreferences(new_cards_per_day=10, max_reviews_per_day=120),
        )
        store.save_preferences(
            self.connection,
            owner_id=self.owner_id,
            preferences=DeckPreferences(new_cards_per_day=25, max_reviews_per_day=200),
        )
        self.assertEqual(
            store.get_preferences(
                self.connection, owner_id=self.owner_id
            ).new_cards_per_day,
            25,
        )


class DeckQueueTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        self.scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )
        self.scope_key = book_scope_key(self.book_id, self.scope.root_node_id)

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _enqueue(self):
        return jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=self.scope_key,
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )

    def test_a_second_request_attaches_to_the_run_already_in_flight(self) -> None:
        first = self._enqueue()
        second = self._enqueue()
        self.assertEqual(first.id, second.id)

    def test_progress_and_completion_are_recorded(self) -> None:
        job = self._enqueue()
        jobs.record_progress(
            self.connection,
            job_id=job.id,
            stage="generation",
            topics_total=4,
            topics_done=2,
        )
        current = jobs.get_job(
            self.connection, owner_id=self.owner_id, job_id=job.id
        )
        self.assertEqual(current.progress_ratio, 0.5)

    def test_a_retryable_failure_requeues_until_the_attempts_are_spent(self) -> None:
        job = self._enqueue()
        self.connection.execute(
            "update public.deck_jobs set attempt_count = 1 where id = %s", (job.id,)
        )
        jobs.fail_job(
            self.connection, job_id=job.id, code="generation_failed", retryable=True
        )
        self.assertEqual(
            jobs.get_job(self.connection, owner_id=self.owner_id, job_id=job.id).status,
            "queued",
        )

        self.connection.execute(
            "update public.deck_jobs set attempt_count = 3 where id = %s", (job.id,)
        )
        jobs.fail_job(
            self.connection, job_id=job.id, code="generation_failed", retryable=True
        )
        self.assertEqual(
            jobs.get_job(self.connection, owner_id=self.owner_id, job_id=job.id).status,
            "failed",
        )

    def test_a_source_failure_does_not_retry(self) -> None:
        job = self._enqueue()
        jobs.fail_job(
            self.connection,
            job_id=job.id,
            code="source_unavailable",
            retryable=False,
        )
        self.assertEqual(
            jobs.get_job(self.connection, owner_id=self.owner_id, job_id=job.id).status,
            "failed",
        )

    def test_a_failed_job_can_be_dismissed_without_deleting_it(self) -> None:
        job = self._enqueue()
        jobs.fail_job(
            self.connection,
            job_id=job.id,
            code="source_unavailable",
            retryable=False,
        )
        self.assertEqual(
            [
                item.id
                for item in jobs.list_jobs(
                    self.connection, owner_id=self.owner_id
                )
            ],
            [job.id],
        )

        self.assertTrue(
            jobs.dismiss_failed_job(
                self.connection, owner_id=self.owner_id, job_id=job.id
            )
        )
        self.assertEqual(
            jobs.list_jobs(self.connection, owner_id=self.owner_id), []
        )
        self.assertEqual(
            jobs.get_job(
                self.connection, owner_id=self.owner_id, job_id=job.id
            ).status,
            "failed",
        )

    def test_a_newer_attempt_resolves_the_old_failure_alert(self) -> None:
        failed = self._enqueue()
        jobs.fail_job(
            self.connection,
            job_id=failed.id,
            code="source_unavailable",
            retryable=False,
        )
        retry = self._enqueue()

        visible = jobs.list_jobs(self.connection, owner_id=self.owner_id)
        self.assertIn(retry.id, [item.id for item in visible])
        self.assertNotIn(failed.id, [item.id for item in visible])

    def test_dismissal_is_owner_scoped_and_repeatable(self) -> None:
        job = self._enqueue()
        jobs.fail_job(
            self.connection,
            job_id=job.id,
            code="source_unavailable",
            retryable=False,
        )
        stranger = uuid4()
        self.assertFalse(
            jobs.dismiss_failed_job(
                self.connection, owner_id=stranger, job_id=job.id
            )
        )
        self.assertTrue(
            jobs.dismiss_failed_job(
                self.connection, owner_id=self.owner_id, job_id=job.id
            )
        )
        self.assertTrue(
            jobs.dismiss_failed_job(
                self.connection, owner_id=self.owner_id, job_id=job.id
            )
        )

    def test_an_expired_lease_returns_the_job_to_the_queue(self) -> None:
        job = self._enqueue()
        self.connection.execute(
            """
            update public.deck_jobs
            set status = 'running', worker_id = 'gone', lease_expires_at = %s
            where id = %s
            """,
            (datetime.now(UTC) - timedelta(minutes=5), job.id),
        )
        self.assertEqual(jobs.reclaim_expired_leases(self.connection), 1)
        self.assertEqual(
            jobs.get_job(self.connection, owner_id=self.owner_id, job_id=job.id).status,
            "queued",
        )


class DeckJobEndpointTests(PostgresOwnerMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )
        self.node_id = scope.root_node_id
        self.scope_key = book_scope_key(self.book_id, self.node_id)
        self.failed = jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=self.scope_key,
            book_id=self.book_id,
            node_id=self.node_id,
        )
        jobs.fail_job(
            self.connection,
            job_id=self.failed.id,
            code="source_unavailable",
            retryable=False,
        )
        # The endpoint uses a separate pooled connection.
        self.connection.commit()
        app.dependency_overrides[current_owner] = lambda: UUID(self.owner_id)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(current_owner, None)
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    async def _client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def test_failed_job_dismissal_is_idempotent_and_retains_history(
        self,
    ) -> None:
        async with await self._client() as client:
            first = await client.post(
                f"/api/decks/jobs/{self.failed.id}/dismiss"
            )
            second = await client.post(
                f"/api/decks/jobs/{self.failed.id}/dismiss"
            )
        self.assertEqual(first.status_code, 204, first.text)
        self.assertEqual(second.status_code, 204, second.text)
        self.assertEqual(
            jobs.get_job(
                self.connection, owner_id=self.owner_id, job_id=self.failed.id
            ).status,
            "failed",
        )

    async def test_failed_job_dismissal_is_owner_scoped(self) -> None:
        app.dependency_overrides[current_owner] = uuid4
        async with await self._client() as client:
            response = await client.post(
                f"/api/decks/jobs/{self.failed.id}/dismiss"
            )
        self.assertEqual(response.status_code, 404, response.text)

    async def test_malformed_job_id_is_not_found(self) -> None:
        async with await self._client() as client:
            response = await client.post(
                "/api/decks/jobs/not-a-uuid/dismiss"
            )
        self.assertEqual(response.status_code, 404, response.text)

    async def test_live_job_cannot_be_dismissed(self) -> None:
        live = jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=f"{self.scope_key}:another-set",
            book_id=self.book_id,
            node_id=self.node_id,
        )
        self.connection.commit()
        async with await self._client() as client:
            response = await client.post(
                f"/api/decks/jobs/{live.id}/dismiss"
            )
        self.assertEqual(response.status_code, 409, response.text)


class DeckInventoryTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        self.scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def test_a_book_chapter_inventories_its_content_bearing_nodes(self) -> None:
        inventory, version = load_inventory(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )
        self.assertIsNone(version)
        self.assertEqual(inventory.source_kind, "book")
        self.assertTrue(inventory.topics)
        # Every marker offered to a topic resolves inside that topic, which is
        # what makes an out-of-scope citation detectable at all.
        for item in inventory.topics:
            for marker in item.allowed_markers:
                self.assertIn(marker, item.evidence_text)

    def test_an_unknown_node_is_a_source_error_not_a_crash(self) -> None:
        with self.assertRaises(DeckSourceError):
            load_inventory(
                self.connection,
                owner_id=self.owner_id,
                source_kind="book",
                book_id=self.book_id,
                node_id=10_000_000,
            )


class DeckPipelineTests(PostgresOwnerMixin, unittest.TestCase):
    """One job, run end to end with a scripted model."""

    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        self.scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def test_a_job_produces_a_readable_deck_with_scheduling_rows(self) -> None:
        from decks.contracts import GeneratedCard, TopicCards

        inventory, _ = load_inventory(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )
        first = inventory.topics[0]
        marker = sorted(first.allowed_markers)[0]

        class Scripted:
            def invoke(self, messages):
                return TopicCards(
                    cards=[
                        GeneratedCard(
                            topic_ordinal=first.ordinal + 1,
                            card_type="qa",
                            front="What does this chapter introduce?",
                            back=CardBack(
                                answer=f"The core idea. {marker}",
                                say_it_aloud="The core idea.",
                            ),
                            citation_markers=[marker],
                            interview_priority=5,
                        )
                    ]
                )

        job = jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=book_scope_key(self.book_id, self.scope.root_node_id),
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )
        run = run_deck_job(self.connection, job=job, model=Scripted())

        summary = store.get_deck(
            self.connection, owner_id=self.owner_id, deck_id=run.deck_id
        )
        self.assertIn(summary.status, ("ready", "partial"))
        self.assertEqual(summary.card_count, 1)
        self.assertEqual(summary.metrics.cards_kept, 1)

        queued = store.new_cards(self.connection, owner_id=self.owner_id)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].review.state, "new")

        finished = jobs.get_job(
            self.connection, owner_id=self.owner_id, job_id=job.id
        )
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(str(finished.deck_id), str(run.deck_id))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
