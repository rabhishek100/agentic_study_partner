"""Per-source Cards eligibility and automatic Set 1 dispatch."""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from psycopg.types.json import Jsonb

from api.auth import current_owner
from api.main import app
from decks import jobs, source_preferences, store
from decks.contracts import CardBack, DeckCard, DeckCitation
from decks.generate import GeneratedDeck
from decks.topics import Topic, book_scope_key, video_scope_key
from decks.validate import ValidationTally, build_metrics
from parsing.models import ParsedBook, Section, TextBlock
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.scope import list_chapters
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin
from video.repository import create_youtube_video


def _two_chapter_book() -> ParsedBook:
    sections = [
        Section(
            path=["Chapter 1"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(
                    text="The first chapter has enough canonical evidence.",
                    category="NarrativeText",
                    page=1,
                )
            ],
        ),
        Section(
            path=["Chapter 2"],
            level=1,
            start_page=2,
            end_page=2,
            texts=[
                TextBlock(
                    text="The second chapter has different canonical evidence.",
                    category="NarrativeText",
                    page=2,
                )
            ],
        ),
    ]
    return ParsedBook(
        source="sources/books/two-chapters.pdf",
        toc=[(1, "Chapter 1", 1), (1, "Chapter 2", 2)],
        sections=sections,
    )


