"""Tests for book-extracted questions deck generation, storage, and API contracts."""

import unittest
from unittest.mock import MagicMock, patch

from decks import jobs, store
from decks.contracts import CardBack, DeckCard, DeckCitation
from decks.extraction import ExtractedQuestion, ExtractedQuestionList, extract_and_generate_deck
from decks.generate import GeneratedDeck
from decks.topics import ScopeInventory, Topic, book_scope_key
from decks.validate import ValidationTally, build_metrics
from storage.database import close_pools, connection as database_connection
from storage.postgres import ingest_book
from study.content import load_scope_content
from study.scope import resolve_chapter
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


def sample_topic(key: str, *, ordinal: int, node_id: int) -> Topic:
    return Topic(
        key=key,
        ordinal=ordinal,
        label=f"Chapter 1 > {key}",
        required=True,
        evidence_text=f"[N{node_id}:P1] Sample chapter text with Question 1: What is RAG? Answer: RAG stands for Retrieval-Augmented Generation.",
        allowed_markers=frozenset({f"[N{node_id}:P1]"}),
        node_id=node_id,
        start_page=1,
        end_page=2,
    )


class DeckExtractionTests(PostgresOwnerMixin, unittest.TestCase):
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
            self.connection,
            self.book_id,
            chapter_title="Chapter 1",
            owner_id=self.owner_id,
        )

    def tearDown(self) -> None:
        if hasattr(self, "database_context"):
            self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def test_book_scope_key_by_generation_mode(self) -> None:
        key_topic = book_scope_key(10, 20, generation_mode="topic_generated")
        key_extracted = book_scope_key(10, 20, generation_mode="book_extracted")
        self.assertEqual(key_topic, "book:10:node:20")
        self.assertEqual(key_extracted, "book:10:node:20:mode:book_extracted")

    def test_enqueue_and_store_book_extracted_deck(self) -> None:
        scope_key = book_scope_key(self.book_id, self.scope.root.id, generation_mode="book_extracted")
        job = jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=scope_key,
            generation_mode="book_extracted",
            book_id=self.book_id,
            node_id=self.scope.root.id,
        )
        self.assertEqual(job.generation_mode, "book_extracted")

        deck_id, version = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=scope_key,
            title="Chapter 1 Extracted Questions",
            source_title="Sample Book",
            generation_mode="book_extracted",
            book_id=self.book_id,
            node_id=self.scope.root.id,
        )

        t = sample_topic("topic-1", ordinal=0, node_id=self.scope.root.id)
        card = DeckCard(
            topic_key="topic-1",
            card_index=0,
            card_type="qa",
            front="What is RAG?",
            back=CardBack(answer="Retrieval-Augmented Generation", say_it_aloud="RAG is Retrieval-Augmented Generation"),
            citations=[DeckCitation(marker=f"[N{self.scope.root.id}:P1]", node_id=self.scope.root.id, page=1)],
            interview_priority=3,
            answer_source="printed_in_book",
        )
        tally = ValidationTally()
        tally.generated = 1
        tally.keep(card)
        metrics = build_metrics(ScopeInventory(scope_key=scope_key, title="Ch1", source_title="Book", topics=(t,)), tally, [card])

        generated = GeneratedDeck(
            inventory=ScopeInventory(scope_key=scope_key, title="Ch1", source_title="Book", topics=(t,)),
            cards=(card,),
            metrics=metrics,
            model_name="test-model",
            prompt_version="v1_book_extracted",
        )

        store.store_deck(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
            topics=(t,),
            generated=generated,
        )

        summary = store.get_deck(self.connection, owner_id=self.owner_id, deck_id=deck_id)
        self.assertEqual(summary.generation_mode, "book_extracted")

        cards = store.deck_cards(self.connection, owner_id=self.owner_id, deck_id=deck_id)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].card.answer_source, "printed_in_book")

    @patch("decks.extraction._question_extraction_model")
    def test_extract_and_generate_deck_printed_answer(self, mock_extractor_builder) -> None:
        node_id = self.scope.root.id
        t = sample_topic("topic-1", ordinal=0, node_id=node_id)
        inventory = ScopeInventory(scope_key="test-key", title="Ch1", source_title="Book", topics=(t,))

        mock_extractor = MagicMock()
        mock_extractor.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="What is RAG?",
                    printed_answer="Retrieval-Augmented Generation",
                    citation_marker=f"[N{node_id}:P1]",
                )
            ]
        )
        mock_extractor_builder.return_value = mock_extractor

        generated = extract_and_generate_deck(inventory, connection=self.connection, owner_id=str(self.owner_id))
        self.assertEqual(len(generated.cards), 1)
        self.assertEqual(generated.cards[0].front, "What is RAG?")
        self.assertEqual(generated.cards[0].answer_source, "printed_in_book")

    @patch("decks.extraction._question_extraction_model")
    def test_extract_and_generate_deck_no_questions_found(self, mock_extractor_builder) -> None:
        t = sample_topic("topic-1", ordinal=0, node_id=self.scope.root.id)
        inventory = ScopeInventory(scope_key="test-key", title="Ch1", source_title="Book", topics=(t,))

        mock_extractor = MagicMock()
        mock_extractor.invoke.return_value = ExtractedQuestionList(questions=[])
        mock_extractor_builder.return_value = mock_extractor

        generated = extract_and_generate_deck(inventory, connection=self.connection, owner_id=str(self.owner_id))
        self.assertEqual(len(generated.cards), 0)
        self.assertEqual(generated.metrics.notice, "No printed questions found in this chapter")


if __name__ == "__main__":
    unittest.main()
