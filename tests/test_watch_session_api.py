"""The watch-session endpoints: opening, resuming, position, and resolving.

The store is served from memory, so these run without a database. They mirror
`test_reading_session_api.py` deliberately: the two surfaces answer the same
questions about different material, and a difference between them should be
visible as a difference between these files.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from video.anchors import ResolvedLectureAnchor

OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
VIDEO_ID = UUID("55555555-5555-4555-8555-555555555555")
OTHER_VIDEO_ID = UUID("66666666-6666-4666-8666-666666666666")
SESSION_ID = UUID("77777777-7777-4777-8777-777777777777")

VIDEO = {"id": VIDEO_ID, "title": "Lecture 1 — Transformer"}


def session_row(position=None, session_kind="watch", video_id=VIDEO_ID):
    return {
        "id": SESSION_ID,
        "video_id": video_id,
        "title": "Lecture 1 — Transformer",
        "retrieval_mode": "hybrid_rerank",
        "retrieval_config_json": {},
        "prompt_snapshot_json": {},
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
    connection.execute.return_value.fetchone.return_value = {"question_count": 4}
    defaults = {
        "_require_video": MagicMock(return_value=VIDEO),
        "watch_session": MagicMock(return_value=None),
        "create_conversation": MagicMock(return_value=session_row()),
        "set_source_position": MagicMock(
            return_value=session_row({"timestamp_ms": 724000})
        ),
        "load_conversation": MagicMock(return_value=session_row()),
        "list_watch_sessions": MagicMock(return_value=[]),
        "resolve_lecture_anchors": MagicMock(
            return_value=(
                ResolvedLectureAnchor(
                    anchor_id="preview",
                    kind="lecture_stretch",
                    evidence_ids=("unit-a", "unit-b", "unit-c"),
                    label="11:40 – 12:30",
                ),
            )
        ),
    }
    defaults.update(overrides)
    with patch("api.video_chat.database_connection") as open_connection:
        open_connection.return_value.__enter__.return_value = connection
        with (
            patch("api.video_chat._require_video", defaults["_require_video"]),
            patch("api.video_chat.watch_session", defaults["watch_session"]),
            patch(
                "api.video_chat.create_conversation",
                defaults["create_conversation"],
            ),
            patch(
                "api.video_chat.set_source_position",
                defaults["set_source_position"],
            ),
            patch("api.video_chat.load_conversation", defaults["load_conversation"]),
            patch(
                "api.video_chat.list_watch_sessions",
                defaults["list_watch_sessions"],
            ),
            patch(
                "api.video_chat.resolve_lecture_anchors",
                defaults["resolve_lecture_anchors"],
            ),
        ):
            yield SimpleNamespace(connection=connection, **defaults)


class WatchSessionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_opening_a_lecture_creates_a_watch_session(self):
        with stubbed_store() as store:
            response = await self.client.post(
                "/api/watch-sessions",
                json={"video_id": str(VIDEO_ID)},
            )

        self.assertEqual(response.status_code, 201)
        created = store.create_conversation.call_args.kwargs
        self.assertEqual(created["session_kind"], "watch")
        self.assertEqual(created["video_id"], VIDEO_ID)
        self.assertEqual(created["title"], VIDEO["title"])

    async def test_opening_a_lecture_twice_resumes_the_same_session(self):
        with stubbed_store(
            watch_session=MagicMock(return_value=session_row({"timestamp_ms": 724000})),
        ) as store:
            response = await self.client.post(
                "/api/watch-sessions",
                json={"video_id": str(VIDEO_ID)},
            )

        self.assertEqual(response.status_code, 201)
        store.create_conversation.assert_not_called()
        payload = response.json()
        self.assertEqual(payload["position"], {"timestamp_ms": 724000})
        self.assertEqual(payload["question_count"], 4)

    async def test_the_moment_the_viewer_reached_is_recorded(self):
        with stubbed_store() as store:
            response = await self.client.patch(
                f"/api/watch-sessions/{SESSION_ID}/position",
                json={"position": {"timestamp_ms": 724000}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["position"], {"timestamp_ms": 724000})
        self.assertEqual(
            store.set_source_position.call_args.kwargs["position"],
            {"timestamp_ms": 724000},
        )

    async def test_a_position_on_something_that_is_not_a_session_is_refused(self):
        with stubbed_store(set_source_position=MagicMock(return_value=None)):
            response = await self.client.patch(
                f"/api/watch-sessions/{SESSION_ID}/position",
                json={"position": {"timestamp_ms": 724000}},
            )

        self.assertEqual(response.status_code, 404)

    async def test_a_stretch_is_resolved_before_anything_is_asked(self):
        with stubbed_store() as store:
            response = await self.client.post(
                f"/api/watch-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "lecture_stretch",
                        "video_id": str(VIDEO_ID),
                        "start_ms": 700000,
                        "end_ms": 750000,
                    }
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"matched": True, "label": "11:40 – 12:30", "unit_count": 3},
        )
        (_, anchors), _ = store.resolve_lecture_anchors.call_args
        self.assertEqual(anchors[0].start_ms, 700000)

    async def test_a_silent_stretch_is_reported_rather_than_failing(self):
        # A stretch past the end of what was ingested, or one with nothing in
        # it, resolves to nothing. Worth knowing before committing a question.
        with stubbed_store(
            resolve_lecture_anchors=MagicMock(
                return_value=(
                    ResolvedLectureAnchor(
                        anchor_id="preview",
                        kind="lecture_moment",
                        evidence_ids=(),
                        label="1:40:00",
                        matched=False,
                    ),
                )
            )
        ):
            response = await self.client.post(
                f"/api/watch-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "lecture_moment",
                        "video_id": str(VIDEO_ID),
                        "timestamp_ms": 6000000,
                    }
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["matched"])
        self.assertEqual(response.json()["unit_count"], 0)

    async def test_an_anchor_naming_another_lecture_is_refused(self):
        # A session's recording is inherited. Resolving this would produce
        # plausible evidence for a different lesson.
        with stubbed_store():
            response = await self.client.post(
                f"/api/watch-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "lecture_moment",
                        "video_id": str(OTHER_VIDEO_ID),
                        "timestamp_ms": 724000,
                    }
                },
            )

        self.assertEqual(response.status_code, 422)

    async def test_an_ask_first_conversation_has_nothing_to_resolve_against(self):
        with stubbed_store(
            load_conversation=MagicMock(return_value=session_row(session_kind=None)),
        ):
            response = await self.client.post(
                f"/api/watch-sessions/{SESSION_ID}/anchors/resolve",
                json={
                    "anchor": {
                        "kind": "lecture_moment",
                        "video_id": str(VIDEO_ID),
                        "timestamp_ms": 724000,
                    }
                },
            )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
