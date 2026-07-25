import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from study.contracts import ConversationState, TurnResult
from study.query import QueryExecutionError


OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")


@contextmanager
def owner_scoped_book(book_id: int | None = 1):
    """Serve one ready book to the API without touching Postgres.

    ``book_id=None`` stands for every reason the API refuses a book: missing,
    owned by someone else, or still processing.
    """

    connection = MagicMock()
    with (
        patch("api.main.database_connection") as open_connection,
        patch("api.main.ready_book") as lookup,
    ):
        open_connection.return_value.__enter__.return_value = connection
        lookup.return_value = (
            None if book_id is None else {"id": book_id, "status": "ready"}
        )
        yield lookup


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_health_reports_database_readiness(self):
        response = await self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("canonical_database_ready", response.json())
        self.assertIn("retrieval_database_ready", response.json())

    @patch("api.main.database_readiness", return_value=(True, False))
    async def test_health_returns_503_when_database_is_not_ready(self, check):
        response = await self.client.get("/api/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertTrue(response.json()["canonical_database_ready"])
        self.assertFalse(response.json()["retrieval_database_ready"])
        check.assert_called_once_with()

    async def test_health_does_not_require_authentication(self):
        app.dependency_overrides.clear()

        response = await self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)

    async def test_protected_endpoints_reject_a_missing_token(self):
        app.dependency_overrides.clear()

        for method, path, payload in (
            ("get", "/api/books", None),
            ("post", "/api/chat", {"question": "Hi", "book_id": 1}),
            ("post", "/api/chat/stream", {"question": "Hi", "book_id": 1}),
        ):
            with self.subTest(path=path):
                response = await getattr(self.client, method)(
                    path, **({"json": payload} if payload else {})
                )
                self.assertEqual(response.status_code, 401)

    async def test_protected_endpoints_reject_an_invalid_token(self):
        app.dependency_overrides.clear()

        response = await self.client.get(
            "/api/books", headers={"Authorization": "Bearer not-a-real-token"}
        )

        self.assertEqual(response.status_code, 401)

    @patch("api.main.book_retrieval_completeness", return_value=(12, 12))
    @patch("api.main.list_books")
    @patch("api.main.database_connection")
    async def test_books_lists_only_owner_scoped_ready_books(
        self, open_connection, books, completeness
    ):
        connection = MagicMock()
        open_connection.return_value.__enter__.return_value = connection
        books.return_value = [
            {
                "id": 7,
                "title": "Designing ML Systems",
                "author": "Chip Huyen",
                "page_count": 386,
                "ready_at": None,
                "status": "ready",
            }
        ]

        response = await self.client.get("/api/books")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["books"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["book_id"], 7)
        self.assertTrue(payload[0]["retrieval_complete"])
        # The listing is scoped by the verified token subject, never by input.
        self.assertEqual(books.call_args.kwargs["owner_id"], OWNER_ID)

    @patch("api.main.execute_conversation_turn")
    async def test_chat_returns_result_and_updated_state(self, execute):
        result = TurnResult(
            question="What is training-serving skew?",
            answer="It is a mismatch [S1].",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query="What is training-serving skew?",
            outcome="answer",
            retrieval_mode="hybrid",
        )
        state = ConversationState(conversation_id="conversation-1", book_id=1)
        execute.return_value = (result, state)

        with owner_scoped_book():
            response = await self.client.post(
                "/api/chat",
                json={
                    "question": "What is training-serving skew?",
                    "retrieval_mode": "hybrid",
                    "book_id": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["answer"], result.answer)
        self.assertEqual(
            response.json()["state"]["conversation_id"],
            "conversation-1",
        )
        self.assertEqual(execute.call_args.kwargs["owner_id"], OWNER_ID)

    @patch("api.main.execute_conversation_turn")
    async def test_chat_rejects_a_book_the_caller_cannot_use(self, execute):
        with owner_scoped_book(book_id=None):
            response = await self.client.post(
                "/api/chat",
                json={"question": "Explain drift", "book_id": 4242},
            )

        self.assertEqual(response.status_code, 404)
        execute.assert_not_called()

    async def test_chat_requires_an_explicit_book(self):
        response = await self.client.post(
            "/api/chat",
            json={"question": "Explain drift", "retrieval_mode": "hybrid"},
        )

        self.assertEqual(response.status_code, 422)

    async def test_chat_rejects_unknown_retrieval_mode(self):
        response = await self.client.post(
            "/api/chat",
            json={"question": "Hello", "retrieval_mode": "magic", "book_id": 1},
        )

        self.assertEqual(response.status_code, 422)

    @patch("api.main.execute_conversation_turn")
    async def test_chat_turns_expected_workflow_errors_into_422(self, execute):
        execute.side_effect = QueryExecutionError("No indexed book is available")

        with owner_scoped_book():
            response = await self.client.post(
                "/api/chat",
                json={
                    "question": "Explain drift",
                    "retrieval_mode": "bm25",
                    "book_id": 1,
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "No indexed book is available")

    @patch("api.main.execute_conversation_turn")
    async def test_chat_stream_emits_tokens_then_final(self, execute):
        result = TurnResult(
            question="What is training-serving skew?",
            answer="It is a mismatch [S1].",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query="What is training-serving skew?",
            outcome="answer",
            retrieval_mode="hybrid",
        )
        state = ConversationState(conversation_id="conversation-1", book_id=1)

        def fake_execute(question, conversation_state, **kwargs):
            token_callback = kwargs["token_callback"]
            token_callback("token", "It is ")
            token_callback("token", "a mismatch [S1].")
            return result, state

        execute.side_effect = fake_execute

        with owner_scoped_book():
            async with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "question": "What is training-serving skew?",
                    "retrieval_mode": "hybrid",
                    "book_id": 1,
                },
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join([chunk async for chunk in response.aiter_text()])

        events = [block for block in body.split("\n\n") if block.strip()]
        self.assertEqual(len(events), 3)
        self.assertIn("event: token", events[0])
        self.assertIn("It is ", events[0])
        self.assertIn("event: token", events[1])
        self.assertIn("event: final", events[2])
        self.assertIn(result.answer, events[2])
        self.assertIn("conversation-1", events[2])

    @patch("api.main.execute_conversation_turn")
    async def test_chat_stream_rejects_a_book_the_caller_cannot_use(self, execute):
        with owner_scoped_book(book_id=None):
            response = await self.client.post(
                "/api/chat/stream",
                json={"question": "Explain drift", "book_id": 4242},
            )

        self.assertEqual(response.status_code, 404)
        execute.assert_not_called()

    @patch("api.main.execute_conversation_turn")
    async def test_chat_stream_emits_error_event_on_rejection(self, execute):
        execute.side_effect = QueryExecutionError("No indexed book is available")

        with owner_scoped_book():
            async with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "question": "Explain drift",
                    "retrieval_mode": "bm25",
                    "book_id": 1,
                },
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join([chunk async for chunk in response.aiter_text()])

        self.assertIn("event: error", body)
        self.assertIn("No indexed book is available", body)


if __name__ == "__main__":
    unittest.main()
