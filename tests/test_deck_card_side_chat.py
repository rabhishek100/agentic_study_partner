"""A card, recorded as the turn a side chat anchors to."""

import unittest
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from decks import store
from decks.contracts import CardBack, DeckCard, DeckCitation, McqOption
from decks.generate import GeneratedDeck
from decks.topics import Topic, book_scope_key
from decks.validate import ValidationTally, build_metrics
from decks.conversation import card_turn_result, render_back, resolve_chunks
from retrieval.postgres import rebuild
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.scope import resolve_chapter
from study.side_context import ParentTurn, build_side_context, resolve_pins
from study.contracts import QuoteAnchor
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


def card(citations: list[DeckCitation], **overrides) -> DeckCard:
    base = {
        "card_id": "00000000-0000-4000-8000-000000000abc",
        "topic_key": "node:1",
        "card_index": 0,
        "card_type": "qa",
        "front": "What is the core idea?",
        "back": CardBack(
            answer="It buffers work.",
            say_it_aloud="Buffering decouples producers from consumers.",
            key_points=["Bounded queues"],
        ),
        "citations": citations,
        "interview_priority": 5,
    }
    return DeckCard(**{**base, **overrides})


class RenderBackTests(unittest.TestCase):
    def test_keeps_citation_markers(self) -> None:
        """Pinning reads the markers out of the highlighted passage."""

        rendered = render_back(
            card(
                [DeckCitation(marker="[N1:P1]", node_id=1, page=1)],
                back=CardBack(answer="Because of the buffer. [N1:P1]"),
            )
        )
        self.assertIn("[N1:P1]", rendered)

    def test_excludes_the_interview_angle(self) -> None:
        """Model knowledge must not launder itself into a turn's answer."""

        rendered = render_back(
            card(
                [DeckCitation(marker="[N1:P1]", node_id=1, page=1)],
                interview_angle="What would you do at ten times the volume?",
            )
        )
        self.assertNotIn("ten times the volume", rendered)

    def test_lays_out_an_mcq_with_its_options(self) -> None:
        rendered = render_back(
            card(
                [DeckCitation(marker="[N1:P1]", node_id=1, page=1)],
                card_type="mcq",
                back=CardBack(
                    answer="The majority label.",
                    options=[
                        McqOption(
                            label=label,
                            text=f"option {label}",
                            correct=label == "A",
                            rationale="because",
                        )
                        for label in ("A", "B", "C", "D")
                    ],
                ),
            )
        )
        for label in ("A", "B", "C", "D"):
            self.assertIn(f"**{label}.**", rendered)

    def test_lays_out_a_design_card_as_structure(self) -> None:
        rendered = render_back(
            card(
                [DeckCitation(marker="[N1:P1]", node_id=1, page=1)],
                card_type="system_design",
                back=CardBack(
                    answer="A queue decouples the halves.",
                    components=["Ingest", "Queue"],
                    data_flow=["Client posts", "Worker drains"],
                    trade_offs=["Latency for durability"],
                ),
            )
        )
        self.assertIn("**Components**", rendered)
        self.assertIn("**Data flow**", rendered)
        self.assertIn("**Trade-offs**", rendered)


