"""A course question buys each query embedding once, not once per lecture."""

import unittest

from video.course_retrieval import _MemoizedQueryEmbedder


class CountingEmbedder:
    """Stands in for a paid embedding provider and counts what it is asked for."""

    model_name = "fake/text-embedding-2026"
    model_revision = "r1"
    dimension = 4

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str):
        self.queries.append(text)
        return object()

    def embed_documents(self, texts):
        raise AssertionError("retrieval must not embed documents")


class MemoizedQueryEmbedderTests(unittest.TestCase):
    def test_the_same_question_is_embedded_once_for_every_lecture(self) -> None:
        """Twenty lectures asked the same thing must cost one embedding.

        Cross-lecture retrieval calls the per-lecture retriever once per
        selected lecture, and that retriever embeds the query it is given. For
        a twenty-lecture course that was twenty identical purchases of one
        vector for a single question.
        """

        provider = CountingEmbedder()
        embedder = _MemoizedQueryEmbedder(provider)

        results = [embedder.embed_query("how does raft elect a leader") for _ in range(20)]

        self.assertEqual(provider.queries, ["how does raft elect a leader"])
        self.assertEqual(embedder.calls, 1)
        # Every lecture receives the identical vector, not merely an equal one.
        self.assertEqual(len({id(result) for result in results}), 1)

    def test_a_different_question_is_still_bought(self) -> None:
        provider = CountingEmbedder()
        embedder = _MemoizedQueryEmbedder(provider)

        embedder.embed_query("how does raft elect a leader")
        embedder.embed_query("what is a linearizable read")
        embedder.embed_query("how does raft elect a leader")

        self.assertEqual(len(provider.queries), 2)
        self.assertEqual(embedder.calls, 2)

    def test_the_identity_the_embedding_lookup_filters_on_survives(self) -> None:
        """The vector search matches rows on the model's identity, not the wrapper."""

        provider = CountingEmbedder()
        embedder = _MemoizedQueryEmbedder(provider)

        self.assertEqual(embedder.model_name, provider.model_name)
        self.assertEqual(embedder.model_revision, provider.model_revision)
        self.assertEqual(embedder.dimension, provider.dimension)

    def test_nothing_is_shared_between_questions(self) -> None:
        """The memo is per question, so a later ask is never served a stale vector."""

        provider = CountingEmbedder()

        _MemoizedQueryEmbedder(provider).embed_query("same text")
        _MemoizedQueryEmbedder(provider).embed_query("same text")

        self.assertEqual(len(provider.queries), 2)


if __name__ == "__main__":
    unittest.main()
