"""Standalone-video HTTP contracts against the real video repository."""

import unittest
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from storage.database import connection, resolve_database_url


class VideoApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-api.test"),
                )
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: self.owner

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.clear()
        await self.client.aclose()
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )

    def act_as(self, owner) -> None:
        app.dependency_overrides[current_owner] = lambda: owner

    async def create_youtube(self, *, key=None, video_id="abcdefghijk"):
        return await self.client.post(
            "/api/videos/youtube",
            headers={"Idempotency-Key": str(key or uuid4())},
            json={
                "url": f"https://youtu.be/{video_id}?si=tracking",
                "title": "CME 295 Lecture 1",
            },
        )

    async def test_video_routes_require_authentication(self) -> None:
        app.dependency_overrides.clear()
        identifier = uuid4()
        for method, path, body in (
            ("get", "/api/videos", None),
            ("post", "/api/videos/youtube", {"url": "https://youtu.be/abcdefghijk"}),
            ("post", "/api/videos/uploads", {
                "original_filename": "lecture.mp4", "content_type": "video/mp4",
                "content_length": 100,
            }),
            ("get", f"/api/videos/{identifier}", None),
            ("get", f"/api/video-ingestions/{identifier}", None),
        ):
            with self.subTest(path=path):
                response = await getattr(self.client, method)(
                    path, **({"json": body} if body else {})
                )
                self.assertEqual(response.status_code, 401)

    async def test_youtube_create_replay_list_detail_and_safe_job_status(self) -> None:
        key = uuid4()
        first = await self.create_youtube(key=key)
        replay = await self.create_youtube(key=key)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json()["video_id"], replay.json()["video_id"])
        self.assertEqual(first.json()["ingestion_job_id"], replay.json()["ingestion_job_id"])
        self.assertEqual(first.json()["ingestion_status"], "queued")
        self.assertEqual(first.headers["Location"], f"/api/videos/{first.json()['video_id']}")

        listed = await self.client.get("/api/videos")
        detail = await self.client.get(first.headers["Location"])
        job = await self.client.get(
            f"/api/video-ingestions/{first.json()['ingestion_job_id']}"
        )
        events = await self.client.get(
            f"/api/video-ingestions/{first.json()['ingestion_job_id']}/events"
        )

        self.assertEqual(len(listed.json()["videos"]), 1)
        self.assertFalse(listed.json()["videos"][0]["ready_for_qa"])
        self.assertEqual(detail.json()["source"]["youtube_video_id"], "abcdefghijk")
        self.assertEqual(detail.json()["source"]["source_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(job.json()["cost_cap_usd"], "0.500000")
        self.assertEqual(events.json()["events"][0]["event_type"], "created")
        for private in ("storage_key", "lease_owner", "provenance", "last_error_message"):
            self.assertNotIn(private, detail.text)
            self.assertNotIn(private, job.text)

    async def test_duplicate_invalid_url_and_upload_reservation_contract(self) -> None:
        self.assertEqual((await self.create_youtube()).status_code, 201)
        duplicate = await self.create_youtube(key=uuid4())
        invalid = await self.client.post(
            "/api/videos/youtube",
            headers={"Idempotency-Key": str(uuid4())},
            json={"url": "https://example.test/watch?v=abcdefghijk"},
        )
        upload = await self.client.post(
            "/api/videos/uploads",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "original_filename": "lecture.mp4",
                "content_type": "video/mp4",
                "content_length": 250_000_000,
            },
        )

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(upload.status_code, 201)
        self.assertEqual(upload.json()["ingestion_status"], "awaiting_upload")
        self.assertEqual(upload.json()["upload"]["method"], "put")
        self.assertTrue(upload.json()["upload"]["upload_url"].endswith("/source"))

    async def test_owner_isolation_patch_and_delete(self) -> None:
        created = await self.create_youtube()
        video_id = created.json()["video_id"]
        self.act_as(self.other_owner)
        self.assertEqual((await self.client.get(f"/api/videos/{video_id}")).status_code, 404)
        self.assertEqual((await self.client.patch(
            f"/api/videos/{video_id}", json={"title": "Stolen"}
        )).status_code, 404)
        self.assertEqual((await self.client.delete(f"/api/videos/{video_id}")).status_code, 404)

        self.act_as(self.owner)
        empty_patch = await self.client.patch(f"/api/videos/{video_id}", json={})
        null_title = await self.client.patch(
            f"/api/videos/{video_id}", json={"title": None}
        )
        patched = await self.client.patch(
            f"/api/videos/{video_id}",
            json={"title": "Updated", "description": "Visual lecture"},
        )
        self.assertEqual(empty_patch.status_code, 422)
        self.assertEqual(null_title.status_code, 422)
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["title"], "Updated")
        self.assertEqual((await self.client.delete(f"/api/videos/{video_id}")).status_code, 204)

    async def test_external_resource_attach_and_detach_preserves_record(self) -> None:
        created = await self.create_youtube()
        video_id = created.json()["video_id"]
        attached = await self.client.post(
            f"/api/videos/{video_id}/resources/links",
            json={
                "resource_kind": "external_link",
                "title": "Course notes",
                "url": "https://example.test/notes",
                "role": "notes",
            },
        )
        resource_id = attached.json()["resource_id"]
        listed = await self.client.get(f"/api/videos/{video_id}/resources")
        detached = await self.client.delete(
            f"/api/videos/{video_id}/resources/{resource_id}"
        )
        with connection(self.database_url) as database:
            canonical = database.execute(
                "select count(*) as count from video.resources where id = %s",
                (resource_id,),
            ).fetchone()["count"]

        self.assertEqual(attached.status_code, 201)
        self.assertEqual(listed.json()["resources"][0]["role"], "notes")
        self.assertEqual(detached.status_code, 204)
        self.assertEqual(canonical, 1)

    async def test_suggestion_confirm_and_dismiss_contracts(self) -> None:
        created = await self.create_youtube()
        video_id = created.json()["video_id"]
        with connection(self.database_url) as database:
            confirmed_id = database.execute(
                """
                insert into video.resource_suggestions (
                    owner_id, video_id, suggested_kind, url, normalized_url, title
                ) values (%s, %s, 'pdf', %s, %s, 'Slides') returning id
                """,
                (self.owner, video_id, "https://example.test/slides.pdf", "https://example.test/slides.pdf"),
            ).fetchone()["id"]
            dismissed_id = database.execute(
                """
                insert into video.resource_suggestions (
                    owner_id, video_id, suggested_kind, url, normalized_url
                ) values (%s, %s, 'external_link', %s, %s) returning id
                """,
                (self.owner, video_id, "https://example.test/notes", "https://example.test/notes"),
            ).fetchone()["id"]

        confirmed = await self.client.post(
            f"/api/videos/{video_id}/resource-suggestions/{confirmed_id}/confirm",
            json={"role": "slides"},
        )
        replay = await self.client.post(
            f"/api/videos/{video_id}/resource-suggestions/{confirmed_id}/confirm",
            json={"role": "slides"},
        )
        dismissed = await self.client.post(
            f"/api/videos/{video_id}/resource-suggestions/{dismissed_id}/dismiss"
        )
        suggestions = await self.client.get(
            f"/api/videos/{video_id}/resource-suggestions"
        )

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "pending")
        self.assertEqual(confirmed.json()["resource_id"], replay.json()["resource_id"])
        self.assertEqual(dismissed.json()["status"], "dismissed")
        self.assertEqual(len(suggestions.json()["suggestions"]), 2)


if __name__ == "__main__":
    unittest.main()
