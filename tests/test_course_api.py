"""HTTP contracts for the course aggregation boundary."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from storage.database import connection, resolve_database_url
from video.playlists import PlaylistLecture, YouTubePlaylist


class CourseApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@course-api.test"),
            )
        app.dependency_overrides[current_owner] = lambda: self.owner
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.clear()
        await self.client.aclose()
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    async def test_batch_list_reorder_detach_and_delete(self) -> None:
        key = str(uuid4())
        request = {
            "title": "Transformers",
            "lectures": [
                {"url": "https://youtu.be/abcdefghijk", "title": "One"},
                {"url": "https://youtu.be/lmnopqrstuv", "title": "Two"},
            ],
        }
        first = await self.client.post(
            "/api/courses/batch-youtube",
            headers={"Idempotency-Key": key},
            json=request,
        )
        replay = await self.client.post(
            "/api/courses/batch-youtube",
            headers={"Idempotency-Key": key},
            json=request,
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        course_id = first.json()["course_id"]

        listed = await self.client.get("/api/courses")
        detail = await self.client.get(f"/api/courses/{course_id}")
        videos = await self.client.get("/api/videos")
        self.assertEqual(listed.json()["courses"][0]["lecture_count"], 2)
        self.assertEqual(listed.json()["courses"][0]["processing_count"], 2)
        self.assertEqual(
            listed.json()["courses"][0]["preview_youtube_video_id"],
            "abcdefghijk",
        )
        self.assertEqual(
            detail.json()["preview_youtube_video_id"],
            "abcdefghijk",
        )
        self.assertEqual(
            [item["display_title"] for item in detail.json()["lectures"]],
            ["One", "Two"],
        )
        self.assertIn("timing", detail.json()["lectures"][0]["latest_ingestion"])
        self.assertEqual(videos.json()["videos"], [])

        course_lecture = await self.client.get(
            f"/api/videos/{detail.json()['lectures'][0]['video_id']}"
        )
        self.assertEqual(course_lecture.status_code, 200)

        second_id = detail.json()["lectures"][1]["video_id"]
        moved = await self.client.patch(
            f"/api/courses/{course_id}/lectures/{second_id}",
            json={"lecture_index": 0},
        )
        self.assertEqual(moved.json()["lectures"][0]["video_id"], second_id)
        detached = await self.client.delete(
            f"/api/courses/{course_id}/lectures/{second_id}"
        )
        self.assertEqual(detached.json()["lecture_count"], 1)
        self.assertEqual(len((await self.client.get("/api/videos")).json()["videos"]), 1)

        removed = await self.client.delete(f"/api/courses/{course_id}")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(len((await self.client.get("/api/videos")).json()["videos"]), 2)

    async def test_playlist_snapshot_creates_the_ordered_course_idempotently(self) -> None:
        key = str(uuid4())
        playlist = YouTubePlaylist(
            playlist_id="PLrw6a1wE39_tb2fErI4-WkMbsvGQk9_UB",
            canonical_url=(
                "https://www.youtube.com/playlist?"
                "list=PLrw6a1wE39_tb2fErI4-WkMbsvGQk9_UB"
            ),
            title="MIT 6.824 Spring 2020",
            description="Distributed systems lectures",
            lectures=(
                PlaylistLecture("abcdefghijk", "Lecture 1", 3600),
                PlaylistLecture("lmnopqrstuv", "Lecture 2", 3900),
            ),
        )
        with patch(
            "api.courses.discover_youtube_playlist", return_value=playlist
        ) as discover:
            first = await self.client.post(
                "/api/courses/from-youtube-playlist",
                headers={"Idempotency-Key": key},
                json={"playlist_url": playlist.canonical_url},
            )
            replay = await self.client.post(
                "/api/courses/from-youtube-playlist",
                headers={"Idempotency-Key": key},
                json={"playlist_url": playlist.canonical_url},
            )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json()["duration_seconds"], 7500)
        self.assertEqual(len(first.json()["lectures"]), 2)
        self.assertEqual(discover.call_count, 2)
        course_id = first.json()["course_id"]
        with connection(self.database_url) as database:
            stored = database.execute(
                "select title, metadata_json from video.courses where id = %s",
                (course_id,),
            ).fetchone()
        self.assertEqual(stored["title"], playlist.title)
        self.assertEqual(stored["metadata_json"]["playlist_id"], playlist.playlist_id)
        self.assertEqual(stored["metadata_json"]["lecture_count"], 2)


if __name__ == "__main__":
    unittest.main()
