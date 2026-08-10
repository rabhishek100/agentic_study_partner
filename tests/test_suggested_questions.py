"""Tests for dynamic suggested questions storage, question generator, and API endpoints."""

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from storage.database import connection as database_connection
from storage.suggested_questions import (
    get_cached_suggested_questions,
    save_cached_suggested_questions,
    versioned_suggested_questions_key,
)
from study.question_generator import (
    MAX_QUESTION_WORDS,
    _deterministic_book_questions,
    _deterministic_video_questions,
    normalize_suggested_question,
    select_concise_questions,
)
from tests.postgres import PostgresOwnerMixin

TEST_OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
TEST_VIDEO_ID = UUID("33333333-3333-4333-8333-333333333333")


class SuggestedQuestionsStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def test_cache_miss_returns_none(self) -> None:
        with database_connection(self.database_url, readonly=True) as connection:
            cached = get_cached_suggested_questions(
                connection, owner_id=self.owner_id, scope_type="book", scope_key="book:999"
            )
        self.assertIsNone(cached)

    def test_save_and_retrieve_cache(self) -> None:
        questions = [
            "Q1: What is the main thesis?",
            "Q2: How does the model scale?",
            "Q3: Explain section 2.1.",
            "Q4: What are the latency trade-offs?",
            "Q5: Describe the evaluation metrics.",
        ]
        with database_connection(self.database_url) as connection:
            save_cached_suggested_questions(
                connection,
                owner_id=self.owner_id,
                scope_type="book",
                scope_key="book:1",
                questions=questions,
            )

        with database_connection(self.database_url, readonly=True) as connection:
            retrieved = get_cached_suggested_questions(
                connection,
                owner_id=self.owner_id,
                scope_type="book",
                scope_key="book:1",
            )
        self.assertEqual(retrieved, questions)

    def test_updating_cache_replaces_row(self) -> None:
        first = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        second = ["New Q1", "New Q2", "New Q3", "New Q4", "New Q5"]
        with database_connection(self.database_url) as connection:
            save_cached_suggested_questions(
                connection,
                owner_id=self.owner_id,
                scope_type="library",
                scope_key="books:all:2026-08-08",
                questions=first,
            )
            save_cached_suggested_questions(
                connection,
                owner_id=self.owner_id,
                scope_type="library",
                scope_key="books:all:2026-08-08",
                questions=second,
            )
            count = connection.execute(
                "select count(*) as total from public.suggested_questions_cache where owner_id = %s",
                (self.owner_id,),
            ).fetchone()["total"]

        self.assertEqual(count, 1)
        with database_connection(self.database_url, readonly=True) as connection:
            retrieved = get_cached_suggested_questions(
                connection,
                owner_id=self.owner_id,
                scope_type="library",
                scope_key="books:all:2026-08-08",
            )
        self.assertEqual(retrieved, second)


class DeterministicQuestionGeneratorTests(unittest.TestCase):
    def assert_concise(self, questions: list[str]) -> None:
        self.assertEqual(len(questions), 5)
        for question in questions:
            self.assertEqual(normalize_suggested_question(question), question)
            self.assertLessEqual(len(question.split()), MAX_QUESTION_WORDS)

    def test_deterministic_book_questions_returns_5(self) -> None:
        # Empty books
        q0 = _deterministic_book_questions([])
        self.assertEqual(len(q0), 5)

        # Single book with chapters
        single = [
            {
                "title": "Designing Data-Intensive Applications",
                "chapters": [
                    "Chapter 1: Reliable Systems",
                    "Chapter 2: Data Models",
                ],
            }
        ]
        q1 = _deterministic_book_questions(single)
        self.assert_concise(q1)
        self.assertIn("What is Reliable Systems about?", q1)

        # Multiple books
        multi = [
            {"title": "Book A", "chapters": []},
            {"title": "Book B", "chapters": []},
        ]
        q2 = _deterministic_book_questions(multi)
        self.assert_concise(q2)
        self.assertIn("What idea appears across these books?", q2)

    def test_deterministic_video_questions_returns_5(self) -> None:
        chapters = ["Introduction", "Self-Attention Mechanism", "Conclusion"]
        q = _deterministic_video_questions("Lecture 1: Transformers", chapters)
        self.assert_concise(q)
        self.assertTrue(any("Transformers" in item for item in q))
        self.assertTrue(any("Self-Attention" in item for item in q))

    def test_rejects_long_compound_interview_exercises(self) -> None:
        generated = [
            "How would you design an end-to-end RAG assistant that handles changing data, latency, and quality?",
            "In a machine learning system, how would you define the objective, engineer features, choose metrics, and monitor drift?",
            "Compare replication, partitioning, consensus, and transactions in a distributed service?",
            "Why does caching matter?",
        ]
        selected = select_concise_questions(
            generated,
            _deterministic_book_questions([]),
        )

        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0], "Why does caching matter?")
        self.assertNotIn(generated[0], selected)
        self.assertNotIn(generated[1], selected)
        self.assertNotIn(generated[2], selected)
        self.assert_concise(selected)

    def test_normalizes_model_numbering_without_loosening_limits(self) -> None:
        self.assertEqual(
            normalize_suggested_question("Q1: What problem does caching solve?"),
            "What problem does caching solve?",
        )
        self.assertIsNone(
            normalize_suggested_question(
                "How would you design an end-to-end system for this problem?"
            )
        )
        self.assertIsNone(
            normalize_suggested_question("What is caching? Why does it matter?")
        )

    def test_cache_version_replaces_existing_oversized_suggestions(self) -> None:
        self.assertEqual(
            versioned_suggested_questions_key("book:7"),
            "book:7:concise-v2",
        )


class SuggestedQuestionsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        app.dependency_overrides[current_owner] = lambda: TEST_OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    @patch("api.main.database_connection")
    @patch("api.main.get_cached_suggested_questions")
    @patch("api.main.generate_book_questions")
    async def test_get_book_suggested_questions_endpoint(
        self, mock_generate, mock_cache, mock_db
    ) -> None:
        mock_cache.return_value = None
        mock_generate.return_value = ["B1", "B2", "B3", "B4", "B5"]

        response = await self.client.get("/api/books/suggested-questions?book_ids=1,2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["questions"]), 5)
        self.assertEqual(data["scope_type"], "library")
        self.assertTrue(data["scope_key"].startswith("books:1,2:"))
        self.assertTrue(
            mock_cache.call_args.kwargs["scope_key"].endswith(":concise-v2")
        )

    @patch("api.video_chat.database_connection")
    @patch("api.video_chat._require_video")
    @patch("api.video_chat.get_cached_suggested_questions")
    @patch("api.video_chat.generate_video_questions")
    async def test_get_video_suggested_questions_endpoint(
        self, mock_generate, mock_cache, mock_require_video, mock_db
    ) -> None:
        mock_require_video.return_value = {"id": TEST_VIDEO_ID, "title": "Test Video"}
        mock_cache.return_value = None
        mock_generate.return_value = ["V1", "V2", "V3", "V4", "V5"]

        response = await self.client.get(f"/api/videos/{TEST_VIDEO_ID}/suggested-questions")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["questions"]), 5)
        self.assertEqual(data["scope_type"], "video")
        self.assertEqual(data["scope_key"], f"video:{TEST_VIDEO_ID}")
        self.assertEqual(
            mock_cache.call_args.kwargs["scope_key"],
            f"video:{TEST_VIDEO_ID}:concise-v2",
        )


if __name__ == "__main__":
    unittest.main()
