"""Resolving a reader's selection in a source to canonical content.

The matching rules are the interesting part and they are pure, so the database
is faked here: what these tests are about is which chunks a page, a selection
or a section names, and what happens when a selection names none of them.
"""

import unittest
from types import SimpleNamespace
from uuid import uuid4

from study.anchors import (
    MAX_CHUNKS_PER_ANCHOR,
    normalise,
    resolve_document_anchors,
)
from study.contracts import (
    DocumentPageAnchor,
    DocumentPassageAnchor,
    DocumentSectionAnchor,
)

OWNER = uuid4()

PAGE_TEXT = (
    "The measurement that hides this is accuracy, which a 999-to-1 split "
    "rewards at 99.9% for a model that has learned nothing about the class "
    "you actually care about."
)


def chunk(identifier: str, text: str, title: str = "4.3 Class imbalance") -> dict:
    return {
        "id": identifier,
        "section_title": title,
        "path_text": f"Training Data > {title}",
        "start_page": 108,
        "end_page": 108,
        "text": text,
    }


class FakeConnection:
    """Answers the two queries `study.anchors` makes, and records them."""

    def __init__(self, page_rows: list[dict], node_rows: list[dict] | None = None):
        self.page_rows = page_rows
        self.node_rows = node_rows or []
        self.parameters: list[tuple] = []

    def execute(self, sql: str, parameters: tuple):
        self.parameters.append(parameters)
        rows = self.node_rows if "source_node_id" in sql else self.page_rows
        return SimpleNamespace(fetchall=lambda: rows)


class NormaliseTests(unittest.TestCase):
    def test_folds_every_difference_between_render_and_parse(self):
        rendered = "The  ﬁrst class-\n imbalance “problem”"
        stored = 'the first class-imbalance "problem"'
        self.assertEqual(normalise(rendered), normalise(stored))

    def test_hyphen_is_not_a_difference_in_either_direction(self):
        self.assertEqual(normalise("dot-product"), normalise("dot- product"))
        self.assertEqual(normalise("dot-product"), normalise("dotproduct"))

    def test_a_dash_between_words_survives_as_punctuation(self):
        # Only a hyphen bound to a word on its left is joined, so an em dash
        # used as punctuation does not silently weld two words together.
        self.assertIn(" - ", normalise("input — output"))


class PageAnchorTests(unittest.TestCase):
    def test_a_page_resolves_to_every_chunk_covering_it(self):
        connection = FakeConnection([chunk("one", PAGE_TEXT), chunk("two", "More.")])
        (resolved,) = resolve_document_anchors(
            connection,
            [DocumentPageAnchor(anchor_id="a1", book_id=7, page=108)],
            owner_id=OWNER,
        )
        self.assertEqual(resolved.chunk_ids, ("one", "two"))
        self.assertTrue(resolved.matched)
        self.assertEqual(resolved.label, "p. 108 · 4.3 Class imbalance")

    def test_a_page_with_no_parsed_text_reports_no_match(self):
        # A full-page figure or an unOCR'd scan. An empty success would read as
        # "grounded on the page" when nothing was.
        connection = FakeConnection([])
        (resolved,) = resolve_document_anchors(
            connection,
            [DocumentPageAnchor(anchor_id="a1", book_id=7, page=402)],
            owner_id=OWNER,
        )
        self.assertEqual(resolved.chunk_ids, ())
        self.assertFalse(resolved.matched)
        self.assertEqual(resolved.label, "p. 402")

    def test_a_crowded_page_is_capped_and_says_so(self):
        connection = FakeConnection(
            [chunk(str(index), f"Passage {index}.") for index in range(6)]
        )
        (resolved,) = resolve_document_anchors(
            connection,
            [DocumentPageAnchor(anchor_id="a1", book_id=7, page=108)],
            owner_id=OWNER,
        )
        self.assertEqual(len(resolved.chunk_ids), MAX_CHUNKS_PER_ANCHOR)
        self.assertTrue(resolved.dropped)


