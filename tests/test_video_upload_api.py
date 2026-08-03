"""Authenticated raw video uploads use durable streaming transport."""

from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from storage.database import connection, resolve_database_url


class VideoUploadApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        self.temporary = TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {
                "VIDEO_MEDIA_ROOT": self.temporary.name,
                "VIDEO_MAX_UPLOAD_BYTES": "1024",
            },
        )
        self.environment.start()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-upload.test"),
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
        self.environment.stop()
        self.temporary.cleanup()

    async def reserve(self, payload: bytes):
        return await self.client.post(
            "/api/videos/uploads",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "original_filename": "lecture.mp4",
                "content_type": "video/mp4",
                "content_length": len(payload),
            },
        )

    async def test_upload_is_atomic_queued_and_idempotent(self) -> None:
        payload = b"small-private-lecture"
        reserved = await self.reserve(payload)
        job_id = reserved.json()["ingestion_job_id"]
        upload_url = reserved.json()["upload"]["upload_url"]

        uploaded = await self.client.put(
            upload_url, content=payload, headers={"Content-Type": "video/mp4"}
        )
        replay = await self.client.put(
            upload_url, content=payload, headers={"Content-Type": "video/mp4"}
        )
        changed = await self.client.put(
            upload_url,
            content=b"changed-private-video",
            headers={"Content-Type": "video/mp4"},
        )

        with connection(self.database_url, readonly=True) as database:
            row = database.execute(
                """
                select j.status, j.staging_storage_key, j.staging_size_bytes,
                       j.staging_content_hash, j.upload_completed_at,
                       s.status as source_status, s.storage_backend,
                       s.storage_key, s.content_hash, s.size_bytes,
                       (select count(*) from video.ingestion_job_events e
                        where e.job_id = j.id
                          and e.event_type = 'upload_completed') as upload_events
                from video.ingestion_jobs j
                join video.video_sources s
                  on s.owner_id = j.owner_id and s.video_id = j.video_id
                where j.owner_id = %s and j.id = %s
                """,
                (self.owner, job_id),
            ).fetchone()

        self.assertEqual(uploaded.status_code, 202)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(uploaded.headers["Location"], f"/api/video-ingestions/{job_id}")
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["source_status"], "pending")
        self.assertEqual(row["storage_backend"], "filesystem")
        self.assertEqual(row["staging_size_bytes"], len(payload))
        self.assertEqual(row["content_hash"], sha256(payload).hexdigest())
        self.assertEqual(row["upload_events"], 1)
        self.assertIsNotNone(row["upload_completed_at"])
        self.assertEqual(
            (Path(self.temporary.name) / row["storage_key"]).read_bytes(), payload
        )

    async def test_owner_and_stream_failures_never_queue_partial_uploads(self) -> None:
        payload = b"12345"
        reserved = await self.reserve(payload)
        job_id = reserved.json()["ingestion_job_id"]
        upload_url = reserved.json()["upload"]["upload_url"]

        app.dependency_overrides[current_owner] = lambda: self.other_owner
        foreign = await self.client.put(
            upload_url, content=payload, headers={"Content-Type": "video/mp4"}
        )
        app.dependency_overrides[current_owner] = lambda: self.owner
        wrong_type = await self.client.put(
            upload_url, content=payload, headers={"Content-Type": "video/webm"}
        )
        short = await self.client.put(
            upload_url, content=b"1234", headers={"Content-Type": "video/mp4"}
        )
        os.environ["VIDEO_MAX_UPLOAD_BYTES"] = "3"
        streamed_over_cap = await self.client.put(
            upload_url,
            content=_chunks(b"12", b"345"),
            headers={"Content-Type": "video/mp4"},
        )

        with connection(self.database_url, readonly=True) as database:
            row = database.execute(
                """
                select status, staging_storage_key, staging_content_hash
                from video.ingestion_jobs where owner_id = %s and id = %s
                """,
                (self.owner, job_id),
            ).fetchone()

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(short.status_code, 422)
        self.assertEqual(streamed_over_cap.status_code, 413)
        self.assertEqual(row["status"], "awaiting_upload")
        self.assertIsNone(row["staging_content_hash"])
        self.assertFalse((Path(self.temporary.name) / row["staging_storage_key"]).exists())
        self.assertEqual(list(Path(self.temporary.name).rglob("*.part")), [])


async def _chunks(*values: bytes):
    for value in values:
        yield value


if __name__ == "__main__":
    unittest.main()
