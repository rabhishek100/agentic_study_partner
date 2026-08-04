"""Linked PDFs ingest page by page and stay separate from timestamps."""

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import fitz

from storage.database import connection, resolve_database_url
from video.evidence_store import evaluate_quality_gates, rebuild_evidence
from video.jobs import claim_next_job, get_job
from video.media_store import FilesystemMediaStore
from video.pipeline import VideoPipelineDependencies, run_video_stage
from video.repository import create_url_resource, create_youtube_video
from video.resources import (
    DownloadedResource,
    ResourceAcquisitionError,
    parse_pdf_pages,
)
from video.retrieval import retrieve_video_evidence
from video.states import Stage
from video.transcript_store import persist_transcript
from video.transcripts import parse_webvtt


SLIDES = (
    ("Scaled dot-product attention", "queries keys and values are compared"),
    ("Multi-head attention", "several attention heads run in parallel"),
    ("Positional encoding", "sinusoidal signals mark token order"),
)


def write_deck(path: Path, slides=SLIDES) -> Path:
    """Build a small landscape deck with one clearly largest title line."""

    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for title, body in slides:
        page = document.new_page(width=720, height=540)
        page.insert_text((60, 90), title, fontsize=34)
        # Body text must outweigh the title in characters so the profiler
        # measures the body size correctly.
        for offset in range(4):
            page.insert_text((60, 200 + offset * 24), body, fontsize=12)
    document.save(path)
    document.close()
    return path


class VideoResourceStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FilesystemMediaStore(self.root / "media")
        self.deck = write_deck(self.root / "source" / "slides.pdf")
        self.downloads: list[str] = []
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-resources.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))
        self.temporary.cleanup()

    def _downloader(self, source: Path | None = None):
        def download(url, destination, *, maximum_bytes, timeout=60.0):
            self.downloads.append(url)
            if source is None:
                raise ResourceAcquisitionError("resource download failed: 404")
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return DownloadedResource(
                path=destination,
                media_type="application/pdf",
                size_bytes=destination.stat().st_size,
                final_url=url,
            )

        return download

    def _dependencies(self, source: Path | None) -> VideoPipelineDependencies:
        return VideoPipelineDependencies(
            media_store=self.store,
            youtube_acquirer=lambda *args, **kwargs: None,
            pdf_downloader=self._downloader(source),
        )

    def _video_at_resources(self, database, *, title="Lecture"):
        created = create_youtube_video(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            url="https://youtu.be/abcdefghijk",
            title=title,
        )
        database.execute(
            "update video.videos set duration_ms = 10000 where id = %s",
            (created.video_id,),
        )
        database.execute(
            """
            update video.video_sources
            set status = 'ready', storage_backend = 'filesystem',
                storage_key = %s, content_hash = %s, size_bytes = 100,
                media_type = 'video/mp4', acquired_at = now()
            where id = %s
            """,
            (f"{self.owner}/canonical/videos/v.mp4", "a" * 64, created.source_id),
        )
        claimed = claim_next_job(database, worker_id="video-worker")
        database.execute(
            "update video.ingestion_jobs set stage = 'resources' where id = %s",
            (created.job_id,),
        )
        return created, claimed

    def _claim_at(self, database, created, stage: Stage = Stage.RESOURCES):
        database.execute(
            """
            update video.ingestion_jobs
            set stage = %s, status = 'running', lease_owner = 'video-worker',
                lease_expires_at = now() + interval '5 minutes'
            where id = %s
            """,
            (str(stage), created.job_id),
        )
        return get_job(database, owner_id=self.owner, job_id=created.job_id)

    def test_parses_a_deck_into_titled_lossless_pages(self) -> None:
        parsed = parse_pdf_pages(self.deck)

        self.assertEqual(parsed.page_count, 3)
        self.assertTrue(parsed.is_slide_deck)
        self.assertEqual(
            [page.title for page in parsed.pages],
            [title for title, _ in SLIDES],
        )
        self.assertIn("queries keys and values", parsed.pages[0].text)
        self.assertEqual([page.page_number for page in parsed.pages], [1, 2, 3])
        self.assertEqual(parsed.provenance["empty_page_count"], 0)
        self.assertEqual(len({page.content_hash for page in parsed.pages}), 3)

    def test_downloads_parses_and_publishes_a_linked_deck(self) -> None:
        with connection(self.database_url) as database:
            created, claimed = self._video_at_resources(database)
            resource = create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="CME295 slides",
                source_url="https://example.test/slides.pdf",
                role="slides",
                required=True,
            )
            job = self._claim_at(database, created)
            outcome = run_video_stage(
                database,
                job=job,
                worker_id="video-worker",
                work_dir=self.root / "work" / "resources",
                dependencies=self._dependencies(self.deck),
            )
            stored = database.execute(
                """
                select status, page_count, media_type, storage_key, content_hash,
                       size_bytes, provenance_json
                from video.resources where id = %s
                """,
                (resource["id"],),
            ).fetchone()
            pages = database.execute(
                """
                select page_number, text_content, layout_json, parser_version
                from video.resource_pages where resource_id = %s
                order by page_number
                """,
                (resource["id"],),
            ).fetchall()
            manifest = database.execute(
                """
                select output_manifest_json from video.ingestion_stage_checkpoints
                where ingestion_version_id = %s and stage = 'resources'
                """,
                (created.version_id,),
            ).fetchone()["output_manifest_json"]

        self.assertEqual(outcome.stage, Stage.FRAME_SELECTION)
        self.assertEqual(self.downloads, ["https://example.test/slides.pdf"])
        self.assertEqual(stored["status"], "ready")
        self.assertEqual(stored["page_count"], 3)
        self.assertEqual(stored["media_type"], "application/pdf")
        self.assertTrue(stored["storage_key"].startswith(f"{self.owner}/canonical/resources/"))
        self.assertTrue(stored["provenance_json"]["is_slide_deck"])
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[1]["layout_json"]["title"], "Multi-head attention")
        self.assertIn("several attention heads", pages[1]["text_content"])
        self.assertEqual(manifest["ready_count"], 1)
        self.assertEqual(manifest["page_count"], 3)

    def test_a_failed_document_never_fails_the_video(self) -> None:
        with connection(self.database_url) as database:
            created, _ = self._video_at_resources(database)
            resource = create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="Missing slides",
                source_url="https://example.test/gone.pdf",
                role="slides",
                required=False,
            )
            job = self._claim_at(database, created)
            outcome = run_video_stage(
                database,
                job=job,
                worker_id="video-worker",
                work_dir=self.root / "work" / "failed",
                dependencies=self._dependencies(None),
            )
            stored = database.execute(
                "select status, provenance_json from video.resources where id = %s",
                (resource["id"],),
            ).fetchone()
            manifest = database.execute(
                """
                select output_manifest_json from video.ingestion_stage_checkpoints
                where ingestion_version_id = %s and stage = 'resources'
                """,
                (created.version_id,),
            ).fetchone()["output_manifest_json"]

        # The video keeps ingesting: slides are supporting material.
        self.assertEqual(outcome.stage, Stage.FRAME_SELECTION)
        self.assertEqual(stored["status"], "failed")
        self.assertIn("404", stored["provenance_json"]["failure_reason"])
        self.assertEqual(manifest["failed_count"], 1)
        self.assertEqual(manifest["page_count"], 0)

    def test_adding_a_second_deck_reuses_the_first(self) -> None:
        second = write_deck(
            self.root / "source" / "notes.pdf",
            slides=(("Course logistics", "office hours and grading"),),
        )
        with connection(self.database_url) as database:
            created, _ = self._video_at_resources(database)
            create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="CME295 slides",
                source_url="https://example.test/slides.pdf",
                role="slides",
            )
            job = self._claim_at(database, created)
            run_video_stage(
                database,
                job=job,
                worker_id="video-worker",
                work_dir=self.root / "work" / "first",
                dependencies=self._dependencies(self.deck),
            )
            create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="Course notes",
                source_url="https://example.test/notes.pdf",
                role="notes",
            )
            job = self._claim_at(database, created)
            run_video_stage(
                database,
                job=job,
                worker_id="video-worker",
                work_dir=self.root / "work" / "second",
                dependencies=self._dependencies(second),
            )
            manifest = database.execute(
                """
                select output_manifest_json from video.ingestion_stage_checkpoints
                where ingestion_version_id = %s and stage = 'resources'
                """,
                (created.version_id,),
            ).fetchone()["output_manifest_json"]
            page_totals = database.execute(
                """
                select count(*) as count from video.resource_pages
                where owner_id = %s
                """,
                (self.owner,),
            ).fetchone()["count"]

        self.assertEqual(self.downloads, ["https://example.test/slides.pdf"] * 1 + ["https://example.test/notes.pdf"])
        self.assertEqual(
            sorted(item["status"] for item in manifest["resources"]),
            ["ready", "reused"],
        )
        self.assertEqual(page_totals, 4)

    def test_pages_become_citable_evidence_without_timestamps(self) -> None:
        with connection(self.database_url) as database:
            created, claimed = self._video_at_resources(database)
            create_url_resource(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                resource_kind="pdf",
                title="CME295 slides",
                source_url="https://example.test/slides.pdf",
                role="slides",
            )
            job = self._claim_at(database, created)
            run_video_stage(
                database,
                job=job,
                worker_id="video-worker",
                work_dir=self.root / "work" / "evidence",
                dependencies=self._dependencies(self.deck),
            )
            transcript = persist_transcript(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                video_source_id=created.source_id,
                source_kind="youtube_caption",
                language="en",
                provider="youtube",
                storage_backend="filesystem",
                storage_key=f"{self.owner}/canonical/transcripts/c.vtt",
                content_hash="b" * 64,
                cues=parse_webvtt(
                    "WEBVTT\n\n00:00.000 --> 00:10.000\nwelcome to the lecture\n"
                ),
            )
            job = self._claim_at(database, created, Stage.INDEXING)
            built = rebuild_evidence(
                database,
                job_id=created.job_id,
                worker_id="video-worker",
                attempt_count=job.attempt_count,
                transcript_source_id=transcript.id,
            )
            evidence = database.execute(
                """
                select modality, page_number, start_ms
                from video.evidence_units
                where ingestion_version_id = %s and modality = 'resource_page'
                order by page_number
                """,
                (created.version_id,),
            ).fetchall()
            database.execute(
                """
                update video.ingestion_versions
                set status = 'degraded', completed_at = now(), published_at = now(),
                    quality_gates_json = '{"readiness":"degraded"}'::jsonb
                where id = %s
                """,
                (created.version_id,),
            )
            database.execute(
                """
                update video.videos
                set readiness_status = 'degraded',
                    current_ingestion_version_id = %s, ready_at = now()
                where id = %s
                """,
                (created.version_id, created.video_id),
            )
            _, found = retrieve_video_evidence(
                database,
                owner_id=self.owner,
                video_id=created.video_id,
                query="sinusoidal",
                limit=4,
            )

        self.assertEqual(built.resource_page_count, 3)
        self.assertEqual(built.total_count, 4)
        self.assertEqual([row["page_number"] for row in evidence], [1, 2, 3])
        self.assertTrue(all(row["start_ms"] is None for row in evidence))
        page = next(item for item in found if item.modality == "resource_page")
        self.assertEqual(page.page_number, 3)
        # A slide page is cited by page, never by a timestamp it never had.
        self.assertIsNone(page.start_ms)
        self.assertIn("Positional encoding", page.text)


if __name__ == "__main__":
    unittest.main()
