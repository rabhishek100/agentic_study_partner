"""The reading-session endpoints: opening, resuming, position, and resolving.

The store is served from memory, as the other API tests do, so these run
without a database. What they check is the endpoint contract — what is created,
what is refused, and what the popover is told about a selection — rather than
the SQL underneath, which the storage tests cover.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from study.anchors import ResolvedAnchor
from study.prompts import DEFAULT_PROMPT_PROFILE

OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("44444444-4444-4444-8444-444444444444")

BOOK = {
    "id": 7,
    "title": "Designing Machine Learning Systems",
    "author": "Chip Huyen",
    "page_count": 386,
    "status": "ready",
    "ready_at": None,
    "document_type": "book",
}


def session_row(position=None, session_kind="read", book_ids=(7,)):
    return {
        "id": SESSION_ID,
        "title": "Designing Machine Learning Systems",
        "book_ids": list(book_ids),
        "document_type": "book",
        "retrieval_mode": "hybrid_rerank",
        "prompt_profile_json": DEFAULT_PROMPT_PROFILE.model_dump(mode="json"),
        "state_json": {},
        "parent_conversation_id": None,
        "anchors_json": [],
        "session_kind": session_kind,
        "source_position": position,
        "created_at": "2026-08-19T00:00:00Z",
        "updated_at": "2026-08-19T00:00:00Z",
    }


@contextmanager
def stubbed_store(**overrides):
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = {"question_count": 3}
    defaults = {
        "ready_book": MagicMock(return_value=BOOK),
        "reading_session": MagicMock(return_value=None),
        "create_conversation": MagicMock(return_value=session_row()),
        "set_source_position": MagicMock(return_value=session_row({"page": 112})),
        "load_conversation": MagicMock(return_value=session_row()),
        "list_reading_sessions": MagicMock(return_value=[]),
        "load_prompt_profile": MagicMock(return_value=None),
        "resolve_document_anchors": MagicMock(
            return_value=(
                ResolvedAnchor(
                    anchor_id="preview",
                    kind="document_passage",
                    chunk_ids=("chunk-one", "chunk-two"),
                    label="p. 108 · 4.3 Class imbalance",
                    selected_text="accuracy",
                ),
            )
        ),
    }
    defaults.update(overrides)
    with patch("api.main.database_connection") as open_connection:
        open_connection.return_value.__enter__.return_value = connection
        with (
            patch("api.main.ready_book", defaults["ready_book"]),
            patch("api.main.reading_session", defaults["reading_session"]),
            patch("api.main.create_conversation", defaults["create_conversation"]),
            patch("api.main.set_source_position", defaults["set_source_position"]),
            patch("api.main.load_conversation", defaults["load_conversation"]),
            patch(
                "api.main.list_reading_sessions",
                defaults["list_reading_sessions"],
            ),
            patch("api.main.load_prompt_profile", defaults["load_prompt_profile"]),
            patch(
                "api.main.resolve_document_anchors",
                defaults["resolve_document_anchors"],
            ),
        ):
            yield SimpleNamespace(connection=connection, **defaults)


class ReadingSessionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_opening_a_source_creates_a_reading_session(self):
        with stubbed_store() as store:
            response = await self.client.post(
                "/api/reading-sessions",
                json={"book_id": 7},
            )

        self.assertEqual(response.status_code, 201)
        created = store.create_conversation.call_args.kwargs
        self.assertEqual(created["session_kind"], "read")
        self.assertEqual(created["book_ids"], [7])
        # Named after the source, because a reading session has no first
        # question — the book is the subject and the questions are its margins.
        self.assertEqual(created["title"], BOOK["title"])

    async def test_opening_a_source_twice_resumes_the_same_session(self):
        # Resuming rather than accumulating is the whole point: a second
        # session would leave last week's margin questions behind while looking
        # identical to the one that has them.
        existing = session_row({"page": 112})
        with stubbed_store(
            reading_session=MagicMock(return_value=existing),
        ) as store:
            response = await self.client.post(
                "/api/reading-sessions",
                json={"book_id": 7},
            )

        self.assertEqual(response.status_code, 201)
        store.create_conversation.assert_not_called()
        payload = response.json()
        self.assertEqual(payload["conversation_id"], str(SESSION_ID))
        self.assertEqual(payload["position"], {"page": 112})
        self.assertEqual(payload["question_count"], 3)

    async def test_a_source_that_is_not_ready_cannot_be_read(self):
        with stubbed_store(ready_book=MagicMock(return_value=None)):
            response = await self.client.post(
                "/api/reading-sessions",
                json={"book_id": 7},
            )

        self.assertEqual(response.status_code, 404)

    async def test_the_page_the_reader_reached_is_recorded(self):
        with stubbed_store() as store:
            response = await self.client.patch(
                f"/api/reading-sessions/{SESSION_ID}/position",
                json={"position": {"page": 112}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["position"], {"page": 112})
        self.assertEqual(
            store.set_source_position.call_args.kwargs["position"],
            {"page": 112},
        )

    async def test_a_position_on_something_that_is_not_a_session_is_refused(self):
        with stubbed_store(set_source_position=MagicMock(return_value=None)):
            response = await self.client.patch(
                f"/api/reading-sessions/{SESSION_ID}/position",
                json={"position": {"page": 112}},
            )

        self.assertEqual(response.status_code, 404)

    async def test_a_selection_is_resolved_before_anything_is_asked(self):
        with stubbed_store() as store:
            response = await self.client.post(
                f"/api/reading-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "document_passage",
                        "book_id": 7,
                        "page": 108,
                        "selected_text": "The measurement that hides this",
                    }
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "matched": True,
                "label": "p. 108 · 4.3 Class imbalance",
                "passage_count": 2,
            },
        )
        # Resolved by exactly the code that will resolve it when the question
        # is asked, so the preview cannot promise something the answer then
        # fails to do.
        (_, anchors), _ = store.resolve_document_anchors.call_args
        self.assertEqual(anchors[0].page, 108)

    async def test_an_unmatched_selection_is_reported_rather_than_failing(self):
        # A miss is a designed state: the words still go in as context and the
        # page still grounds the answer.
        with stubbed_store(
            resolve_document_anchors=MagicMock(
                return_value=(
                    ResolvedAnchor(
                        anchor_id="preview",
                        kind="document_passage",
                        chunk_ids=("chunk-one",),
                        label="p. 108",
                        selected_text="Figure 4.6",
                        matched=False,
                    ),
                )
            )
        ):
            response = await self.client.post(
                f"/api/reading-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "document_passage",
                        "book_id": 7,
                        "page": 108,
                        "selected_text": "Figure 4.6",
                    }
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["matched"])

    async def test_a_selection_in_another_book_is_refused(self):
        # A session's source is frozen. An anchor is not a way around that.
        with stubbed_store():
            response = await self.client.post(
                f"/api/reading-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "document_page",
                        "book_id": 9,
                        "page": 12,
                    }
                },
            )

        self.assertEqual(response.status_code, 422)

    async def test_an_ask_first_conversation_has_nothing_to_resolve_against(self):
        with stubbed_store(
            load_conversation=MagicMock(return_value=session_row(session_kind=None)),
        ):
            response = await self.client.post(
                f"/api/reading-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "document_page",
                        "book_id": 7,
                        "page": 108,
                    }
                },
            )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
