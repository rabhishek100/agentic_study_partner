import re
import unittest

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from retrieval.models import ChunkingConfig
from retrieval.search import retrieve
from retrieval.postgres import rebuild, search
from retrieval.vector import rebuild_vector_index, vector_search
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from tests.postgres import PostgresOwnerMixin


FILE_HASH = "b" * 64


class FakeEmbedder:
    model_name = "test-embedding-v1"
    model_revision = "test-revision"
    device = "cpu"
    dimension = 3072
    max_sequence_length = 4096

    @staticmethod
    def _embed(text: str) -> list[float]:
        words = set(re.findall(r"\w+", text.casefold()))
        values = [
            float(bool(words.intersection({"online", "architecture"}))),
            float(bool(words.intersection({"batch", "throughput"}))),
            float(bool(words.intersection({"monitoring", "failures"}))),
            float(bool(words.intersection({"table", "compares"}))),
        ]
        return values + [0.0] * (FakeEmbedder.dimension - len(values))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FakeReranker:
    model_name = "test-reranker-v1"
    model_revision = "test-revision"
    device = "cpu"
    max_sequence_length = 4096

    def __init__(self) -> None:
        self.candidate_count = 0

    def score(self, query: str, documents: list[str]) -> list[float]:
        del query
        self.candidate_count = len(documents)
        return [
            1.0 if "Online prediction architecture" in document else 0.0
            for document in documents
        ]


def sample_book() -> ParsedBook:
    return ParsedBook(
        source="sources/books/retrieval.pdf",
        toc=[
            (1, "Chapter 1. Serving", 1),
            (2, "Prediction modes", 2),
        ],
        sections=[
            Section(
                path=["Chapter 1. Serving"],
                level=1,
                start_page=1,
                end_page=1,
                texts=[
                    TextBlock(
                        text="Repeated publisher header",
                        category="DetectedHeader",
                        page=1,
                    ),
                    TextBlock(
                        text=(
                            "Serving systems need low latency. "
                            "Batch systems prioritize high throughput. "
                            "Monitoring detects failures quickly."
                        ),
                        category="NarrativeText",
                        page=1,
                    )
                ],
            ),
            Section(
                path=["Chapter 1. Serving", "Prediction modes"],
                level=2,
                start_page=2,
                end_page=3,
                texts=[
                    TextBlock(
                        text="The table compares the two prediction modes.",
                        category="NarrativeText",
                        page=2,
                    ),
                    TextBlock(
                        text="[TABLE 0]",
                        category="TablePlaceholder",
                        page=2,
                    ),
                    TextBlock(
                        text="[IMAGE 0]",
                        category="ImagePlaceholder",
                        page=3,
                    ),
                    TextBlock(
                        text="Figure 1. Online prediction architecture.",
                        category="FigureCaption",
                        page=3,
                    ),
                ],
                tables=[
                    TableBlock(
                        text=(
                            "Batch prediction high throughput. "
                            "Online prediction low latency."
                        ),
                        html="<table><tr><td>Batch prediction</td></tr></table>",
                        page=2,
                    )
                ],
                images=[
                    ImageBlock(
                        base64="aW1hZ2U=",
                        mime="image/png",
                        page=3,
                    )
                ],
            ),
        ],
    )


class RetrievalTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.database = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.database,
            sample_book(),
            owner_id=self.owner_id,
            title="Retrieval",
            author="Test",
            file_hash=FILE_HASH,
            page_count=3,
            parser_version="test-v1",
        )
        self.config = ChunkingConfig(
            target_tokens=12,
            max_tokens=30,
            overlap_tokens=3,
        )

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def test_rebuild_is_deterministic_and_searches_table_text(self) -> None:
        first = rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        first_ids = [
            row["id"]
            for row in self.database.execute(
                """
                SELECT id FROM chunks WHERE owner_id = %s
                ORDER BY toc_index, chunk_index
                """,
                (self.owner_id,),
            )
        ]
        second = rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        second_ids = [
            row["id"]
            for row in self.database.execute(
                """
                SELECT id FROM chunks WHERE owner_id = %s
                ORDER BY toc_index, chunk_index
                """,
                (self.owner_id,),
            )
        ]

        self.assertEqual(first.chunk_count, second.chunk_count)
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(
            self.database.execute(
                """
                SELECT COUNT(*) AS count FROM chunk_builds WHERE owner_id = %s
                """,
                (self.owner_id,),
            ).fetchone()["count"],
            1,
        )

        results = search(
            self.database,
            "Which prediction mode is optimized for low latency?",
            owner_id=self.owner_id,
            book_id=self.book_id,
            limit=3,
        )
        self.assertTrue(results)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertIn("Online prediction low latency", results[0].text)
        self.assertNotIn("[TABLE 0]", results[0].text)
        self.assertNotIn("[IMAGE 0]", results[0].text)
        self.assertFalse(
            search(
                self.database,
                "Repeated publisher header",
                owner_id=self.owner_id,
                book_id=self.book_id,
                limit=3,
            )
        )

    def test_chunks_keep_page_block_table_and_image_provenance(self) -> None:
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        rows = self.database.execute(
            """
            SELECT
                chunks.source_node_id,
                chunks.start_page,
                chunks.end_page,
                chunks.token_count,
                chunks.content_types,
                chunk_sources.source_block_id,
                chunk_sources.block_type
            FROM chunks
            JOIN chunk_sources ON chunk_sources.chunk_id = chunks.id
            WHERE chunks.owner_id = %s
            ORDER BY chunks.toc_index, chunks.chunk_index,
                     chunk_sources.source_order
            """,
            (self.owner_id,),
        ).fetchall()

        self.assertTrue(rows)
        self.assertTrue(
            all(row["token_count"] <= self.config.max_tokens for row in rows)
        )
        self.assertIn("table", {row["block_type"] for row in rows})
        self.assertIn("image", {row["block_type"] for row in rows})
        for row in rows:
            canonical_node_id = self.database.execute(
                """
                SELECT node_id FROM content_blocks
                WHERE id = %s AND owner_id = %s
                """,
                (row["source_block_id"], self.owner_id),
            ).fetchone()["node_id"]
            self.assertEqual(canonical_node_id, row["source_node_id"])
            self.assertLessEqual(row["start_page"], row["end_page"])

        content_types = {
            content_type for row in rows for content_type in row["content_types"]
        }
        self.assertEqual(content_types, {"text", "table", "image"})

    def test_derived_rows_can_be_deleted_without_canonical_loss(self) -> None:
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        self.database.execute(
            "delete from chunk_builds where owner_id = %s",
            (self.owner_id,),
        )

        chunk_count = self.database.execute(
            "select count(*) as count from chunks where owner_id = %s",
            (self.owner_id,),
        ).fetchone()["count"]
        node_count = self.database.execute(
            "select count(*) as count from nodes where owner_id = %s",
            (self.owner_id,),
        ).fetchone()["count"]
        self.assertEqual(chunk_count, 0)
        self.assertEqual(node_count, 2)

    def test_oversized_block_prefers_sentence_boundaries(self) -> None:
        config = ChunkingConfig(
            target_tokens=8,
            max_tokens=10,
            overlap_tokens=0,
        )
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=config,
        )
        chunks = self.database.execute(
            """
            SELECT text, token_count
            FROM chunks
            WHERE section_title = 'Chapter 1. Serving' AND owner_id = %s
            ORDER BY chunk_index
            """,
            (self.owner_id,),
        ).fetchall()

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(row["token_count"] <= 10 for row in chunks))
        chunk_texts = [row["text"] for row in chunks]
        for sentence in (
            "Serving systems need low latency.",
            "Batch systems prioritize high throughput.",
            "Monitoring detects failures quickly.",
        ):
            self.assertTrue(any(sentence in text for text in chunk_texts))

    def test_vector_index_is_idempotent_and_hydrates_postgres_chunks(self) -> None:
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        embedder = FakeEmbedder()

        first = rebuild_vector_index(
            self.database,
            embedder=embedder,
            owner_id=self.owner_id,
        )
        second = rebuild_vector_index(
            self.database,
            embedder=embedder,
            owner_id=self.owner_id,
        )

        self.assertGreater(first.embedded_count, 0)
        self.assertEqual(second.embedded_count, 0)
        self.assertEqual(second.unchanged_count, first.total_count)

        results = vector_search(
            self.database,
            "online prediction architecture",
            embedder=embedder,
            owner_id=self.owner_id,
            limit=3,
            unique_nodes=True,
        )
        self.assertTrue(results)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertEqual(results[0].retrieval_method, "vector")
        self.assertEqual(results[0].source_book_id, self.book_id)

    def test_vector_index_accepts_integer_embedding_components(self) -> None:
        """A JSON-decoded embedding can contain int 0 among floats.

        psycopg refuses to adapt a mixed int/float list, so an uncoerced
        vector fails at the insert. Regression for a book whose second batch
        happened to contain an exact-zero component.
        """

        class IntegerZeroEmbedder(FakeEmbedder):
            @staticmethod
            def _embed(text: str) -> list[float]:
                values = FakeEmbedder._embed(text)
                # Exactly what json.loads produces for 0: an int among floats.
                values[1] = 0
                values[-1] = 0
                return values

        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )

        summary = rebuild_vector_index(
            self.database,
            embedder=IntegerZeroEmbedder(),
            owner_id=self.owner_id,
        )

        self.assertGreater(summary.embedded_count, 0)

    def test_hybrid_retrieval_returns_fused_results(self) -> None:
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        embedder = FakeEmbedder()
        rebuild_vector_index(
            self.database,
            embedder=embedder,
            owner_id=self.owner_id,
        )

        results = retrieve(
            self.database,
            "online prediction architecture",
            mode="hybrid",
            owner_id=self.owner_id,
            book_id=self.book_id,
            limit=3,
            unique_nodes=True,
            embedder=embedder,
        )

        self.assertTrue(results)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertTrue(all(result.retrieval_method == "hybrid" for result in results))

    def test_hybrid_rerank_scores_the_rrf_shortlist(self) -> None:
        rebuild(
            self.database,
            self.book_id,
            owner_id=self.owner_id,
            config=self.config,
        )
        embedder = FakeEmbedder()
        reranker = FakeReranker()
        rebuild_vector_index(
            self.database,
            embedder=embedder,
            owner_id=self.owner_id,
        )

        results = retrieve(
            self.database,
            "online prediction architecture",
            mode="hybrid_rerank",
            owner_id=self.owner_id,
            book_id=self.book_id,
            limit=3,
            unique_nodes=True,
            embedder=embedder,
            reranker=reranker,
        )

        self.assertTrue(results)
        self.assertLessEqual(reranker.candidate_count, 20)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertTrue(
            all(result.retrieval_method == "hybrid_rerank" for result in results)
        )


if __name__ == "__main__":
    unittest.main()
