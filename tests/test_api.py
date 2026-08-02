import os
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from api.version import build_revision
from study.contracts import ConversationState, TurnResult
from study.prompts import DEFAULT_PROMPT_PROFILE
from study.query import QueryExecutionError

OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")


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


@contextmanager
def stubbed_conversation_store(conversation_id: str = str(CONVERSATION_ID)):
    """Serve conversation persistence without a database.

    The chat endpoints now create or resume a conversation and record the
    settled turn, so the unit-level API tests stub that boundary. Behaviour
    against a real database is covered by the Postgres-backed conversation
    tests instead.
    """

    created = {
        "id": conversation_id,
        "title": "Stub",
        "book_ids": [1],
        "retrieval_mode": "hybrid",
        "state_json": {},
        "created_at": None,
        "updated_at": None,
    }
    with (
        patch("api.main.create_conversation", return_value=created) as create,
        patch("api.main.load_conversation", return_value=None) as load,
        patch("api.main.append_turn", return_value=0) as append,
    ):
        yield {"create": create, "load": load, "append": append}


@contextmanager
def owner_scoped_books(ready_ids: set[int]):
    """Serve a specific set of ready books, refusing every other id."""

    connection = MagicMock()
    with (
        patch("api.main.database_connection") as open_connection,
        patch("api.main.ready_book") as lookup,
    ):
        open_connection.return_value.__enter__.return_value = connection
        lookup.side_effect = lambda _connection, requested, **_kwargs: (
            {"id": requested, "status": "ready"} if requested in ready_ids else None
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

    async def test_queue_health_reports_aggregates_without_authentication(self):
        app.dependency_overrides.clear()
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "queued": 2,
            "processing": 1,
            "retry_scheduled": 0,
            "failed_last_day": 3,
            "oldest_queued_seconds": 42.5,
            "heartbeat_age": 7.0,
            "expired_leases": 0,
        }
        with patch("api.main.database_connection") as open_connection:
            open_connection.return_value.__enter__.return_value = connection
            response = await self.client.get("/api/health/queue")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["queued_jobs"], 2)
        self.assertEqual(payload["processing_jobs"], 1)
        self.assertEqual(payload["failed_jobs_last_day"], 3)
        self.assertAlmostEqual(payload["oldest_queued_seconds"], 42.5)
        self.assertAlmostEqual(payload["worker_heartbeat_seconds"], 7.0)
        # Aggregates only: nothing here names a user, a book, or a file.
        for leaked in ("owner", "email", "filename", "storage_path"):
            self.assertNotIn(leaked, response.text)

    async def test_health_does_not_require_authentication(self):
        app.dependency_overrides.clear()

        response = await self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)

    async def test_protected_endpoints_reject_a_missing_token(self):
        app.dependency_overrides.clear()

        for method, path, payload in (
            ("get", "/api/books", None),
            ("post", "/api/chat", {"question": "Hi", "book_ids": [1]}),
            ("post", "/api/chat/stream", {"question": "Hi", "book_ids": [1]}),
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

    @patch("api.main.load_prompt_profile", return_value=None)
    @patch("api.main.database_connection")
    async def test_prompt_settings_expose_locked_and_editable_layers(
        self,
        open_connection,
        load_profile,
    ):
        open_connection.return_value.__enter__.return_value = MagicMock()

        response = await self.client.get("/api/prompt-settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("Grounding requirements", payload["locked_system_prompt"])
        self.assertIn(
            "interview-preparation study partner",
            payload["profile"]["interview_instructions"],
        )
        self.assertIn(
            "{book_evidence_inserted_by_server}",
            payload["preview_user_prompt"],
        )
        load_profile.assert_called_once()

    @patch("api.main.save_prompt_profile")
    @patch("api.main.database_connection")
    async def test_prompt_settings_save_an_owner_scoped_profile(
        self,
        open_connection,
        save_profile,
    ):
        profile = DEFAULT_PROMPT_PROFILE.model_dump(mode="json")
        profile["concept_template"] += "\nPrefer one memorable analogy."
        save_profile.return_value = profile
        open_connection.return_value.__enter__.return_value = MagicMock()

        response = await self.client.patch(
            "/api/prompt-settings",
            json={"profile": profile},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "memorable analogy",
            response.json()["profile"]["concept_template"],
        )
        self.assertEqual(save_profile.call_args.kwargs["owner_id"], OWNER_ID)

    async def test_prompt_preview_rejects_a_template_without_evidence(self):
        profile = DEFAULT_PROMPT_PROFILE.model_dump(mode="json")
        profile["user_prompt_template"] = "Question: {question}"

        response = await self.client.post(
            "/api/prompt-settings/preview",
            json={"profile": profile},
        )

        self.assertEqual(response.status_code, 422)

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
        state = ConversationState(conversation_id="conversation-1", book_ids=[1])
        execute.return_value = (result, state)

        with owner_scoped_book(), stubbed_conversation_store() as store:
            response = await self.client.post(
                "/api/chat",
                json={
                    "question": "What is training-serving skew?",
                    "retrieval_mode": "hybrid",
                    "book_ids": [1],
                    "response_depth": "deep",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["answer"], result.answer)
        # The stored conversation is the identity, not whatever id the
        # workflow happened to mint.
        self.assertEqual(
            response.json()["state"]["conversation_id"],
            str(CONVERSATION_ID),
        )
        self.assertEqual(execute.call_args.kwargs["owner_id"], OWNER_ID)
        self.assertEqual(execute.call_args.kwargs["response_depth"], "deep")
        self.assertIsNone(execute.call_args.kwargs["turn_book_ids"])
        store["append"].assert_called_once()

    @patch("api.main.execute_conversation_turn")
    async def test_chat_narrows_one_turn_to_mentioned_books(self, execute):
        result = TurnResult(
            question="Explain skew in @[Second Book]",
            answer="A grounded answer [S1].",
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query="Explain skew",
            outcome="answer",
        )
        execute.return_value = (
            result,
            ConversationState(conversation_id="c", book_ids=[1, 2]),
        )

        with owner_scoped_books({1, 2}), stubbed_conversation_store():
            response = await self.client.post(
                "/api/chat",
                json={
                    "question": "Explain skew in @[Second Book]",
                    "book_ids": [1, 2],
                    "mentioned_book_ids": [2],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(execute.call_args.kwargs["book_ids"], [1, 2])
        self.assertEqual(execute.call_args.kwargs["turn_book_ids"], [2])

    @patch("api.main.execute_conversation_turn")
    async def test_chat_rejects_an_unavailable_mentioned_book(self, execute):
        with owner_scoped_books({1}):
            response = await self.client.post(
                "/api/chat",
                json={
                    "question": "Explain skew in @[Other Book]",
                    "book_ids": [1],
                    "mentioned_book_ids": [4242],
                },
            )

        self.assertEqual(response.status_code, 404)
        execute.assert_not_called()

    @patch("api.main.execute_conversation_turn")
    async def test_chat_rejects_a_book_the_caller_cannot_use(self, execute):
        with owner_scoped_book(book_id=None):
            response = await self.client.post(
                "/api/chat",
                json={"question": "Explain drift", "book_ids": [4242]},
            )

        self.assertEqual(response.status_code, 404)
        execute.assert_not_called()

    async def test_chat_requires_an_explicit_book(self):
        response = await self.client.post(
            "/api/chat",
            json={"question": "Explain drift", "retrieval_mode": "hybrid"},
        )

        self.assertEqual(response.status_code, 422)

    async def test_chat_rejects_an_empty_book_selection(self):
        """An empty list must not be read as "search everything"."""

        response = await self.client.post(
            "/api/chat",
            json={"question": "Explain drift", "book_ids": []},
        )

        self.assertEqual(response.status_code, 422)

    @patch("api.main.execute_conversation_turn")
    async def test_chat_accepts_several_books(self, execute):
        execute.return_value = (
            TurnResult(
                question="Compare the two",
                answer="Both describe skew [S1].",
                route="retrieval_qa",
                history_dependency="independent",
                standalone_query="Compare the two",
                outcome="answer",
                retrieval_mode="hybrid",
            ),
            ConversationState(conversation_id="c", book_ids=[3, 7]),
        )
        with owner_scoped_books({3, 7}):
            response = await self.client.post(
                "/api/chat",
                json={"question": "Compare the two", "book_ids": [7, 3, 7]},
            )

        self.assertEqual(response.status_code, 200)
        # Deduplicated and ordered before it reaches the workflow.
        self.assertEqual(execute.call_args.kwargs["book_ids"], [3, 7])

    @patch("api.main.execute_conversation_turn")
    async def test_chat_rejects_the_whole_turn_when_one_book_is_unusable(
        self,
        execute,
    ):
        """Dropping the bad book would silently answer a narrower question."""

        with owner_scoped_books({3}):
            response = await self.client.post(
                "/api/chat",
                json={"question": "Compare the two", "book_ids": [3, 4242]},
            )

        self.assertEqual(response.status_code, 404)
        execute.assert_not_called()

    async def test_chat_rejects_unknown_retrieval_mode(self):
        response = await self.client.post(
            "/api/chat",
            json={"question": "Hello", "retrieval_mode": "magic", "book_ids": [1]},
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
                    "book_ids": [1],
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
        state = ConversationState(conversation_id="conversation-1", book_ids=[1])

        def fake_execute(question, conversation_state, **kwargs):
            token_callback = kwargs["token_callback"]
            token_callback("token", "It is ")
            token_callback("token", "a mismatch [S1].")
            return result, state

        execute.side_effect = fake_execute

        with owner_scoped_book(), stubbed_conversation_store():
            async with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "question": "What is training-serving skew?",
                    "retrieval_mode": "hybrid",
                    "book_ids": [1],
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
        # The final event carries the stored conversation identity, which
        # is what the client sends back to resume.
        self.assertIn(str(CONVERSATION_ID), events[2])

    @patch("api.main.execute_conversation_turn")
    async def test_chat_stream_rejects_a_book_the_caller_cannot_use(self, execute):
        with owner_scoped_book(book_id=None):
            response = await self.client.post(
                "/api/chat/stream",
                json={"question": "Explain drift", "book_ids": [4242]},
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
                    "book_ids": [1],
                },
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join([chunk async for chunk in response.aiter_text()])

        self.assertIn("event: error", body)
        self.assertIn("No indexed book is available", body)


class BuildIdentityTests(unittest.TestCase):
    """A service should be able to say what code it is serving.

    Deployment drift is otherwise invisible: the worker once ran five commits
    behind the repository for hours, and noticing meant comparing a deployment
    timestamp against a git log by eye.
    """

    def test_an_unbuilt_checkout_reports_unknown_rather_than_guessing(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BUILD_REVISION", None)
            self.assertEqual(build_revision(), "unknown")

    def test_a_built_image_reports_what_it_was_built_from(self) -> None:
        with patch.dict(os.environ, {"BUILD_REVISION": "abc1234"}):
            self.assertEqual(build_revision(), "abc1234")

    def test_whitespace_is_not_a_revision(self) -> None:
        with patch.dict(os.environ, {"BUILD_REVISION": "   "}):
            self.assertEqual(build_revision(), "unknown")


if __name__ == "__main__":
    unittest.main()
