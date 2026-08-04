"""HTTP contracts for asking a video questions and reading the answer back."""

import json
import unittest
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from storage.database import connection, resolve_database_url
from tests.test_video_conversation import FakeAnswerModel
from tests.video_fixtures import publish_video_with_evidence
from video.answers import VideoAnswerDependencies


class VideoChatApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-chat-api.test"),
                )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: self.owner
        self.model = FakeAnswerModel(
            "The lecturer explains attention [S1].",
            "It weights the values [S1].",
            cost=0.002,
        )
        # The HTTP layer must not reach a provider in tests; the answer model
        # and embedders are the only pieces that would.
        import api.video_chat as module

        self._original = module._answer_dependencies
        module._answer_dependencies = lambda: VideoAnswerDependencies(
            model=self.model
        )
        self._module = module

    async def asyncTearDown(self) -> None:
        self._module._answer_dependencies = self._original
        app.dependency_overrides.clear()
        await self.client.aclose()
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )

    async def _conversation(self) -> str:
        response = await self.client.post(
            f"/api/videos/{self.video.video_id}/conversations",
            json={"title": "Attention questions"},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["conversation_id"]

    async def test_chat_routes_require_authentication(self) -> None:
        app.dependency_overrides.clear()
        identifier = uuid4()
        for method, path, body in (
            ("post", f"/api/videos/{identifier}/conversations", {}),
            ("get", f"/api/videos/{identifier}/conversations", None),
            ("get", "/api/video-conversations", None),
            ("get", f"/api/video-conversations/{identifier}", None),
            ("post", f"/api/video-conversations/{identifier}/turns", {"question": "x"}),
            ("get", f"/api/videos/{identifier}/timeline", None),
            ("get", f"/api/videos/{identifier}/frames/1/image", None),
        ):
            response = await getattr(self.client, method)(
                path, **({"json": body} if body is not None else {})
            )
            self.assertEqual(response.status_code, 401, path)

    async def test_asks_a_question_and_stores_the_turn(self) -> None:
        conversation_id = await self._conversation()
        response = await self.client.post(
            f"/api/video-conversations/{conversation_id}/turns",
            json={"question": "what does the diagram show?"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        result = payload["result"]
        self.assertEqual(result["outcome"], "answer")
        self.assertEqual(result["ingestion_version_id"], str(self.video.version_id))
        self.assertTrue(result["evidence"])
        self.assertEqual(result["citations"][0]["marker"], "[S1]")
        self.assertEqual(result["cost_usd"], 0.002)

        detail = await self.client.get(f"/api/video-conversations/{conversation_id}")
        self.assertEqual(detail.status_code, 200)
        turns = detail.json()["turns"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["turn_index"], 0)
        self.assertEqual(turns[0]["answer"], result["answer"])

        listed = await self.client.get(
            f"/api/videos/{self.video.video_id}/conversations"
        )
        self.assertEqual(listed.json()["conversations"][0]["turn_count"], 1)

    async def test_streams_tokens_then_one_final_event(self) -> None:
        conversation_id = await self._conversation()
        events = []
        async with self.client.stream(
            "POST",
            f"/api/video-conversations/{conversation_id}/turns/stream",
            json={"question": "what does the diagram show?"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            current = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current = line.removeprefix("event: ")
                elif line.startswith("data: ") and current:
                    events.append((current, line.removeprefix("data: ")))

        kinds = [kind for kind, _ in events]
        self.assertIn("token", kinds)
        self.assertEqual(kinds[-1], "final")
        final = json.loads(events[-1][1])
        self.assertEqual(final["result"]["outcome"], "answer")
        self.assertTrue(final["result"]["answer"])

    async def test_another_owner_cannot_see_or_use_the_conversation(self) -> None:
        conversation_id = await self._conversation()
        app.dependency_overrides[current_owner] = lambda: self.other_owner
        for method, path, body in (
            ("get", f"/api/video-conversations/{conversation_id}", None),
            (
                "post",
                f"/api/video-conversations/{conversation_id}/turns",
                {"question": "what is this?"},
            ),
            ("delete", f"/api/video-conversations/{conversation_id}", None),
            ("get", f"/api/videos/{self.video.video_id}/timeline", None),
        ):
            response = await getattr(self.client, method)(
                path, **({"json": body} if body is not None else {})
            )
            self.assertEqual(response.status_code, 404, path)

    async def test_renames_and_deletes_a_conversation(self) -> None:
        conversation_id = await self._conversation()
        renamed = await self.client.patch(
            f"/api/video-conversations/{conversation_id}",
            json={"title": "Self-attention thread"},
        )
        self.assertEqual(renamed.json()["title"], "Self-attention thread")
        removed = await self.client.delete(
            f"/api/video-conversations/{conversation_id}"
        )
        self.assertEqual(removed.status_code, 204)
        missing = await self.client.get(
            f"/api/video-conversations/{conversation_id}"
        )
        self.assertEqual(missing.status_code, 404)

    async def test_timeline_lists_published_frames_with_image_links(self) -> None:
        response = await self.client.get(
            f"/api/videos/{self.video.video_id}/timeline"
        )
        self.assertEqual(response.status_code, 200)
        entries = response.json()["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["timestamp_ms"], 120_000)
        self.assertEqual(entries[0]["visual_types"], ["diagram"])
        self.assertEqual(
            entries[0]["image_url"],
            f"/api/videos/{self.video.video_id}/frames/{entries[0]['frame_id']}/image",
        )


if __name__ == "__main__":
    unittest.main()
