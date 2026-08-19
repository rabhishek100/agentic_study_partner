"""Anchor resolution against a real database.

`test_anchors.py` covers the matching rules with a faked connection, which is
where the interesting logic is. This covers the half that cannot be faked: that
the SQL is valid, that the page arithmetic selects the chunks a real chunker
produced, and that a selection made from rendered text finds the canonical
passage it came from.
"""

import unittest

from parsing.models import ParsedBook, Section, TextBlock
from retrieval.models import ChunkingConfig
from retrieval.postgres import rebuild
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.anchors import resolve_document_anchors
from study.contracts import (
    DocumentPageAnchor,
    DocumentPassageAnchor,
    DocumentSectionAnchor,
)
from tests.postgres import PostgresOwnerMixin

FILE_HASH = "c" * 64

PAGE_ONE = (
    "A classifier trained on a lopsided label distribution learns the fastest "
    "shortcut available to it: predict the majority class and be right nearly "
    "always."
)
PAGE_TWO = (
    "The measurement that hides this is accuracy, which a 999-to-1 split "
    "rewards at 99.9% for a model that has learned nothing about the class you "
    "actually care about."
)
PAGE_THREE = (
    "Resampling changes the distribution the model is fitted on, so every "
    "downstream estimate of probability is calibrated to a world that does not "
    "exist."
)


def imbalance_book() -> ParsedBook:
    """Three pages, one section each, with text a selection can be made from."""

    return ParsedBook(
        source="sources/books/imbalance.pdf",
        toc=[
            (1, "4 Training Data", 1),
            (2, "4.3 Class imbalance", 2),
            (2, "4.4 Resampling", 3),
        ],
        sections=[
            Section(
                path=["4 Training Data"],
                level=1,
                start_page=1,
                end_page=1,
                texts=[TextBlock(text=PAGE_ONE, category="NarrativeText", page=1)],
            ),
            Section(
                path=["4 Training Data", "4.3 Class imbalance"],
                level=2,
                start_page=2,
                end_page=2,
                texts=[TextBlock(text=PAGE_TWO, category="NarrativeText", page=2)],
            ),
            Section(
                path=["4 Training Data", "4.4 Resampling"],
                level=2,
                start_page=3,
                end_page=3,
                texts=[TextBlock(text=PAGE_THREE, category="NarrativeText", page=3)],
            ),
        ]
    )


class AnchorResolutionPostgresTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.database = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.database,
            imbalance_book(),
            owner_id=self.owner_id,
            title="Designing Machine Learning Systems",
            author="Test",
            file_hash=FILE_HASH,
            page_count=3,
            parser_version="test-v1",
        )
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=ChunkingConfig(target_tokens=64, max_tokens=128, overlap_tokens=0),
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def resolve(self, anchor):
        (resolved,) = resolve_document_anchors(
            self.database,
            [anchor],
            owner_id=self.owner_id,
        )
        return resolved

    def chunk_text(self, chunk_id: str) -> str:
        return self.database.execute(
            "select text from chunks where id = %s and owner_id = %s",
            (chunk_id, self.owner_id),
        ).fetchone()["text"]

    def test_a_page_resolves_to_the_chunks_actually_on_it(self):
        resolved = self.resolve(
            DocumentPageAnchor(anchor_id="a1", book_id=self.book_id, page=2)
        )

        self.assertTrue(resolved.matched)
        self.assertTrue(resolved.chunk_ids)
        for chunk_id in resolved.chunk_ids:
            self.assertIn("measurement that hides", self.chunk_text(chunk_id))
        self.assertIn("4.3 Class imbalance", resolved.label)

    def test_a_page_past_the_end_matches_nothing(self):
        resolved = self.resolve(
            DocumentPageAnchor(anchor_id="a1", book_id=self.book_id, page=99)
        )

        self.assertFalse(resolved.matched)
        self.assertEqual(resolved.chunk_ids, ())

    def test_a_selection_finds_the_canonical_passage_it_came_from(self):
        resolved = self.resolve(
            DocumentPassageAnchor(
                anchor_id="a1",
                book_id=self.book_id,
                page=2,
                selected_text="The measurement that hides this is accuracy",
            )
        )

        self.assertTrue(resolved.matched)
        self.assertEqual(len(resolved.chunk_ids), 1)
        self.assertIn("999-to-1", self.chunk_text(resolved.chunk_ids[0]))

    def test_a_selection_survives_the_differences_a_render_introduces(self):
        # Case, collapsed whitespace, a line-broken hyphen and a typographic
        # quote are all differences between what a reader sees on a rendered
        # page and what the parser stored.
        resolved = self.resolve(
            DocumentPassageAnchor(
                anchor_id="a1",
                book_id=self.book_id,
                page=2,
                selected_text="the  MEASUREMENT that hides\nthis is accuracy",
            )
        )

        self.assertTrue(resolved.matched)
        self.assertEqual(len(resolved.chunk_ids), 1)

    def test_a_selection_is_scoped_to_the_page_the_reader_is_on(self):
        # The words are in the book, but not on this page. Resolution must not
        # reach for them: the reader is looking at page one.
        resolved = self.resolve(
            DocumentPassageAnchor(
                anchor_id="a1",
                book_id=self.book_id,
                page=1,
                selected_text="The measurement that hides this is accuracy",
            )
        )

        self.assertFalse(resolved.matched)
        # The page still grounds the answer, which is the designed fallback.
        self.assertTrue(resolved.chunk_ids)
        for chunk_id in resolved.chunk_ids:
            self.assertIn("lopsided", self.chunk_text(chunk_id))

    def test_an_unmatched_selection_keeps_the_page_and_admits_it(self):
        resolved = self.resolve(
            DocumentPassageAnchor(
                anchor_id="a1",
                book_id=self.book_id,
                page=3,
                selected_text="Figure 4.6 Label counts before and after resampling",
            )
        )

        self.assertFalse(resolved.matched)
        self.assertTrue(resolved.chunk_ids)

    def test_a_section_resolves_through_its_subtree(self):
        node_id = self.database.execute(
            """
            select id from nodes
            where book_id = %s and owner_id = %s and title = %s
            """,
            (self.book_id, self.owner_id, "4.3 Class imbalance"),
        ).fetchone()["id"]

        resolved = self.resolve(
            DocumentSectionAnchor(
                anchor_id="a1",
                book_id=self.book_id,
                node_id=node_id,
            )
        )

        self.assertTrue(resolved.matched)
        for chunk_id in resolved.chunk_ids:
            self.assertIn("measurement that hides", self.chunk_text(chunk_id))

    def test_another_owners_book_resolves_to_nothing(self):
        from uuid import uuid4

        (resolved,) = resolve_document_anchors(
            self.database,
            [DocumentPageAnchor(anchor_id="a1", book_id=self.book_id, page=2)],
            owner_id=uuid4(),
        )

        self.assertFalse(resolved.matched)
        self.assertEqual(resolved.chunk_ids, ())


if __name__ == "__main__":
    unittest.main()
