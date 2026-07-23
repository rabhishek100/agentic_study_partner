import os
import unittest
from unittest.mock import patch

from evals.judge import DEFAULT_JUDGE_MODEL, OpenRouterAnswerJudge
from retrieval.reranker import (
    DEFAULT_RERANKER_MODEL,
    OpenRouterReranker,
    build_reranker,
)
from study.analyze import DEFAULT_CONTROL_MODEL, _openrouter_model
from study.query import DEFAULT_GENERATION_MODEL, openrouter_model
from retrieval.vector import (
    DEFAULT_EMBEDDING_MODEL,
    OpenRouterEmbedder,
    build_embedder,
)
from storage.database import DEFAULT_EMBEDDING_MODEL as DATABASE_EMBEDDING_MODEL


class HostedProviderTests(unittest.TestCase):
    def test_database_and_client_embedding_defaults_match(self) -> None:
        self.assertEqual(DATABASE_EMBEDDING_MODEL, DEFAULT_EMBEDDING_MODEL)

    @patch("retrieval.vector.httpx.Client")
    def test_embedder_sends_dimension_and_restores_input_order(self, client) -> None:
        response = client.return_value.post.return_value
        response.json.return_value = {
            "data": [
                {"index": 1, "embedding": [2.0, 0.0, 0.0]},
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
            ]
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            embedder = OpenRouterEmbedder("test/embed", dimension=3)
            result = embedder.embed_documents(["first", "second"])

        self.assertEqual(result, [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
        payload = client.return_value.post.call_args.kwargs["json"]
        self.assertEqual(payload["dimensions"], 3)
        self.assertEqual(payload["encoding_format"], "float")
        response.raise_for_status.assert_called_once_with()

    @patch("retrieval.vector.httpx.Client")
    def test_embedder_rejects_missing_provider_rows(self, client) -> None:
        client.return_value.post.return_value.json.return_value = {
            "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            embedder = OpenRouterEmbedder("test/embed", dimension=3)
            with self.assertRaisesRegex(ValueError, "different number of vectors"):
                embedder.embed_documents(["first", "second"])

    @patch("retrieval.reranker.httpx.Client")
    def test_reranker_requests_and_validates_every_candidate(self, client) -> None:
        response = client.return_value.post.return_value
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.1},
            ]
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            reranker = OpenRouterReranker("test/rerank")
            scores = reranker.score("query", ["first", "second"])

        self.assertEqual(scores, [0.1, 0.9])
        payload = client.return_value.post.call_args.kwargs["json"]
        self.assertEqual(payload["top_n"], 2)
        response.raise_for_status.assert_called_once_with()

    @patch("retrieval.reranker.httpx.Client")
    def test_reranker_rejects_partial_provider_results(self, client) -> None:
        client.return_value.post.return_value.json.return_value = {
            "results": [{"index": 0, "relevance_score": 0.1}]
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            reranker = OpenRouterReranker("test/rerank")
            with self.assertRaisesRegex(ValueError, "every candidate"):
                reranker.score("query", ["first", "second"])

    @patch("retrieval.vector.OpenRouterEmbedder")
    @patch("retrieval.reranker.OpenRouterReranker")
    def test_empty_model_environment_values_use_managed_defaults(
        self,
        reranker,
        embedder,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_EMBEDDING_MODEL": "",
                "OPENROUTER_RERANKER_MODEL": "",
            },
        ):
            build_embedder()
            build_reranker()

        embedder.assert_called_once_with(DEFAULT_EMBEDDING_MODEL)
        reranker.assert_called_once_with(DEFAULT_RERANKER_MODEL)

    @patch("langchain_openai.ChatOpenAI")
    def test_empty_generation_model_uses_managed_default(self, chat_model) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_GENERATION_MODEL": "",
            },
        ):
            openrouter_model()

        self.assertEqual(
            chat_model.call_args.kwargs["model"],
            DEFAULT_GENERATION_MODEL,
        )

    @patch("langchain_openai.ChatOpenAI")
    def test_empty_control_model_uses_managed_default(self, chat_model) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_CONTROL_MODEL": "",
            },
        ):
            _openrouter_model()

        self.assertEqual(chat_model.call_args.kwargs["model"], DEFAULT_CONTROL_MODEL)

    @patch("langchain_openai.ChatOpenAI")
    def test_empty_judge_model_uses_managed_default(self, chat_model) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_JUDGE_MODEL": "",
            },
        ):
            OpenRouterAnswerJudge()

        self.assertEqual(chat_model.call_args.kwargs["model"], DEFAULT_JUDGE_MODEL)


if __name__ == "__main__":
    unittest.main()
