"""A reranker outage must degrade the ordering, not kill the turn.

`hybrid_rerank` retrieves and fuses candidates first and only then asks the
hosted reranker to reorder them. When that call failed the exception travelled
all the way out of the LangGraph run and reached the reader as "internal
error" — on a question the library could answer perfectly well. A 429 from the
rerank endpoint did exactly that in production on 2026-09-07.
"""

import importlib
import unittest
from unittest import mock

import httpx

from retrieval.reranker import (
    OpenRouterReranker,
    RerankerUnavailable,
    rerank,
)
from retrieval.postgres import SearchResult


# `retrieval/__init__.py` re-exports a `search` *function*, which shadows the
# submodule for both `from retrieval import search` and `import retrieval.search
# as ...`. Going through sys.modules is what actually yields the module whose
# globals the fallback reads.
search_module = importlib.import_module("retrieval.search")


def result(chunk_id: str, rank: int) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        source_book_id=1,
        source_node_id=1,
        toc_index=0,
        chunk_index=rank,
        section_title="Chapter 1",
        path_text="Chapter 1",
        start_page=rank,
        end_page=rank,
        text=f"passage {chunk_id}",
        content_types=("text",),
        score=1.0 / rank,
        retrieval_method="hybrid",
    )


class RerankerUnavailableTests(unittest.TestCase):
    def reranker(self) -> OpenRouterReranker:
        # The constructor reads the key from the environment; the request it
        # would build is mocked out in every test below.
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            return OpenRouterReranker("cohere/rerank-4-pro")

    def test_a_rate_limit_is_reported_as_unavailable_not_as_a_crash(self) -> None:
        reranker = self.reranker()
        response = httpx.Response(
            429, request=httpx.Request("POST", "https://openrouter.ai/api/v1/rerank")
        )
        with mock.patch.object(reranker._client, "post", return_value=response):
            with self.assertRaises(RerankerUnavailable) as caught:
                reranker.score("q", ["a", "b"])

        # The provider's own status is preserved for the log, and the original
        # error is chained rather than swallowed.
        self.assertIn("429", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, httpx.HTTPError)

    def test_a_timeout_is_also_unavailable(self) -> None:
        reranker = self.reranker()
        with mock.patch.object(
            reranker._client, "post", side_effect=httpx.ReadTimeout("slow")
        ):
            with self.assertRaises(RerankerUnavailable):
                reranker.score("q", ["a"])

    def test_a_malformed_response_is_unavailable_rather_than_a_key_error(self) -> None:
        reranker = self.reranker()
        response = httpx.Response(
            200,
            json={"unexpected": []},
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/rerank"),
        )
        with mock.patch.object(reranker._client, "post", return_value=response):
            with self.assertRaises(RerankerUnavailable):
                reranker.score("q", ["a"])

    def test_a_contract_violation_is_still_a_hard_error(self) -> None:
        """Degrading is for the provider being down, not for our own bugs."""

        class Broken:
            model_name = "broken"

            def score(self, query, documents):
                return [1.0]  # one score for two documents

        with self.assertRaises(ValueError):
            rerank(
                "q",
                [result("a", 1), result("b", 2)],
                reranker=Broken(),
                limit=2,
                unique_nodes=True,
            )


class DegradedRetrievalTests(unittest.TestCase):
    """The behaviour that actually matters: the turn survives."""

    CANDIDATES = [result("a", 1), result("b", 2), result("c", 3)]

    def retrieve_with(self, reranker_effect):
        """Run `hybrid_rerank` with the database and embedder stubbed out.

        Only the reranking branch is under test, so the fused candidates are
        supplied directly rather than standing up Postgres and paying an
        embedding call to reach the one `try` this is about.
        """

        with (
            mock.patch.object(
                search_module, "hybrid_candidates", return_value=self.CANDIDATES
            ),
            mock.patch.object(search_module, "rerank", side_effect=reranker_effect),
        ):
            return search_module.retrieve(
                connection=None,
                query="how does caching work",
                mode="hybrid_rerank",
                owner_id="00000000-0000-4000-8000-000000000001",
                limit=3,
                embedder=object(),
                reranker=object(),
            )

    def test_an_unavailable_reranker_falls_back_to_the_fused_ordering(self) -> None:
        results = self.retrieve_with(RerankerUnavailable("429 Too Many Requests"))

        # The same passages hybrid retrieval already found, in its order — the
        # `hybrid` mode this project measured, not an empty result and not a
        # raised exception.
        self.assertEqual(
            [item.chunk_id for item in results], ["a", "b", "c"]
        )

    def test_a_working_reranker_is_still_used(self) -> None:
        reordered = [result("c", 1), result("a", 2)]
        results = self.retrieve_with(None)
        # side_effect=None means the mock returns its default; assert the
        # reranked path is taken by returning a distinct list instead.
        with (
            mock.patch.object(
                search_module, "hybrid_candidates", return_value=self.CANDIDATES
            ),
            mock.patch.object(
                search_module, "rerank", return_value=reordered
            ) as ranked,
        ):
            results = search_module.retrieve(
                connection=None,
                query="q",
                mode="hybrid_rerank",
                owner_id="00000000-0000-4000-8000-000000000001",
                limit=3,
                embedder=object(),
                reranker=object(),
            )
        self.assertTrue(ranked.called)
        self.assertEqual([item.chunk_id for item in results], ["c", "a"])


if __name__ == "__main__":
    unittest.main()