class CardTurnTests(PostgresOwnerMixin, unittest.TestCase):
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
        # Chunks are what pinning works in, so the deck's citations have
        # somewhere to resolve to.
        rebuild(self.connection, self.book_id, owner_id=self.owner_id)
        self.scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _citation_for_a_real_page(self) -> DeckCitation:
        row = self.connection.execute(
            """
            select chunks.source_node_id as node_id, chunk_sources.page_number as page
            from public.chunks
            join public.chunk_sources
              on chunk_sources.chunk_id = chunks.id
             and chunk_sources.owner_id = chunks.owner_id
            where chunks.owner_id = %s and chunks.source_book_id = %s
            limit 1
            """,
            (self.owner_id, self.book_id),
        ).fetchone()
        return DeckCitation(
            marker=f"[N{row['node_id']}:P{row['page']}]",
            node_id=row["node_id"],
            page=row["page"],
        )

    def test_a_cited_page_resolves_to_the_chunk_it_lives_in(self) -> None:
        citation = self._citation_for_a_real_page()
        chunks = resolve_chunks(
            self.connection,
            owner_id=self.owner_id,
            book_id=self.book_id,
            citations=[citation],
        )
        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["node_id"], citation.node_id)

    def test_a_page_with_no_chunk_resolves_to_nothing_rather_than_guessing(
        self,
    ) -> None:
        stray = DeckCitation(marker="[N999999:P1]", node_id=999_999, page=1)
        self.assertEqual(
            resolve_chunks(
                self.connection,
                owner_id=self.owner_id,
                book_id=self.book_id,
                citations=[stray],
            ),
            [],
        )

    def test_the_turn_carries_evidence_a_side_chat_can_pin(self) -> None:
        citation = self._citation_for_a_real_page()
        result = card_turn_result(
            self.connection,
            owner_id=self.owner_id,
            card=card([citation]),
            book_id=self.book_id,
            book_title="Sample Book",
            deck_title="Chapter 1",
        )
        self.assertEqual(result.outcome, "answer")
        self.assertTrue(result.evidence)
        self.assertTrue(all(item.chunk_id for item in result.evidence))
        self.assertEqual(result.evidence[0].retrieval_method, "card_citation")
        self.assertEqual(result.warnings, [])

        # The end of the chain: a highlight on this turn pins real chunks.
        turn = ParentTurn.from_result(0, result.question, result.answer, result)
        anchor = QuoteAnchor(
            anchor_id="a1", parent_turn_index=0, quoted_text=result.answer[:120]
        )
        pins = resolve_pins(anchor, turn)
        self.assertTrue(pins)
        self.assertIn(pins[0], {item.chunk_id for item in result.evidence})

        context = build_side_context([anchor], [turn])
        self.assertEqual(list(context.pinned_chunk_ids), list(pins))

    def test_an_unresolvable_card_says_so_instead_of_pretending(self) -> None:
        result = card_turn_result(
            self.connection,
            owner_id=self.owner_id,
            card=card([DeckCitation(marker="[N999999:P1]", node_id=999_999, page=1)]),
            book_id=self.book_id,
            book_title="Sample Book",
            deck_title="Chapter 1",
        )
        self.assertEqual(result.evidence, [])
        self.assertTrue(result.warnings)