class DeckSourcePreferenceTests(PostgresOwnerMixin, unittest.TestCase):
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

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _publish_video(self, *, readiness: str = "ready"):
        created = create_youtube_video(
            self.connection,
            owner_id=self.owner_id,
            idempotency_key=uuid4(),
            url="https://youtu.be/abcdefghijk",
            title="Attention lecture",
        )
        self.connection.execute(
            """
            update video.ingestion_versions
            set status = %s, completed_at = now(), published_at = now(),
                quality_gates_json = %s
            where id = %s
            """,
            (
                readiness,
                Jsonb({"readiness": readiness}),
                created.version_id,
            ),
        )
        self.connection.execute(
            """
            update video.videos
            set readiness_status = %s, current_ingestion_version_id = %s,
                ready_at = now()
            where id = %s
            """,
            (readiness, created.version_id, created.video_id),
        )
        return created

    def _store_one_card(self):
        chapter = list_chapters(
            self.connection, owner_id=self.owner_id, book_id=self.book_id
        )[0]
        scope_key = book_scope_key(self.book_id, chapter.id)
        deck_id, _ = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=scope_key,
            title=chapter.title,
            source_title="Sample Book",
            book_id=self.book_id,
            node_id=chapter.id,
        )
        marker = f"[N{chapter.id}:P1]"
        topic = Topic(
            key="chapter-topic",
            ordinal=0,
            label=chapter.title,
            required=True,
            evidence_text=f"Evidence {marker}",
            allowed_markers=frozenset({marker}),
            node_id=chapter.id,
            start_page=1,
            end_page=1,
        )
        card = DeckCard(
            topic_key=topic.key,
            card_index=0,
            card_type="qa",
            front="What is the chapter's core idea?",
            back=CardBack(answer="The grounded idea.", say_it_aloud="The idea."),
            citations=[
                DeckCitation(
                    marker=marker,
                    node_id=chapter.id,
                    page=1,
                )
            ],
            interview_priority=4,
        )
        tally = ValidationTally(generated=1, kept=1)
        tally.keep(card)
        store.store_deck(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
            topics=(topic,),
            generated=GeneratedDeck(
                inventory=None,
                cards=(card,),
                metrics=build_metrics(
                    tally,
                    topics=(topic,),
                    covered_keys={topic.key},
                    repair_attempted=False,
                ),
                model_name="test/model",
                prompt_version="decks-v1:test",
            ),
        )
        return deck_id

    def test_sources_are_enabled_by_default_but_existing_rows_do_not_backfill(self):
        sources = source_preferences.list_sources(
            self.connection, owner_id=self.owner_id
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].source_kind, "book")
        self.assertEqual(sources[0].source_id, str(self.book_id))
        self.assertTrue(sources[0].cards_enabled)
        self.assertFalse(sources[0].automatic_cards_queued)
        self.assertEqual(
            source_preferences.reconcile_missing_initial_sets(self.connection), 0
        )

    def test_paper_toggle_controls_today_without_enabling_automation(self):
        paper_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Paper",
            author=None,
            file_hash="c" * 64,
            page_count=5,
            parser_version="test-v1",
            document_type="paper",
        )
        for enabled in (False, True):
            source_preferences.save_source(
                self.connection,
                owner_id=self.owner_id,
                source_kind="book",
                source_id=paper_id,
                cards_enabled=enabled,
            )

        row = self.connection.execute(
            """
            select cards_enabled, cards_automation_eligible_at
            from public.books where id = %s and owner_id = %s
            """,
            (paper_id, self.owner_id),
        ).fetchone()
        self.assertTrue(row["cards_enabled"])
        self.assertIsNone(row["cards_automation_eligible_at"])
        self.assertEqual(
            source_preferences.enqueue_initial_for_book(
                self.connection, owner_id=self.owner_id, book_id=paper_id
            ),
            [],
        )

    def test_source_updates_are_owner_scoped(self):
        foreign_owner = uuid4()

        self.assertEqual(
            source_preferences.list_sources(
                self.connection, owner_id=foreign_owner
            ),
            [],
        )
        with self.assertRaisesRegex(LookupError, "no such source"):
            source_preferences.save_source(
                self.connection,
                owner_id=foreign_owner,
                source_kind="book",
                source_id=self.book_id,
                cards_enabled=False,
            )

        source = source_preferences.list_sources(
            self.connection, owner_id=self.owner_id
        )[0]
        self.assertTrue(source.cards_enabled)

    def test_disabling_a_source_excludes_only_the_aggregate_queue(self):
        deck_id = self._store_one_card()
        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            source_id=self.book_id,
            cards_enabled=False,
        )

        self.assertEqual(
            store.new_cards(self.connection, owner_id=self.owner_id), []
        )
        explicit = store.new_cards(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
        )
        self.assertEqual(len(explicit), 1)
        self.assertEqual(explicit[0].card.front, "What is the chapter's core idea?")
        self.assertEqual(
            len(
                store.deck_cards(
                    self.connection,
                    owner_id=self.owner_id,
                    deck_id=deck_id,
                )
            ),
            1,
        )

        card_id = explicit[0].card.card_id
        store.record_review(
            self.connection,
            owner_id=self.owner_id,
            card_id=card_id,
            rating=1,
        )
        self.connection.execute(
            """
            update public.deck_card_reviews
            set due_at = now() - interval '1 minute'
            where card_id = %s and owner_id = %s
            """,
            (card_id, self.owner_id),
        )
        self.assertEqual(
            store.due_cards(self.connection, owner_id=self.owner_id), []
        )
        self.assertEqual(
            len(
                store.due_cards(
                    self.connection,
                    owner_id=self.owner_id,
                    deck_id=deck_id,
                )
            ),
            1,
        )

    def test_initial_book_generation_is_once_per_chapter(self):
        second_book = ingest_book(
            self.connection,
            _two_chapter_book(),
            owner_id=self.owner_id,
            title="Two Chapters",
            author=None,
            file_hash="b" * 64,
            page_count=2,
            parser_version="test-v1",
        )
        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            source_id=second_book,
            cards_enabled=True,
        )

        first = source_preferences.enqueue_initial_for_book(
            self.connection, owner_id=self.owner_id, book_id=second_book
        )
        repeated = source_preferences.enqueue_initial_for_book(
            self.connection, owner_id=self.owner_id, book_id=second_book
        )

        chapters = list_chapters(
            self.connection, owner_id=self.owner_id, book_id=second_book
        )
        self.assertEqual(len(first), 2)
        self.assertEqual(repeated, [])
        self.assertEqual(
            {job.scope_key for job in first},
            {
                book_scope_key(second_book, chapter.id)
                for chapter in chapters
            },
        )
        self.assertEqual(
            {job.automatic_key for job in first},
            {f"auto:set1:{job.scope_key}" for job in first},
        )

    def test_disabled_book_and_video_do_not_enqueue(self):
        video = self._publish_video()
        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            source_id=self.book_id,
            cards_enabled=False,
        )
        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="video",
            source_id=video.video_id,
            cards_enabled=False,
        )

        self.assertEqual(
            source_preferences.enqueue_initial_for_book(
                self.connection, owner_id=self.owner_id, book_id=self.book_id
            ),
            [],
        )
        self.assertEqual(
            source_preferences.enqueue_initial_for_video(
                self.connection,
                owner_id=self.owner_id,
                video_id=video.video_id,
            ),
            [],
        )

    def test_disabling_cancels_queued_automation_and_reenable_resumes_it(self):
        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            source_id=self.book_id,
            cards_enabled=True,
        )
        first = source_preferences.enqueue_initial_for_book(
            self.connection, owner_id=self.owner_id, book_id=self.book_id
        )
        self.assertEqual(len(first), 1)

        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            source_id=self.book_id,
            cards_enabled=False,
        )
        cancelled = jobs.get_job(
            self.connection, owner_id=self.owner_id, job_id=first[0].id
        )
        self.assertEqual(cancelled.status, "cancelled")
        self.assertTrue(cancelled.cancellation_requested)

        source_preferences.save_source(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            source_id=self.book_id,
            cards_enabled=True,
        )
        resumed = source_preferences.enqueue_initial_for_book(
            self.connection, owner_id=self.owner_id, book_id=self.book_id
        )
        self.assertEqual([job.id for job in resumed], [first[0].id])
        self.assertEqual(resumed[0].status, "queued")
        self.assertFalse(resumed[0].cancellation_requested)

    def test_reconcile_is_idempotent_for_book_video_and_failed_jobs(self):
        video = self._publish_video(readiness="degraded")
        for source_kind, source_id in (
            ("book", self.book_id),
            ("video", video.video_id),
        ):
            source_preferences.save_source(
                self.connection,
                owner_id=self.owner_id,
                source_kind=source_kind,
                source_id=source_id,
                cards_enabled=True,
            )

        self.assertEqual(
            source_preferences.reconcile_missing_initial_sets(self.connection), 2
        )
        self.assertEqual(
            source_preferences.reconcile_missing_initial_sets(self.connection), 0
        )

        video_job = next(
            job
            for job in jobs.list_jobs(self.connection, owner_id=self.owner_id)
            if job.scope_key == video_scope_key(video.video_id)
        )
        jobs.fail_job(
            self.connection,
            job_id=video_job.id,
            code="source_unavailable",
            retryable=False,
        )

        self.assertEqual(
            source_preferences.reconcile_missing_initial_sets(self.connection), 0
        )
        count = self.connection.execute(
            """
            select count(*) as count from public.deck_jobs
            where owner_id = %s and automatic_key = %s
            """,
            (self.owner_id, f"auto:set1:{video_scope_key(video.video_id)}"),
        ).fetchone()["count"]
        self.assertEqual(count, 1)


