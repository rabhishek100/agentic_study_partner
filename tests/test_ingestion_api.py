"""Ingestion lifecycle endpoints against local Postgres.

Storage is mocked because these tests are about the API contract: what is
verified, what is idempotent, what a caller is allowed to see, and what a
failure discloses.
"""

import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from ingestion.jobs import get_job, pause_for_outline_review
from ingestion.states import Stage, Status
from ingestion.storage_objects import ObjectInfo
from storage.database import connection, resolve_database_url


class StoredObject:
    """Stand-in for one verified object in the private bucket."""

    def __init__(self, *, size=2048, content_type="application/pdf", uploader=None):
        self.info = ObjectInfo(
            bucket="book-sources",
            path="unused",
            size_bytes=size,
            content_type=content_type,
            etag="etag",
            version="1",
        )
        self.uploader = uploader

    def __enter__(self):
        self._info = patch("api.ingestions.object_info", return_value=self.info)
        self._uploader = patch(
            "api.ingestions.object_uploader", return_value=self.uploader
        )
        self._info.start()
        self._uploader.start()
        return self

    def __exit__(self, *exception):
        self._info.stop()
        self._uploader.stop()
        return False


def missing_object():
    return patch("api.ingestions.object_info", return_value=None)


class IngestionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        self.other_owner = uuid4()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@test.local"),
                )

        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: self.owner

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )

    def act_as(self, owner):
        app.dependency_overrides[current_owner] = lambda: owner

    async def create(self, *, filename="book.pdf", content_type="application/pdf",
                     content_length=2048, key=None):
        return await self.client.post(
            "/api/ingestions",
            headers={"Idempotency-Key": str(key or uuid4())},
            json={
                "original_filename": filename,
                "content_type": content_type,
                "content_length": content_length,
            },
        )

    async def create_and_queue(self):
        created = await self.create()
        job_id = created.json()["job_id"]
        with StoredObject():
            await self.client.post(f"/api/ingestions/{job_id}/complete")
        return job_id

    async def create_review_job(self):
        job_id = await self.create_and_queue()
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'validating', stage = 'preflight',
                    file_hash = %s, page_count = 6
                where id = %s
                """,
                ("c" * 64, job_id),
            )
            pause_for_outline_review(
                database,
                owner_id=self.owner,
                job_id=job_id,
                proposal=[
                    (1, "Chapter 1", 1),
                    (2, "First section", 2),
                    (1, "Chapter 2", 4),
                ],
                reasons=("invalid_destinations", "incomplete_coverage"),
                outline_source="deterministic_proposal",
                proposer_version="proposal-test-v1",
            )
        return job_id

    async def test_creating_a_job_reserves_an_owner_scoped_path(self):
        response = await self.create()

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["status"], "awaiting_upload")
        self.assertEqual(payload["storage_bucket"], "book-sources")
        self.assertEqual(
            payload["storage_path"],
            f"{self.owner}/{payload['job_id']}/original.pdf",
        )
        self.assertEqual(payload["maximum_bytes"], 52_428_800)
        self.assertEqual(payload["upload_method"], "tus")

    async def test_replaying_an_idempotency_key_returns_the_same_job(self):
        key = uuid4()

        first = await self.create(key=key)
        second = await self.create(key=key, filename="renamed.pdf")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(
            first.json()["storage_path"], second.json()["storage_path"]
        )

    async def test_an_idempotency_key_is_required_and_must_be_a_uuid(self):
        without = await self.client.post(
            "/api/ingestions",
            json={"original_filename": "book.pdf", "content_type": "application/pdf"},
        )
        malformed = await self.client.post(
            "/api/ingestions",
            headers={"Idempotency-Key": "not-a-uuid"},
            json={"original_filename": "book.pdf", "content_type": "application/pdf"},
        )

        self.assertEqual(without.status_code, 400)
        self.assertEqual(malformed.status_code, 400)

    async def test_declared_metadata_is_rejected_before_any_upload(self):
        oversize = await self.create(content_length=110_000_000)
        wrong_type = await self.create(content_type="image/png")

        self.assertEqual(oversize.status_code, 413)
        self.assertEqual(oversize.json()["detail"]["code"], "source_too_large")
        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(
            wrong_type.json()["detail"]["code"], "unsupported_content_type"
        )

    async def test_a_pdf_up_to_the_limit_can_be_reserved(self):
        response = await self.create(content_length=52_428_800)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["maximum_bytes"], 52_428_800)

    async def test_a_pdf_over_the_limit_is_refused_before_any_upload(self):
        """The browser reads this same number, so an oversized file is named
        as such instead of reaching Storage and coming back as a bare 413."""

        response = await self.create(content_length=52_428_801)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["code"], "source_too_large")

    async def test_the_active_limits_are_served_to_the_browser(self):
        response = await self.client.get("/api/ingestions/limits")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["maximum_bytes"], 52_428_800)
        self.assertEqual(payload["maximum_pages"], 1000)
        self.assertEqual(payload["allowed_content_types"], ["application/pdf"])

    async def test_the_pending_quota_is_enforced_per_owner(self):
        for _ in range(3):
            self.assertEqual((await self.create()).status_code, 201)

        blocked = await self.create()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["detail"]["code"], "quota_exceeded")

        self.act_as(self.other_owner)
        self.assertEqual((await self.create()).status_code, 201)

    async def test_every_endpoint_requires_authentication(self):
        app.dependency_overrides.clear()
        job_id = uuid4()

        for method, path in (
            ("post", "/api/ingestions"),
            ("get", "/api/ingestions"),
            ("get", f"/api/ingestions/{job_id}"),
            ("post", f"/api/ingestions/{job_id}/complete"),
            ("post", f"/api/ingestions/{job_id}/cancel"),
            ("post", f"/api/ingestions/{job_id}/retry"),
            ("get", f"/api/ingestions/{job_id}/toc-proposal"),
            ("post", f"/api/ingestions/{job_id}/toc-confirmation"),
        ):
            with self.subTest(path=path, method=method):
                arguments = (
                    {
                        "json": {
                            "entries": [
                                {"level": 1, "title": "Chapter 1", "page": 1}
                            ]
                        }
                    }
                    if path.endswith("/toc-confirmation")
                    else ({"json": {}} if method == "post" else {})
                )
                response = await getattr(self.client, method)(path, **arguments)
                self.assertEqual(response.status_code, 401)

    async def test_completing_an_upload_queues_the_job(self):
        created = await self.create()
        job_id = created.json()["job_id"]

        with StoredObject(size=4096):
            response = await self.client.post(f"/api/ingestions/{job_id}/complete")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["Location"], f"/api/ingestions/{job_id}")
        self.assertEqual(response.json()["status"], "queued")

        with connection(self.database_url) as database:
            job = get_job(database, owner_id=self.owner, job_id=job_id)
        self.assertEqual(job.status, Status.QUEUED)
        self.assertEqual(job.verified_size_bytes, 4096)

    async def test_completing_twice_is_idempotent(self):
        created = await self.create()
        job_id = created.json()["job_id"]

        with StoredObject():
            first = await self.client.post(f"/api/ingestions/{job_id}/complete")
            second = await self.client.post(f"/api/ingestions/{job_id}/complete")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["status"], "queued")

    async def test_completing_without_an_uploaded_object_is_refused(self):
        created = await self.create()
        job_id = created.json()["job_id"]

        with missing_object():
            response = await self.client.post(f"/api/ingestions/{job_id}/complete")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "source_missing")

    async def test_completing_verifies_the_stored_object_not_the_claim(self):
        created = await self.create(content_length=2048)
        job_id = created.json()["job_id"]

        with StoredObject(size=110_000_000):
            oversize = await self.client.post(f"/api/ingestions/{job_id}/complete")
        self.assertEqual(oversize.status_code, 413)

        with StoredObject(size=0):
            empty = await self.client.post(f"/api/ingestions/{job_id}/complete")
        self.assertEqual(empty.status_code, 422)

        with StoredObject(content_type="image/png"):
            wrong_type = await self.client.post(f"/api/ingestions/{job_id}/complete")
        self.assertEqual(wrong_type.status_code, 415)

        with connection(self.database_url) as database:
            job = get_job(database, owner_id=self.owner, job_id=job_id)
        self.assertEqual(job.status, Status.AWAITING_UPLOAD)

    async def test_an_object_uploaded_by_another_user_is_refused(self):
        created = await self.create()
        job_id = created.json()["job_id"]

        with StoredObject(uploader=str(self.other_owner)):
            response = await self.client.post(f"/api/ingestions/{job_id}/complete")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "source_missing")

    async def test_status_polling_reports_durable_progress(self):
        job_id = await self.create_and_queue()
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'parsing', stage = 'parse_pages',
                    progress_completed = 275, progress_total = 640,
                    progress_unit = 'pages', attempt_count = 1
                where id = %s
                """,
                (job_id,),
            )

        response = await self.client.get(f"/api/ingestions/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "parsing")
        self.assertEqual(payload["stage"], "parse_pages")
        self.assertEqual(payload["progress"]["completed"], 275)
        self.assertEqual(payload["progress"]["total"], 640)
        self.assertEqual(payload["progress"]["unit"], "pages")
        self.assertAlmostEqual(payload["progress"]["percent"], 42.97, places=2)
        self.assertEqual(payload["attempt"], 1)
        self.assertIsNone(payload["error"])
        self.assertIsNone(payload["book_id"])

    async def test_one_owner_cannot_see_or_touch_another_owners_job(self):
        job_id = await self.create_and_queue()
        self.act_as(self.other_owner)

        for method, path in (
            ("get", f"/api/ingestions/{job_id}"),
            ("post", f"/api/ingestions/{job_id}/complete"),
            ("post", f"/api/ingestions/{job_id}/cancel"),
            ("post", f"/api/ingestions/{job_id}/retry"),
            ("get", f"/api/ingestions/{job_id}/toc-proposal"),
            ("post", f"/api/ingestions/{job_id}/toc-confirmation"),
        ):
            with self.subTest(path=path):
                arguments = (
                    {
                        "json": {
                            "entries": [
                                {"level": 1, "title": "Chapter 1", "page": 1}
                            ]
                        }
                    }
                    if path.endswith("/toc-confirmation")
                    else {}
                )
                response = await getattr(self.client, method)(path, **arguments)
                self.assertEqual(response.status_code, 404)

        listed = await self.client.get("/api/ingestions")
        self.assertEqual(listed.json()["jobs"], [])

        with connection(self.database_url) as database:
            job = get_job(database, owner_id=self.owner, job_id=job_id)
        self.assertEqual(job.status, Status.QUEUED)

    async def test_outline_proposal_can_be_read_and_confirmed(self):
        job_id = await self.create_review_job()

        proposal = await self.client.get(
            f"/api/ingestions/{job_id}/toc-proposal"
        )
        self.assertEqual(proposal.status_code, 200)
        payload = proposal.json()
        self.assertEqual(payload["status"], "needs_toc_review")
        self.assertEqual(payload["page_count"], 6)
        self.assertEqual(payload["proposer_version"], "proposal-test-v1")
        self.assertEqual(
            payload["reasons"],
            ["invalid_destinations", "incomplete_coverage"],
        )
        self.assertEqual(payload["entries"][1]["title"], "First section")

        confirmed = await self.client.post(
            f"/api/ingestions/{job_id}/toc-confirmation",
            json={"entries": payload["entries"]},
        )

        self.assertEqual(confirmed.status_code, 202)
        self.assertEqual(confirmed.headers["Location"], f"/api/ingestions/{job_id}")
        self.assertEqual(confirmed.json()["status"], "queued")
        self.assertEqual(confirmed.json()["stage"], "preflight")

    async def test_invalid_outline_confirmation_stays_in_review(self):
        job_id = await self.create_review_job()

        response = await self.client.post(
            f"/api/ingestions/{job_id}/toc-confirmation",
            json={
                "entries": [
                    {"level": 1, "title": "Chapter 2", "page": 4},
                    {"level": 2, "title": "Backwards", "page": 2},
                ]
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_hierarchy")
        with connection(self.database_url) as database:
            job = get_job(database, owner_id=self.owner, job_id=job_id)
        self.assertEqual(job.status, Status.NEEDS_TOC_REVIEW)
        self.assertEqual(job.stage, Stage.PROPOSE_TOC)

    async def test_an_unknown_job_is_indistinguishable_from_a_forbidden_one(self):
        response = await self.client.get(f"/api/ingestions/{uuid4()}")

        self.assertEqual(response.status_code, 404)

    async def test_cancelling_a_queued_job_stops_it_immediately(self):
        job_id = await self.create_and_queue()

        response = await self.client.post(f"/api/ingestions/{job_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")

    async def test_cancelling_a_running_job_is_cooperative(self):
        job_id = await self.create_and_queue()
        with connection(self.database_url) as database:
            database.execute(
                "update ingestion_jobs set status = 'parsing', stage = 'parse_pages' "
                "where id = %s",
                (job_id,),
            )

        response = await self.client.post(f"/api/ingestions/{job_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "parsing")
        self.assertTrue(response.json()["cancellation_requested"])

    async def test_only_a_retryable_failure_can_be_retried(self):
        job_id = await self.create_and_queue()
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'failed', last_error_code = 'encrypted_pdf',
                    last_error_message = 'Password-protected PDFs are not supported.',
                    last_error_retryable = false, completed_at = now()
                where id = %s
                """,
                (job_id,),
            )

        status_response = await self.client.get(f"/api/ingestions/{job_id}")
        retry = await self.client.post(f"/api/ingestions/{job_id}/retry")

        self.assertFalse(status_response.json()["retryable"])
        self.assertEqual(status_response.json()["error"]["code"], "encrypted_pdf")
        self.assertEqual(retry.status_code, 409)

    async def test_a_retryable_failure_can_be_requeued(self):
        job_id = await self.create_and_queue()
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'failed', last_error_code = 'storage_unavailable',
                    last_error_message = 'File storage was temporarily unavailable.',
                    last_error_retryable = true, attempt_count = 2,
                    completed_at = now()
                where id = %s
                """,
                (job_id,),
            )

        status_response = await self.client.get(f"/api/ingestions/{job_id}")
        retry = await self.client.post(f"/api/ingestions/{job_id}/retry")

        self.assertTrue(status_response.json()["retryable"])
        self.assertEqual(retry.status_code, 200)
        # A manual retry is an immediately eligible scheduled retry, so the
        # worker resumes from the failed stage instead of re-parsing.
        self.assertEqual(retry.json()["status"], "retry_scheduled")
        self.assertEqual(retry.json()["attempt"], 0)
        self.assertIsNone(retry.json()["error"])

    async def test_failure_responses_never_expose_internal_detail(self):
        job_id = await self.create_and_queue()
        with connection(self.database_url) as database:
            database.execute(
                """
                update ingestion_jobs
                set status = 'failed', last_error_code = 'invalid_pdf',
                    last_error_message = 'This file is not a readable PDF.',
                    last_error_retryable = false, completed_at = now()
                where id = %s
                """,
                (job_id,),
            )

        body = (await self.client.get(f"/api/ingestions/{job_id}")).text

        for leak in ("/tmp", "psycopg", "Traceback", "select ", "postgresql://"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)

    async def test_listing_returns_this_owners_jobs_newest_first(self):
        first = (await self.create()).json()["job_id"]
        second = (await self.create()).json()["job_id"]

        response = await self.client.get("/api/ingestions")

        self.assertEqual(response.status_code, 200)
        listed = [job["job_id"] for job in response.json()["jobs"]]
        self.assertEqual(set(listed), {first, second})
        self.assertEqual(listed[0], second)


if __name__ == "__main__":
    unittest.main()
