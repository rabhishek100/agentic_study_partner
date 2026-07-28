"""Cross-book chat: scope filtering, and clarification instead of guessing."""

import unittest

from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.contracts import ConversationState
from study.conversation import _select_state, new_conversation_state
from study.scope import (
    AmbiguousScopeError,
    ScopeNotFoundError,
    resolve_book,
    resolve_chapter,
)
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class MultiBookScopeTests(PostgresOwnerMixin, unittest.TestCase):
    """Two books that both contain a "Chapter 1"."""

    def setUp(self) -> None:
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
            self.first_book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="First Book",
                author="Author One",
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
            self.second_book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Second Book",
                author="Author Two",
                file_hash="b" * 64,
                page_count=5,
                parser_version="test-v1",
            )

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    # --- scope filtering -------------------------------------------------

    def test_a_single_book_selection_resolves_without_ambiguity(self):
        with database_connection(self.database_url, readonly=True) as connection:
            scope = resolve_chapter(
                connection,
                "Chapter 1",
                owner_id=self.owner_id,
                book_ids=[self.second_book_id],
            )
        self.assertEqual(scope.book_id, self.second_book_id)
        self.assertEqual(scope.book_title, "Second Book")

    def test_a_chapter_in_several_selected_books_is_ambiguous(self):
        """The turn analyser turns this into a question rather than a guess."""

        with database_connection(self.database_url, readonly=True) as connection:
            with self.assertRaises(AmbiguousScopeError) as raised:
                resolve_chapter(
                    connection,
                    "Chapter 1",
                    owner_id=self.owner_id,
                    book_ids=[self.first_book_id, self.second_book_id],
                )

        books = {candidate.book_id for candidate in raised.exception.candidates}
        self.assertEqual(books, {self.first_book_id, self.second_book_id})

    def test_book_ids_narrows_which_books_can_be_resolved(self):
        with database_connection(self.database_url, readonly=True) as connection:
            scope = resolve_book(
                connection,
                owner_id=self.owner_id,
                book_ids=[self.first_book_id],
            )
        self.assertEqual(scope.book_id, self.first_book_id)

    def test_an_unselected_book_is_out_of_scope(self):
        with database_connection(self.database_url, readonly=True) as connection:
            with self.assertRaises(ScopeNotFoundError):
                resolve_book(
                    connection,
                    "Second Book",
                    owner_id=self.owner_id,
                    book_ids=[self.first_book_id],
                )

    def test_an_empty_selection_is_rejected_rather_than_widened(self):
        """An empty list must never be read as "every book"."""

        with database_connection(self.database_url, readonly=True) as connection:
            with self.assertRaises(ValueError):
                resolve_book(connection, owner_id=self.owner_id, book_ids=[])

    # --- conversation scope ----------------------------------------------

    def test_changing_the_selection_starts_a_new_conversation(self):
        existing = ConversationState(
            conversation_id="old",
            book_ids=[self.first_book_id],
        )
        selected = _select_state(existing, [self.second_book_id])

        self.assertNotEqual(selected.conversation_id, "old")
        self.assertEqual(selected.book_ids, [self.second_book_id])

    def test_the_same_selection_in_any_order_continues_the_conversation(self):
        existing = ConversationState(
            conversation_id="keep",
            book_ids=[self.first_book_id, self.second_book_id],
        )
        selected = _select_state(
            existing,
            [self.second_book_id, self.first_book_id],
        )
        self.assertEqual(selected.conversation_id, "keep")

    def test_a_new_state_normalizes_duplicates_and_order(self):
        state = new_conversation_state(
            book_ids=[self.second_book_id, self.first_book_id, self.second_book_id],
        )
        self.assertEqual(
            state.book_ids,
            sorted([self.first_book_id, self.second_book_id]),
        )


if __name__ == "__main__":
    unittest.main()
