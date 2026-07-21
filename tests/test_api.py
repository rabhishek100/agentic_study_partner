import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from api.main import app
from study.contracts import ConversationState, TurnResult
from study.query import QueryExecutionError


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_health_reports_database_readiness(self):
        response = await self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("canonical_database_ready", response.json())
        self.assertIn("retrieval_database_ready", response.json())

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

    async def test_chat_rejects_unknown_retrieval_mode(self):
        response = await self.client.post(
            "/api/chat",
            json={"question": "Hello", "retrieval_mode": "magic"},
        )

        self.assertEqual(response.status_code, 422)

    @patch("api.main.execute_conversation_turn")
    async def test_chat_turns_expected_workflow_errors_into_422(self, execute):
        execute.side_effect = QueryExecutionError("No indexed book is available")

        response = await self.client.post(
            "/api/chat",
            json={"question": "Explain drift", "retrieval_mode": "bm25"},
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
    async def test_chat_stream_emits_error_event_on_rejection(self, execute):
        execute.side_effect = QueryExecutionError("No indexed book is available")

        async with self.client.stream(
            "POST",
            "/api/chat/stream",
            json={"question": "Explain drift", "retrieval_mode": "bm25"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join([chunk async for chunk in response.aiter_text()])

        self.assertIn("event: error", body)
        self.assertIn("No indexed book is available", body)


if __name__ == "__main__":
    unittest.main()
