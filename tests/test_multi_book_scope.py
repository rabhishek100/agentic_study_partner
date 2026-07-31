"""Cross-book chat: scope filtering, and clarification instead of guessing."""

import unittest

from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.contracts import ConversationState
from study.conversation import _select_state, new_conversation_state
from study.request import parse_study_request, resolve_study_request
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

    def test_naming_the_book_inline_resolves_a_shared_chapter_number(self):
        """"summarize chapter 1 of <book>" is unambiguous even when every
        selected book has a chapter 1."""

        request = parse_study_request("summarize chapter 1 of Second Book")
        self.assertEqual(request.scope_reference, "1")
        self.assertEqual(request.book_reference, "Second")
        with database_connection(self.database_url, readonly=True) as connection:
            scope = resolve_study_request(
                connection,
                request,
                owner_id=self.owner_id,
                book_ids=[self.first_book_id, self.second_book_id],
            )
        self.assertEqual(scope.book_id, self.second_book_id)

    def test_an_inline_book_reference_cannot_escape_the_selection(self):
        """Naming a book narrows the turn; it must not widen it past what the
        reader selected for the conversation."""

        request = parse_study_request("summarize chapter 1 of Second Book")
        with database_connection(self.database_url, readonly=True) as connection:
            with self.assertRaises(ScopeNotFoundError):
                resolve_study_request(
                    connection,
                    request,
                    owner_id=self.owner_id,
                    book_ids=[self.first_book_id],
                )

    def test_a_book_resolves_from_its_initials(self):
        """Readers say "ddia", not the full title. Two letters is too little
        signal to spend on this, so only three or more are accepted."""

        from study.scope import _title_acronyms

        self.assertIn("ddia", _title_acronyms("Designing Data-Intensive Applications"))
        self.assertIn("dmls", _title_acronyms("Designing Machine Learning Systems"))
        self.assertIn(
            "isl", _title_acronyms("An Introduction to Statistical Learning")
        )
        self.assertEqual(_title_acronyms("Second Book"), set())

        with database_connection(self.database_url) as connection:
            third_book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Designing Data-Intensive Applications",
                author="Author Three",
                file_hash="c" * 64,
                page_count=5,
                parser_version="test-v1",
            )
        request = parse_study_request("summarize chapter 1 of ddia")
        with database_connection(self.database_url, readonly=True) as connection:
            scope = resolve_study_request(
                connection,
                request,
                owner_id=self.owner_id,
                book_ids=[self.first_book_id, self.second_book_id, third_book_id],
            )
        self.assertEqual(scope.book_id, third_book_id)

    def test_a_title_match_beats_an_acronym(self):
        """An abbreviation is the last thing tried, so it can never take a
        book that a real title fragment already names."""

        with database_connection(self.database_url, readonly=True) as connection:
            scope = resolve_book(
                connection,
                "First",
                owner_id=self.owner_id,
                book_ids=[self.first_book_id, self.second_book_id],
            )
        self.assertEqual(scope.book_id, self.first_book_id)

    def test_a_bare_follow_up_stays_in_the_book_just_discussed(self):
        """"summarize chapter 1" after asking about one book is ambiguous to
        the resolver and obvious to the reader. The last resolved scope's book
        is the fallback, tried only after the conversation's own selection."""

        from study.analyze import _explicit_hierarchy_decision
        from study.contracts import ScopeRef

        state = new_conversation_state(
            book_ids=[self.first_book_id, self.second_book_id]
        )
        # Nothing to inherit yet: the selection alone cannot decide.
        self.assertIsNone(
            _explicit_hierarchy_decision(
                "summarize chapter 1",
                state,
                self.database_url,
                owner_id=self.owner_id,
            )
        )

        state.active_scope = ScopeRef(
            kind="book",
            book_id=self.second_book_id,
            node_id=None,
            display_path="Second Book",
            start_page=1,
            end_page=5,
        )
        decision = _explicit_hierarchy_decision(
            "summarize chapter 1",
            state,
            self.database_url,
            owner_id=self.owner_id,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.resolved_scope.book_id, self.second_book_id)

    def test_an_explicit_reference_outranks_the_inherited_book(self):
        from study.analyze import _explicit_hierarchy_decision
        from study.contracts import ScopeRef

        state = new_conversation_state(
            book_ids=[self.first_book_id, self.second_book_id]
        )
        state.active_scope = ScopeRef(
            kind="book",
            book_id=self.second_book_id,
            node_id=None,
            display_path="Second Book",
            start_page=1,
            end_page=5,
        )
        decision = _explicit_hierarchy_decision(
            "summarize chapter 1 of First Book",
            state,
            self.database_url,
            owner_id=self.owner_id,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.resolved_scope.book_id, self.first_book_id)

    def test_execution_keeps_the_book_the_decision_resolved(self):
        """The executor renders a resolved scope back into a sentence and
        re-resolves it. That sentence names no book, so resolving it against
        the whole selection reported "book reference None is ambiguous" for a
        chapter the analyser had already pinned to one book.
        """

        from unittest.mock import patch
        from study.contracts import ScopeRef, TurnDecision, TurnResult
        from study.conversation import execute_decision

        state = new_conversation_state(
            book_ids=[self.first_book_id, self.second_book_id]
        )
        decision = TurnDecision(
            route="hierarchy_summary",
            history_dependency="dependent",
            resolved_scope=ScopeRef(
                kind="chapter",
                book_id=self.second_book_id,
                node_id=None,
                display_path="Chapter 1",
                start_page=1,
                end_page=5,
            ),
            reason="test",
        )
        answer = TurnResult(
            question="",
            answer="a",
            route="hierarchy_summary",
            history_dependency="dependent",
            outcome="answer",
        )
        with patch(
            "study.conversation.execute_query", return_value=answer
        ) as execute:
            execute_decision(
                "summarize chapter 1",
                decision,
                state,
                database_url=self.database_url,
                owner_id=self.owner_id,
                retrieval_mode="hybrid",
                model=None,
            )
        self.assertEqual(
            execute.call_args.kwargs["book_ids"], (self.second_book_id,)
        )

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