class PassageAnchorTests(unittest.TestCase):
    def passage(self, selected: str) -> DocumentPassageAnchor:
        return DocumentPassageAnchor(
            anchor_id="a1",
            book_id=7,
            page=108,
            selected_text=selected,
        )

    def test_a_selection_resolves_to_the_chunk_containing_it(self):
        connection = FakeConnection(
            [chunk("one", "Unrelated prose."), chunk("two", PAGE_TEXT)]
        )
        (resolved,) = resolve_document_anchors(
            connection,
            [self.passage("The measurement that hides this is accuracy")],
            owner_id=OWNER,
        )
        self.assertEqual(resolved.chunk_ids, ("two",))
        self.assertTrue(resolved.matched)

    def test_a_selection_matches_through_rendering_differences(self):
        connection = FakeConnection([chunk("two", PAGE_TEXT)])
        (resolved,) = resolve_document_anchors(
            connection,
            [self.passage("the  measurement that hides this\nis ACCURACY")],
            owner_id=OWNER,
        )
        self.assertEqual(resolved.chunk_ids, ("two",))

    def test_a_selection_across_a_chunk_boundary_takes_both_ends(self):
        head = "Three responses are available, and they are not interchangeable: " + (
            "resample the data by drawing the majority class down or the "
            "minority class up, which changes what the model sees."
        )
        tail = (
            "Reweight the loss so that a minority error costs more than a "
            "majority one, which changes what an error is worth without "
            "touching the distribution at all."
        )
        connection = FakeConnection([chunk("head", head), chunk("tail", tail)])
        (resolved,) = resolve_document_anchors(
            connection,
            [self.passage(f"{head} {tail}")],
            owner_id=OWNER,
        )
        self.assertEqual(set(resolved.chunk_ids), {"head", "tail"})
        self.assertTrue(resolved.matched)

    def test_an_unmatched_selection_keeps_the_page_and_admits_it(self):
        # Drawn text: a figure caption baked into the image. The words still
        # reach the model, the page still grounds the answer, and the turn
        # records that the selection itself was never found.
        connection = FakeConnection([chunk("one", PAGE_TEXT)])
        (resolved,) = resolve_document_anchors(
            connection,
            [self.passage("Figure 4.6 Label counts before and after resampling")],
            owner_id=OWNER,
        )
        self.assertEqual(resolved.chunk_ids, ("one",))
        self.assertFalse(resolved.matched)
        self.assertEqual(
            resolved.selected_text,
            "Figure 4.6 Label counts before and after resampling",
        )

    def test_resolution_is_scoped_to_the_page_the_reader_is_on(self):
        connection = FakeConnection([chunk("one", PAGE_TEXT)])
        resolve_document_anchors(connection, [self.passage("accuracy")], owner_id=OWNER)
        (parameters,) = connection.parameters
        self.assertEqual(parameters[1:], (7, 108, 108))


class SectionAnchorTests(unittest.TestCase):
    def test_a_section_resolves_through_its_subtree(self):
        connection = FakeConnection([], node_rows=[{"id": "a"}, {"id": "b"}])
        scope = SimpleNamespace(
            book_id=7,
            node_ids=(91, 92),
            display_path="Training Data > 4.3 Class imbalance",
        )
        import study.anchors as anchors_module

        original = anchors_module.resolve_node
        anchors_module.resolve_node = lambda *_, **__: scope
        try:
            (resolved,) = resolve_document_anchors(
                connection,
                [DocumentSectionAnchor(anchor_id="a1", book_id=7, node_id=91)],
                owner_id=OWNER,
            )
        finally:
            anchors_module.resolve_node = original

        self.assertEqual(resolved.chunk_ids, ("a", "b"))
        self.assertEqual(resolved.label, "Training Data > 4.3 Class imbalance")

    def test_a_missing_node_pins_nothing_rather_than_guessing(self):
        from study.scope import ScopeNotFoundError

        import study.anchors as anchors_module

        def missing(*_, **__):
            raise ScopeNotFoundError("scope", 91)

        original = anchors_module.resolve_node
        anchors_module.resolve_node = missing
        try:
            (resolved,) = resolve_document_anchors(
                FakeConnection([]),
                [DocumentSectionAnchor(anchor_id="a1", book_id=7, node_id=91)],
                owner_id=OWNER,
            )
        finally:
            anchors_module.resolve_node = original

        self.assertEqual(resolved.chunk_ids, ())
        self.assertFalse(resolved.matched)


if __name__ == "__main__":
    unittest.main()
