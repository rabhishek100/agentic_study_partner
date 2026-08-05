"""HTTP contracts for asking a video questions and reading the answer back."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from uuid import uuid4, UUID

import fitz
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
        # The page renderer reads bytes through the media store, which needs a
        # root; without one it would fall back to a container path.
        self.media = TemporaryDirectory()
        self._media_environment = mock.patch.dict(
            os.environ, {"VIDEO_MEDIA_ROOT": self.media.name}
        )
        self._media_environment.start()
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
        self._media_environment.stop()
        self.media.cleanup()
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

    async def test_names_a_conversation_from_its_first_question(self) -> None:
        created = await self.client.post(
            f"/api/videos/{self.video.video_id}/conversations", json={}
        )
        conversation_id = created.json()["conversation_id"]
        self.assertEqual(created.json()["title"], "New conversation")

        await self.client.post(
            f"/api/video-conversations/{conversation_id}/turns",
            json={"question": "What does the lecturer say about attention?"},
        )

        listed = await self.client.get(
            f"/api/videos/{self.video.video_id}/conversations"
        )
        titles = [row["title"] for row in listed.json()["conversations"]]
        self.assertIn("What does the lecturer say about attention?", titles)

    async def test_a_later_turn_never_renames_the_conversation(self) -> None:
        created = await self.client.post(
            f"/api/videos/{self.video.video_id}/conversations", json={}
        )
        conversation_id = created.json()["conversation_id"]
        for question in ("First question?", "Second question?"):
            await self.client.post(
                f"/api/video-conversations/{conversation_id}/turns",
                json={"question": question},
            )

        detail = await self.client.get(
            f"/api/video-conversations/{conversation_id}"
        )
        self.assertEqual(detail.json()["title"], "First question?")

    async def test_a_name_the_reader_chose_survives_the_first_turn(self) -> None:
        created = await self.client.post(
            f"/api/videos/{self.video.video_id}/conversations",
            json={"title": "Attention questions"},
        )
        conversation_id = created.json()["conversation_id"]

        await self.client.post(
            f"/api/video-conversations/{conversation_id}/turns",
            json={"question": "What does the lecturer say about attention?"},
        )

        detail = await self.client.get(
            f"/api/video-conversations/{conversation_id}"
        )
        self.assertEqual(detail.json()["title"], "Attention questions")

    def _attach_deck(self, *, pages: int = 3) -> UUID:
        """Link a real, readable PDF to this video and return its id."""

        storage_key = f"{self.owner}/canonical/resources/deck.pdf"
        target = Path(self.media.name) / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        document = fitz.open()
        for index in range(pages):
            page = document.new_page(width=720, height=540)
            page.insert_text((60, 90), f"Slide {index + 1}", fontsize=34)
        document.save(target)
        document.close()

        resource_id = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                """
                insert into video.resources (
                    id, owner_id, resource_kind, origin, status, title,
                    original_filename, storage_backend, storage_key,
                    content_hash, size_bytes, media_type, page_count
                ) values (
                    %s, %s, 'pdf', 'upload', 'ready', 'Lecture slides',
                    'deck.pdf', 'filesystem', %s, %s, %s,
                    'application/pdf', %s
                )
                """,
                (
                    resource_id,
                    self.owner,
                    storage_key,
                    "c" * 64,
                    target.stat().st_size,
                    pages,
                ),
            )
            database.execute(
                """
                insert into video.video_resources (
                    owner_id, video_id, resource_id, role, required
                ) values (%s, %s, %s, 'slides', false)
                """,
                (self.owner, self.video.video_id, resource_id),
            )
        return resource_id

    async def test_renders_a_cited_document_page_as_an_image(self) -> None:
        resource_id = self._attach_deck()
        base = f"/api/videos/{self.video.video_id}/resources/{resource_id}/pages"

        response = await self.client.get(f"{base}/2/image")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertTrue(response.content.startswith(b"\xff\xd8"))

        # The render is a pure function of the file, page, and settings, so a
        # second view must settle rather than rasterize the page again.
        etag = response.headers["etag"]
        repeated = await self.client.get(
            f"{base}/2/image", headers={"If-None-Match": etag}
        )
        self.assertEqual(repeated.status_code, 304)

        # A different page is a different image.
        other = await self.client.get(f"{base}/1/image")
        self.assertNotEqual(other.headers["etag"], etag)

    async def test_rejects_a_page_outside_the_document(self) -> None:
        resource_id = self._attach_deck(pages=3)
        base = f"/api/videos/{self.video.video_id}/resources/{resource_id}/pages"

        for page in (0, 4, 900):
            response = await self.client.get(f"{base}/{page}/image")
            self.assertEqual(response.status_code, 404, page)

    async def test_another_owner_cannot_render_a_linked_page(self) -> None:
        resource_id = self._attach_deck()
        app.dependency_overrides[current_owner] = lambda: self.other_owner

        response = await self.client.get(
            f"/api/videos/{self.video.video_id}"
            f"/resources/{resource_id}/pages/1/image"
        )

        self.assertEqual(response.status_code, 404)

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
