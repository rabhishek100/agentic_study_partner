"""Deterministic figure selection from an answer's cited evidence."""

import unittest

from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.contracts import CitationRef, EvidenceRef, FigureRef
from study.figures import apply_limit, select_figures
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class FigureSelectionTests(PostgresOwnerMixin, unittest.TestCase):
    """`sample_book` has one image, on page 3, in Chapter 1 :: Core idea :: Diagram."""

    def setUp(self) -> None:
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
            self.book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Sample Book",
                author=None,
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
            self.nodes = {
                row["path_text"]: row["id"]
                for row in connection.execute(
                    "select id, path_text from nodes where book_id = %s",
                    (self.book_id,),
                ).fetchall()
            }
        self.diagram_node = self.nodes["Chapter 1 :: Core idea :: Diagram"]

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def select(self, evidence, citations=()):
        with database_connection(self.database_url, readonly=True) as connection:
            return select_figures(
                connection,
                owner_id=self.owner_id,
                evidence=evidence,
                citations=citations,
            )

    def evidence(self, node_id: int, pages: list[int], **overrides) -> EvidenceRef:
        return EvidenceRef(
            node_id=node_id,
            pages=pages,
            path="Chapter 1",
            book_id=self.book_id,
            book_title="Sample Book",
            **overrides,
        )

    def test_a_figure_inside_cited_evidence_is_selected(self):
        figures = self.select([self.evidence(self.diagram_node, [3])])

        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0].page, 3)
        self.assertEqual(figures[0].node_id, self.diagram_node)
        self.assertEqual(figures[0].book_id, self.book_id)
        self.assertEqual(figures[0].mime_type, "image/png")
        self.assertEqual(figures[0].path, "Chapter 1 :: Core idea :: Diagram")

    def test_the_payload_is_never_inlined(self):
        """A result carrying base64 would balloon every response and turn row."""

        figures = self.select([self.evidence(self.diagram_node, [3])])
        serialized = figures[0].model_dump()
        self.assertNotIn("base64_content", serialized)
        self.assertEqual(
            set(serialized),
            {
                "book_id",
                "node_id",
                "block_id",
                "page",
                "mime_type",
                "path",
                "caption",
                "evidence_rank",
            },
        )

    def test_a_node_cited_on_another_page_selects_nothing(self):
        # The evidence is the right node but the figure is not on that page.
        self.assertEqual(self.select([self.evidence(self.diagram_node, [4])]), [])

    def test_a_different_node_selects_nothing(self):
        other = self.nodes["Chapter 1 :: Core idea"]
        self.assertEqual(self.select([self.evidence(other, [3])]), [])

    def test_citations_alone_can_surface_a_figure(self):
        """A summary records per-page citations rather than evidence ranks."""

        figures = self.select(
            [],
            [
                CitationRef(
                    marker=f"[N{self.diagram_node}:P3]",
                    node_id=self.diagram_node,
                    page=3,
                    book_id=self.book_id,
                )
            ],
        )
        self.assertEqual(len(figures), 1)

    def test_uncited_evidence_does_not_surface_figures(self):
        """Retrieval returns candidates the answer never referred to.

        Selecting from all of them put four figures under an answer that
        cited one source, from pages the reader was never pointed at.
        """

        other = self.nodes["Chapter 1 :: Core idea"]
        figures = self.select(
            [
                # Cited: a different node, with no figure of its own.
                self.evidence(other, [2], rank=1),
                # Merely retrieved: the node that holds the figure.
                self.evidence(self.diagram_node, [3], rank=2),
            ],
            [
                CitationRef(
                    marker="[S1]",
                    node_id=other,
                    page=2,
                    book_id=self.book_id,
                    evidence_rank=1,
                )
            ],
        )
        self.assertEqual(figures, [])

    def test_a_cited_nodes_full_evidence_pages_still_count(self):
        """One marker names one page; the passage can span several."""

        figures = self.select(
            [self.evidence(self.diagram_node, [3, 4, 5], rank=1)],
            [
                CitationRef(
                    marker=f"[S1]",
                    node_id=self.diagram_node,
                    page=5,
                    book_id=self.book_id,
                    evidence_rank=1,
                )
            ],
        )
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0].page, 3)

    def test_evidence_is_the_fallback_when_nothing_was_cited(self):
        """An abstention still rests on everything retrieved."""

        figures = self.select([self.evidence(self.diagram_node, [3])], [])
        self.assertEqual(len(figures), 1)

    def test_no_evidence_means_no_figures(self):
        self.assertEqual(self.select([]), [])

    def test_the_evidence_rank_is_carried_through(self):
        figures = self.select([self.evidence(self.diagram_node, [3], rank=2)])
        self.assertEqual(figures[0].evidence_rank, 2)

    def test_selection_is_deterministic(self):
        first = self.select([self.evidence(self.diagram_node, [3])])
        second = self.select([self.evidence(self.diagram_node, [3])])
        self.assertEqual(first, second)

    def test_another_owner_sees_none_of_these_figures(self):
        with database_connection(self.database_url, readonly=True) as connection:
            stranger = select_figures(
                connection,
                owner_id="00000000-0000-4000-8000-0000000000ff",
                evidence=[self.evidence(self.diagram_node, [3])],
            )
        self.assertEqual(stranger, [])




class FigureLimitTests(unittest.TestCase):
    """The cap keeps a figure-dense node from swamping a short answer.

    Measured motivation: one production book carries 225 figures across only
    15 nodes, so citing a single node there would otherwise render dozens.
    """

    def figure(self, page: int, rank: int | None) -> FigureRef:
        return FigureRef(
            book_id=1,
            node_id=10,
            block_id=page,
            page=page,
            mime_type="image/png",
            path="Chapter 1",
            evidence_rank=rank,
        )

    def test_a_short_list_is_returned_untouched(self):
        figures = [self.figure(1, 1), self.figure(2, 1)]
        self.assertEqual(apply_limit(figures, 6), figures)

    def test_the_best_ranked_evidence_survives_the_cap(self):
        figures = [
            self.figure(10, 9),
            self.figure(20, 1),
            self.figure(30, 9),
            self.figure(40, 1),
        ]
        kept = apply_limit(figures, 2)

        self.assertEqual([figure.page for figure in kept], [20, 40])

    def test_reading_order_survives_the_cap(self):
        # Rank 1 figures appear late in the book; they must still be shown
        # front-to-back rather than in rank order.
        figures = [
            self.figure(10, 5),
            self.figure(80, 1),
            self.figure(60, 1),
        ]
        kept = apply_limit(figures, 2)

        self.assertEqual([figure.page for figure in kept], [80, 60])

    def test_unranked_figures_yield_to_ranked_ones(self):
        figures = [self.figure(10, None), self.figure(20, 3)]
        self.assertEqual(
            [figure.page for figure in apply_limit(figures, 1)],
            [20],
        )

    def test_a_zero_limit_selects_nothing(self):
        self.assertEqual(apply_limit([self.figure(1, 1)], 0), [])

    def test_a_negative_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_limit([self.figure(1, 1)], -1)


if __name__ == "__main__":
    unittest.main()
