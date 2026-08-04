"""Supplied captions spare an uploaded lecture a transcript it already has."""

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
from tests.video_fixtures import encoded_video_bytes
from video.acquisition import MediaMetadata
from video.jobs import claim_next_job, get_job
from video.media_store import FilesystemMediaStore
from video.pipeline import VideoPipelineDependencies, run_video_stage
from video.repository import complete_video_upload, initialize_video_upload
from video.states import Stage


VTT = (
    "WEBVTT\n\n"
    "00:00.000 --> 00:04.000\nattention is all you need\n\n"
    "00:04.000 --> 00:09.500\nqueries keys and values are compared\n"
)
PAYLOAD = encoded_video_bytes()


class VideoCaptionUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {"VIDEO_MEDIA_ROOT": str(self.root / "media")}
        )
        self.environment.start()
        self.store = FilesystemMediaStore()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-captions.test"),
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

    def _uploaded_video(self, database):
        created = initialize_video_upload(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            original_filename="lecture.mp4",
            media_type="video/mp4",
            declared_size_bytes=len(PAYLOAD),
        )
        writer = self.store.writer(
            owner_id=self.owner,
            storage_key=created.upload_storage_key,
            maximum_bytes=len(PAYLOAD) + 1,
        )
        writer.write(PAYLOAD)
        staged = writer.finish(expected_size=len(PAYLOAD))
        complete_video_upload(
            database,
            created.job_id,
            owner_id=self.owner,
            storage_key=staged.storage_key,
            content_hash=staged.content_hash,
            size_bytes=staged.size_bytes,
            media_type="video/mp4",
        )
        return created

    async def test_captions_upload_before_the_video_has_a_duration(self) -> None:
        with connection(self.database_url) as database:
            created = self._uploaded_video(database)

        response = await self.client.put(
            f"/api/videos/{created.video_id}/captions",
            content=VTT.encode(),
            headers={
                "Content-Type": "text/vtt",
                "X-Caption-Filename": "lecture.en.vtt",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["cue_count"], 2)
        # Duration is unknown until ffprobe runs, so coverage is honestly null
        # rather than a number measured against nothing.
        self.assertIsNone(payload["coverage_ratio"])

        replay = await self.client.put(
            f"/api/videos/{created.video_id}/captions",
            content=VTT.encode(),
            headers={"Content-Type": "text/vtt"},
        )
        self.assertEqual(replay.json()["caption_id"], payload["caption_id"])

        with connection(self.database_url) as database:
            staged = database.execute(
                """
                select count(*) as count from video.caption_uploads
                where video_id = %s
                """,
                (created.video_id,),
            ).fetchone()["count"]
        self.assertEqual(staged, 1)

    async def test_a_malformed_caption_file_is_rejected_immediately(self) -> None:
        with connection(self.database_url) as database:
            created = self._uploaded_video(database)

        for body, content_type, expected in (
            (b"not a caption file at all", "text/vtt", 422),
            (b"", "text/vtt", 422),
            (VTT.encode(), "application/pdf", 415),
        ):
            response = await self.client.put(
                f"/api/videos/{created.video_id}/captions",
                content=body,
                headers={"Content-Type": content_type},
            )
            self.assertEqual(response.status_code, expected, content_type)

        missing = await self.client.put(
            f"/api/videos/{uuid4()}/captions",
            content=VTT.encode(),
            headers={"Content-Type": "text/vtt"},
        )
        self.assertEqual(missing.status_code, 404)

    async def test_another_owner_cannot_attach_captions(self) -> None:
        with connection(self.database_url) as database:
            created = self._uploaded_video(database)
        app.dependency_overrides[current_owner] = lambda: self.other_owner
        response = await self.client.put(
            f"/api/videos/{created.video_id}/captions",
            content=VTT.encode(),
            headers={"Content-Type": "text/vtt"},
        )
        self.assertEqual(response.status_code, 404)

    async def test_the_transcript_stage_uses_them_instead_of_paying(self) -> None:
        def refuse(*args, **kwargs):
            raise AssertionError("audio transcription must not run")

        def probe(path: Path) -> MediaMetadata:
            return MediaMetadata(
                duration_ms=10_000,
                width=1280,
                height=720,
                video_codec="h264",
                audio_codec="aac",
                format_name="mp4",
                size_bytes=path.stat().st_size,
            )

        dependencies = VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=lambda *args, **kwargs: None,
            media_probe=probe,
            audio_transcriber=refuse,
        )
        with connection(self.database_url) as database:
            created = self._uploaded_video(database)

        await self.client.put(
            f"/api/videos/{created.video_id}/captions",
            content=VTT.encode(),
            headers={"Content-Type": "text/vtt"},
        )

        with connection(self.database_url) as database:
            for expected in (
                Stage.ACQUIRE_SOURCE,
                Stage.MEDIA_METADATA,
                Stage.TRANSCRIPT,
            ):
                claimed = claim_next_job(
                    database, worker_id="video-worker", supported_stages={expected}
                )
                self.assertIsNotNone(claimed, expected)
                run_video_stage(
                    database,
                    job=claimed,
                    worker_id="video-worker",
                    work_dir=self.root / "work" / str(expected),
                    dependencies=dependencies,
                )
            transcript = database.execute(
                """
                select source_kind, provider, coverage_ratio, cost_usd,
                       (select count(*) from video.transcript_segments s
                        where s.transcript_source_id = t.id) as cues
                from video.transcript_sources t where t.video_id = %s
                """,
                (created.video_id,),
            ).fetchone()
            job = get_job(database, owner_id=self.owner, job_id=created.job_id)

        self.assertEqual(transcript["source_kind"], "uploaded_caption")
        self.assertEqual(transcript["provider"], "upload")
        self.assertEqual(transcript["cues"], 2)
        self.assertEqual(float(transcript["cost_usd"]), 0.0)
        # 9.5s of cues over a 10s video: enough coverage to skip the fallback.
        self.assertGreaterEqual(float(transcript["coverage_ratio"]), 0.9)
        self.assertEqual(float(job.actual_cost_usd), 0.0)
        self.assertEqual(job.stage, Stage.RESOURCES)


if __name__ == "__main__":
    unittest.main()
