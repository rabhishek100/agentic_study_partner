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


if __name__ == "__main__":
    unittest.main()