class CardSideChatEndpointTests(PostgresOwnerMixin, unittest.IsolatedAsyncioTestCase):
    """The endpoint, against a real database, through the real deck store."""

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
        rebuild(self.connection, self.book_id, owner_id=self.owner_id)
        self.scope = resolve_chapter(
            self.connection, 1, owner_id=self.owner_id, book_id=self.book_id
        )
        self.deck_id, self.card_id = self._store_a_deck()
        # The endpoint borrows its own connection from the pool, so the setup
        # has to be visible outside this test's transaction.
        self.connection.commit()
        app.dependency_overrides[current_owner] = lambda: UUID(self.owner_id)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(current_owner, None)
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _store_a_deck(self) -> tuple[str, str]:
        row = self.connection.execute(
            """
            select chunks.source_node_id as node_id, chunk_sources.page_number as page
            from public.chunks
            join public.chunk_sources
              on chunk_sources.chunk_id = chunks.id
             and chunk_sources.owner_id = chunks.owner_id
            where chunks.owner_id = %s and chunks.source_book_id = %s
            limit 1
            """,
            (self.owner_id, self.book_id),
        ).fetchone()
        citation = DeckCitation(
            marker=f"[N{row['node_id']}:P{row['page']}]",
            node_id=row["node_id"],
            page=row["page"],
        )
        deck_id, _ = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=book_scope_key(self.book_id, self.scope.root_node_id),
            title="Chapter 1",
            source_title="Sample Book",
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )
        topics = (
            Topic(
                key="node:a",
                ordinal=0,
                label="Chapter 1",
                required=True,
                evidence_text="evidence",
                allowed_markers=frozenset({citation.marker}),
                node_id=row["node_id"],
            ),
        )
        cards = (
            card([citation], card_id=None, topic_key="node:a", card_index=0),
        )
        tally = ValidationTally(generated=1, kept=1)
        tally.keep(cards[0])
        store.store_deck(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
            topics=topics,
            generated=GeneratedDeck(
                inventory=None,
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
        stored = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        return str(deck_id), stored[0].card.card_id

    async def _client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def test_highlighting_a_card_opens_an_anchored_side_chat(self) -> None:
        async with await self._client() as client:
            response = await client.post(
                f"/api/decks/cards/{self.card_id}/side-chats",
                json={"quoted_text": "Buffering decouples producers"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["anchors"]), 1)
        self.assertEqual(payload["turn_count"], 0)
        self.assertTrue(payload["parent_conversation_id"])

        # The card is now a real turn of the deck's conversation, which is what
        # the existing streaming endpoint resolves pins against.
        turn = self.connection.execute(
            """
            select turn_index, question, result_json
            from public.conversation_turns
            where conversation_id = %s and deck_card_id = %s
            """,
            (UUID(payload["parent_conversation_id"]), UUID(self.card_id)),
        ).fetchone()
        self.assertIsNotNone(turn)
        self.assertEqual(turn["question"], "What is the core idea?")
        self.assertTrue(turn["result_json"]["evidence"])
        self.assertEqual(
            payload["anchors"][0]["parent_turn_index"], turn["turn_index"]
        )

    async def test_a_second_highlight_reuses_the_card_turn(self) -> None:
        async with await self._client() as client:
            first = await client.post(
                f"/api/decks/cards/{self.card_id}/side-chats",
                json={"quoted_text": "Buffering decouples producers"},
            )
            second = await client.post(
                f"/api/decks/cards/{self.card_id}/side-chats",
                json={"quoted_text": "Bounded queues"},
            )
        self.assertEqual(second.status_code, 200, second.text)
        # Two side chats, one parent conversation, one recorded card turn.
        self.assertNotEqual(
            first.json()["conversation_id"], second.json()["conversation_id"]
        )
        self.assertEqual(
            first.json()["parent_conversation_id"],
            second.json()["parent_conversation_id"],
        )
        count = self.connection.execute(
            "select count(*) as n from public.conversation_turns where deck_card_id = %s",
            (UUID(self.card_id),),
        ).fetchone()
        self.assertEqual(count["n"], 1)

    async def test_the_deck_conversation_is_created_once(self) -> None:
        async with await self._client() as client:
            first = await client.get(f"/api/decks/{self.deck_id}/conversation")
            second = await client.get(f"/api/decks/{self.deck_id}/conversation")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(
            first.json()["conversation_id"], second.json()["conversation_id"]
        )

    async def test_two_simultaneous_openings_share_one_deck_conversation(
        self,
    ) -> None:
        """Opening a side chat fires the lookup and the highlight together.

        Both create the deck's conversation on a first highlight, and the
        loser of that race used to violate the one-per-deck index and 500.
        """

        import asyncio

        async with await self._client() as client:
            lookup, opened = await asyncio.gather(
                client.get(f"/api/decks/{self.deck_id}/conversation"),
                client.post(
                    f"/api/decks/cards/{self.card_id}/side-chats",
                    json={"quoted_text": "Buffering decouples producers"},
                ),
            )
        self.assertEqual(lookup.status_code, 200, lookup.text)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(
            lookup.json()["conversation_id"],
            opened.json()["parent_conversation_id"],
        )
        count = self.connection.execute(
            "select count(*) as n from public.conversations where deck_id = %s",
            (UUID(self.deck_id),),
        ).fetchone()
        self.assertEqual(count["n"], 1)

    async def test_an_unknown_card_is_rejected(self) -> None:
        async with await self._client() as client:
            response = await client.post(
                "/api/decks/cards/00000000-0000-4000-8000-00000000dead/side-chats",
                json={"quoted_text": "anything"},
            )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
