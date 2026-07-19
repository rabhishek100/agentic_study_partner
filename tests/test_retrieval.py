import json
from pathlib import Path
import re
import tempfile
import unittest

import chromadb

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from retrieval.models import ChunkingConfig
from retrieval.search import retrieve
from retrieval.sqlite import connect, initialize, rebuild, search
from retrieval.vector import rebuild_vector_index, vector_search
from storage.sqlite import connect as connect_canonical
from storage.sqlite import ingest_book, initialize as initialize_canonical


FILE_HASH = "b" * 64


class FakeEmbedder:
    model_name = "test-embedding-v1"
    model_revision = "test-revision"
    device = "cpu"
    dimension = 4
    max_sequence_length = 4096

    @staticmethod
    def _embed(text: str) -> list[float]:
        words = set(re.findall(r"\w+", text.casefold()))
        return [
            float(bool(words.intersection({"online", "architecture"}))),
            float(bool(words.intersection({"batch", "throughput"}))),
            float(bool(words.intersection({"monitoring", "failures"}))),
            float(bool(words.intersection({"table", "compares"}))),
        ]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


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


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = connect_canonical(":memory:")
        initialize_canonical(self.source)
        self.book_id = ingest_book(
            self.source,
            sample_book(),
            title="Retrieval",
            author="Test",
            file_hash=FILE_HASH,
            page_count=3,
            parser_version="test-v1",
        )
        self.destination = connect(":memory:")
        initialize(self.destination)
        self.config = ChunkingConfig(
            target_tokens=12,
            max_tokens=30,
            overlap_tokens=3,
        )

    def tearDown(self) -> None:
        self.source.close()
        self.destination.close()

    def test_rebuild_is_deterministic_and_searches_table_text(self) -> None:
        first = rebuild(
            self.source,
            self.destination,
            self.book_id,
            config=self.config,
        )
        first_ids = [
            row["id"]
            for row in self.destination.execute(
                "SELECT id FROM chunks ORDER BY toc_index, chunk_index"
            )
        ]
        second = rebuild(
            self.source,
            self.destination,
            self.book_id,
            config=self.config,
        )
        second_ids = [
            row["id"]
            for row in self.destination.execute(
                "SELECT id FROM chunks ORDER BY toc_index, chunk_index"
            )
        ]

        self.assertEqual(first.chunk_count, second.chunk_count)
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(
            self.destination.execute("SELECT COUNT(*) FROM chunk_builds").fetchone()[0],
            1,
        )

        results = search(
            self.destination,
            "Which prediction mode is optimized for low latency?",
            book_id=self.book_id,
            limit=3,
        )
        self.assertTrue(results)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertIn("Online prediction low latency", results[0].text)
        self.assertNotIn("[TABLE 0]", results[0].text)
        self.assertNotIn("[IMAGE 0]", results[0].text)

    def test_chunks_keep_page_block_table_and_image_provenance(self) -> None:
        rebuild(
            self.source,
            self.destination,
            self.book_id,
            config=self.config,
        )
        rows = self.destination.execute(
            """
            SELECT
                chunks.source_node_id,
                chunks.start_page,
                chunks.end_page,
                chunks.token_count,
                chunks.content_types_json,
                chunk_sources.source_block_id,
                chunk_sources.block_type
            FROM chunks
            JOIN chunk_sources ON chunk_sources.chunk_id = chunks.id
            ORDER BY chunks.toc_index, chunks.chunk_index,
                     chunk_sources.source_order
            """
        ).fetchall()

        self.assertTrue(rows)
        self.assertTrue(
            all(row["token_count"] <= self.config.max_tokens for row in rows)
        )
        self.assertIn("table", {row["block_type"] for row in rows})
        self.assertIn("image", {row["block_type"] for row in rows})
        for row in rows:
            canonical_node_id = self.source.execute(
                "SELECT node_id FROM content_blocks WHERE id = ?",
                (row["source_block_id"],),
            ).fetchone()[0]
            self.assertEqual(canonical_node_id, row["source_node_id"])
            self.assertLessEqual(row["start_page"], row["end_page"])

        content_types = {
            content_type
            for row in rows
            for content_type in json.loads(row["content_types_json"])
        }
        self.assertEqual(content_types, {"text", "table", "image"})

    def test_retrieval_database_is_separate_from_canonical_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination_path = Path(directory) / "retrieval.sqlite3"
            destination = connect(destination_path)
            try:
                initialize(destination)
                rebuild(
                    self.source,
                    destination,
                    self.book_id,
                    config=self.config,
                )
            finally:
                destination.close()

            canonical_tables = {
                row[0]
                for row in self.source.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("chunks", canonical_tables)
            self.assertTrue(destination_path.is_file())

    def test_oversized_block_prefers_sentence_boundaries(self) -> None:
        config = ChunkingConfig(
            target_tokens=8,
            max_tokens=10,
            overlap_tokens=0,
        )
        rebuild(
            self.source,
            self.destination,
            self.book_id,
            config=config,
        )
        chunks = self.destination.execute(
            """
            SELECT text, token_count
            FROM chunks
            WHERE section_title = 'Chapter 1. Serving'
            ORDER BY chunk_index
            """
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

    def test_vector_index_is_idempotent_and_hydrates_sqlite_chunks(self) -> None:
        rebuild(
            self.source,
            self.destination,
            self.book_id,
            config=self.config,
        )
        client = chromadb.EphemeralClient()
        embedder = FakeEmbedder()

        first = rebuild_vector_index(
            self.destination,
            self.source,
            client=client,
            embedder=embedder,
            collection_name="idempotent-test",
        )
        second = rebuild_vector_index(
            self.destination,
            self.source,
            client=client,
            embedder=embedder,
            collection_name="idempotent-test",
        )

        self.assertGreater(first.embedded_count, 0)
        self.assertEqual(second.embedded_count, 0)
        self.assertEqual(second.unchanged_count, first.total_count)

        results = vector_search(
            self.destination,
            "online prediction architecture",
            client=client,
            embedder=embedder,
            collection_name="idempotent-test",
            limit=3,
            unique_nodes=True,
        )
        self.assertTrue(results)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertEqual(results[0].retrieval_method, "vector")
        self.assertEqual(results[0].source_book_id, self.book_id)

    def test_hybrid_retrieval_returns_fused_results(self) -> None:
        rebuild(
            self.source,
            self.destination,
            self.book_id,
            config=self.config,
        )
        client = chromadb.EphemeralClient()
        embedder = FakeEmbedder()
        rebuild_vector_index(
            self.destination,
            self.source,
            client=client,
            embedder=embedder,
            collection_name="hybrid-test",
        )

        results = retrieve(
            self.destination,
            "online prediction architecture",
            mode="hybrid",
            book_id=self.book_id,
            limit=3,
            unique_nodes=True,
            client=client,
            embedder=embedder,
            collection_name="hybrid-test",
        )

        self.assertTrue(results)
        self.assertEqual(results[0].section_title, "Prediction modes")
        self.assertTrue(
            all(result.retrieval_method == "hybrid" for result in results)
        )


if __name__ == "__main__":
    unittest.main()