class DeckSourcePreferenceEndpointTests(
    PostgresOwnerMixin, unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as database:
            self.book_id = ingest_book(
                database,
                sample_book(),
                owner_id=self.owner_id,
                title="API Book",
                author=None,
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
        app.dependency_overrides[current_owner] = lambda: UUID(self.owner_id)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(current_owner, None)
        self.tearDownPostgresOwner()

    async def _client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def test_source_endpoint_reports_the_default_enabled_setting(self) -> None:
        async with await self._client() as client:
            response = await client.get("/api/decks/sources")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "sources": [
                    {
                        "source_kind": "book",
                        "source_id": str(self.book_id),
                        "title": "API Book",
                        "document_type": "book",
                        "status": "ready",
                        "cards_enabled": True,
                        "automatic_cards_queued": False,
                    }
                ]
            },
        )

    async def test_foreign_source_update_is_not_found(self) -> None:
        app.dependency_overrides[current_owner] = uuid4
        async with await self._client() as client:
            response = await client.patch(
                f"/api/decks/sources/book/{self.book_id}",
                json={"cards_enabled": False},
            )

        self.assertEqual(response.status_code, 404, response.text)
        with database_connection(self.database_url) as database:
            source = source_preferences.list_sources(
                database, owner_id=self.owner_id
            )[0]
        self.assertTrue(source.cards_enabled)

    async def test_malformed_video_source_id_is_not_found(self) -> None:
        async with await self._client() as client:
            response = await client.patch(
                "/api/decks/sources/video/not-a-uuid",
                json={"cards_enabled": False},
            )

        self.assertEqual(response.status_code, 404, response.text)

    async def test_batch_source_update_is_atomic(self) -> None:
        async with await self._client() as client:
            response = await client.patch(
                "/api/decks/sources",
                json={
                    "sources": [
                        {
                            "source_kind": "book",
                            "source_id": str(self.book_id),
                            "cards_enabled": False,
                        },
                        {
                            "source_kind": "book",
                            "source_id": "999999999",
                            "cards_enabled": False,
                        },
                    ]
                },
            )

        self.assertEqual(response.status_code, 404, response.text)
        with database_connection(self.database_url) as database:
            source = source_preferences.list_sources(
                database, owner_id=self.owner_id
            )[0]
        self.assertTrue(source.cards_enabled)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
