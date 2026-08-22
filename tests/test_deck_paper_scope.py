"""Whole-paper card scopes use every canonical section as one deck."""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from decks import jobs, source_preferences, store
from decks.contracts import CardBack, DeckCard, DeckCitation
from decks.generate import GeneratedDeck
from decks.pipeline import DeckSourceError, load_inventory
from decks.validate import ValidationTally, build_metrics
from parsing.models import ParsedBook, Section, TextBlock
from storage.database import connection as database_connection
from storage.postgres import delete_book, ingest_book
from tests.fixtures import sample_book
from tests.postgres import PostgresOwnerMixin


def multi_root_paper() -> ParsedBook:
    """A native paper outline: sections and subsections, never chapters."""

    sections = [
        Section(
            path=["Introduction"],
            level=1,
            start_page=1,
            end_page=1,
            texts=[
                TextBlock(
                    text="The paper introduces a grounded research question. " * 14,
                    category="NarrativeText",
                    page=1,
                )
            ],
        ),
        Section(
            path=["Introduction", "Motivation"],
            level=2,
            start_page=2,
            end_page=2,
            texts=[
                TextBlock(
                    text="The motivation identifies the measured failure mode. " * 14,
                    category="NarrativeText",
                    page=2,
                )
            ],
        ),
        Section(
            path=["Method"],
            level=1,
            start_page=3,
            end_page=3,
            texts=[
                TextBlock(
                    text="The method specifies the experimental procedure. " * 14,
                    category="NarrativeText",
                    page=3,
                )
            ],
        ),
        Section(
            path=["Results"],
            level=1,
            start_page=4,
            end_page=4,
            texts=[
                TextBlock(
                    text="The results report the supported comparison and limits. " * 14,
                    category="NarrativeText",
                    page=4,
                )
            ],
        ),
    ]
    return ParsedBook(
        source="sources/papers/multi-root.pdf",
        toc=[
            (1, "Introduction", 1),
            (2, "Motivation", 2),
            (1, "Method", 3),
            (1, "Results", 4),
        ],
        sections=sections,
    )


def ingest_paper(
    connection,
    *,
    owner_id: str | UUID,
    title: str = "Grounded Systems Paper",
    file_hash: str = "c" * 64,
) -> int:
    return ingest_book(
        connection,
        multi_root_paper(),
        owner_id=owner_id,
        title=title,
        author="Researcher",
        file_hash=file_hash,
        page_count=4,
        parser_version="test-v1",
        document_type="paper",
    )


class WholePaperDeckTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.paper_id = ingest_paper(
            self.connection,
            owner_id=self.owner_id,
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def test_full_paper_inventory_contains_every_root_and_subsection(self) -> None:
        inventory, version = load_inventory(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            book_id=self.paper_id,
            node_id=None,
        )

        self.assertIsNone(version)
        self.assertEqual(inventory.scope_key, f"paper:{self.paper_id}")
        self.assertEqual(inventory.title, "Full paper")
        self.assertEqual(inventory.source_title, "Grounded Systems Paper")
        self.assertEqual(
            [topic.label for topic in inventory.topics],
            [
                "Introduction",
                "Introduction :: Motivation",
                "Method",
                "Results",
            ],
        )
        self.assertEqual(
            {topic.start_page for topic in inventory.topics},
            {1, 2, 3, 4},
        )
        self.assertTrue(all(topic.node_id is not None for topic in inventory.topics))
        for topic in inventory.topics:
            self.assertTrue(topic.allowed_markers)
            self.assertTrue(
                all(marker in topic.evidence_text for marker in topic.allowed_markers)
            )

    def test_an_ordinary_book_cannot_use_a_null_node_scope(self) -> None:
        book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Ordinary Book",
            author=None,
            file_hash="d" * 64,
            page_count=5,
            parser_version="test-v1",
        )

        with self.assertRaisesRegex(DeckSourceError, "paper|chapter|section"):
            load_inventory(
                self.connection,
                owner_id=self.owner_id,
                source_kind="book",
                book_id=book_id,
                node_id=None,
            )

    def test_a_paper_deck_stores_with_no_root_node(self) -> None:
        inventory, _ = load_inventory(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            book_id=self.paper_id,
            node_id=None,
        )
        topic = inventory.topics[0]
        marker = sorted(topic.allowed_markers)[0]
        deck_id, version = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=inventory.scope_key,
            title=inventory.title,
            source_title=inventory.source_title,
            book_id=self.paper_id,
            node_id=None,
        )
        card = DeckCard(
            topic_key=topic.key,
            card_index=0,
            card_type="qa",
            front="What research question does the paper introduce?",
            back=CardBack(
                answer=f"It states the grounded research question. {marker}",
                say_it_aloud="It states the research question.",
            ),
            citations=[
                DeckCitation(
                    marker=marker,
                    node_id=topic.node_id,
                    page=topic.start_page,
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
            topics=inventory.topics,
            generated=GeneratedDeck(
                inventory=inventory,
                cards=(card,),
                metrics=build_metrics(
                    tally,
                    topics=inventory.topics,
                    covered_keys={topic.key},
                    repair_attempted=False,
                ),
                model_name="test/model",
                prompt_version="decks-v1:test",
            ),
        )

        summary = store.get_deck(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        self.assertEqual(version, 1)
        self.assertEqual(summary.scope_key, f"paper:{self.paper_id}")
        self.assertEqual(summary.book_id, self.paper_id)
        self.assertIsNone(summary.node_id)
        self.assertEqual(summary.set_number, 1)
        self.assertEqual(summary.title, "Full paper")

    def test_automatic_paper_set_is_idempotent(self) -> None:
        self.connection.execute(
            """
            update public.books
            set cards_enabled = true, cards_automation_eligible_at = now()
            where id = %s and owner_id = %s
            """,
            (self.paper_id, self.owner_id),
        )

        first = source_preferences.enqueue_initial_for_book(
            self.connection,
            owner_id=self.owner_id,
            book_id=self.paper_id,
        )
        repeated = source_preferences.enqueue_initial_for_book(
            self.connection,
            owner_id=self.owner_id,
            book_id=self.paper_id,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(repeated, [])
        self.assertEqual(first[0].scope_key, f"paper:{self.paper_id}")
        self.assertEqual(first[0].automatic_key, f"auto:set1:paper:{self.paper_id}")
        self.assertIsNone(first[0].node_id)
        self.assertEqual(
            source_preferences.reconcile_missing_initial_sets(self.connection), 0
        )
        count = self.connection.execute(
            """
            select count(*) as count from public.deck_jobs
            where owner_id = %s and scope_key = %s
            """,
            (self.owner_id, f"paper:{self.paper_id}"),
        ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_deleting_a_paper_cascades_its_deck_and_job(self) -> None:
        inventory, _ = load_inventory(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            book_id=self.paper_id,
            node_id=None,
        )
        deck_id, _ = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=inventory.scope_key,
            title=inventory.title,
            source_title=inventory.source_title,
            book_id=self.paper_id,
            node_id=None,
        )
        job = jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=inventory.scope_key,
            book_id=self.paper_id,
            node_id=None,
            automatic_key=f"auto:set1:{inventory.scope_key}",
        )

        self.assertTrue(
            delete_book(
                self.connection,
                self.paper_id,
                owner_id=self.owner_id,
            )
        )
        deck_count = self.connection.execute(
            "select count(*) as count from public.decks where id = %s",
            (deck_id,),
        ).fetchone()["count"]
        job_count = self.connection.execute(
            "select count(*) as count from public.deck_jobs where id = %s",
            (job.id,),
        ).fetchone()["count"]
        self.assertEqual(deck_count, 0)
        self.assertEqual(job_count, 0)


class WholePaperDeckEndpointTests(
    PostgresOwnerMixin, unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.foreign_owner = uuid4()
        with database_connection(self.database_url) as database:
            database.execute(
                """
                insert into auth.users (id, email, raw_user_meta_data)
                values (%s, %s, '{}'::jsonb)
                """,
                (self.foreign_owner, f"{self.foreign_owner}@test.local"),
            )
            self.paper_id = ingest_paper(
                database,
                owner_id=self.owner_id,
            )
            self.foreign_paper_id = ingest_paper(
                database,
                owner_id=self.foreign_owner,
                title="Foreign Paper",
                file_hash="e" * 64,
            )
        app.dependency_overrides[current_owner] = lambda: UUID(self.owner_id)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(current_owner, None)
        with database_connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s",
                (self.foreign_owner,),
            )
        self.tearDownPostgresOwner()

    async def _client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def test_a_full_paper_generation_request_uses_the_paper_scope(self) -> None:
        async with await self._client() as client:
            response = await client.post(
                "/api/decks",
                json={
                    "source_kind": "book",
                    "book_id": self.paper_id,
                    "generation_mode": "topic_generated",
                },
            )

        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        self.assertEqual(payload["scope_key"], f"paper:{self.paper_id}")
        self.assertEqual(payload["book_id"], self.paper_id)
        self.assertIsNone(payload["node_id"])

    async def test_a_foreign_paper_is_not_found(self) -> None:
        async with await self._client() as client:
            response = await client.post(
                "/api/decks",
                json={
                    "source_kind": "book",
                    "book_id": self.foreign_paper_id,
                    "generation_mode": "topic_generated",
                },
            )

        self.assertEqual(response.status_code, 404, response.text)

    async def test_a_paper_must_be_ready_before_generation(self) -> None:
        with database_connection(self.database_url) as database:
            database.execute(
                """
                update public.books
                set status = 'processing', ready_at = null
                where id = %s and owner_id = %s
                """,
                (self.paper_id, self.owner_id),
            )

        async with await self._client() as client:
            response = await client.post(
                "/api/decks",
                json={
                    "source_kind": "book",
                    "book_id": self.paper_id,
                    "generation_mode": "topic_generated",
                },
            )

        self.assertEqual(response.status_code, 404, response.text)

    async def test_book_extracted_mode_is_rejected_for_a_whole_paper(self) -> None:
        async with await self._client() as client:
            response = await client.post(
                "/api/decks",
                json={
                    "source_kind": "book",
                    "book_id": self.paper_id,
                    "generation_mode": "book_extracted",
                },
            )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("require a chapter", response.text.casefold())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
