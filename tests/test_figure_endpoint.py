"""Serving canonical figures over the API."""

import base64
import unittest
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from storage.database import connection as database_connection, resolve_database_url
from storage.postgres import ingest_book
from tests.fixtures import FILE_HASH, sample_book


IMAGE_BYTES = base64.b64decode("aW1hZ2U=")


class FigureEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database_url = resolve_database_url()
        self.owner_id = uuid4()
        self.stranger_id = uuid4()
        with database_connection(self.database_url) as connection:
            for owner in (self.owner_id, self.stranger_id):
                connection.execute(
                    """
                    insert into auth.users (id, email, raw_user_meta_data)
                    values (%s, %s, '{}'::jsonb)
                    """,
                    (owner, f"{owner}@test.local"),
                )
            self.book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Sample Book",
                author=None,
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
            self.block_id = connection.execute(
                """
                select block_id from image_blocks
                where book_id = %s and owner_id = %s
                """,
                (self.book_id, self.owner_id),
            ).fetchone()["block_id"]

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        app.dependency_overrides[current_owner] = lambda: self.owner_id

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()
        with database_connection(self.database_url) as connection:
            connection.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_id, self.stranger_id],),
            )

    def path(self, book_id: int | None = None, block_id: int | None = None) -> str:
        return (
            f"/api/books/{book_id or self.book_id}"
            f"/blocks/{block_id or self.block_id}/image"
        )

    async def test_a_figure_is_served_with_its_stored_mime_type(self):
        response = await self.client.get(self.path())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, IMAGE_BYTES)
        self.assertEqual(response.headers["content-type"], "image/png")

    async def test_the_response_is_cacheable_forever(self):
        """Canonical content is immutable, so a figure never revalidates."""

        response = await self.client.get(self.path())

        self.assertIn("immutable", response.headers["cache-control"])
        self.assertIn("private", response.headers["cache-control"])
        self.assertTrue(response.headers["etag"])

    async def test_a_matching_etag_is_answered_with_304(self):
        first = await self.client.get(self.path())
        second = await self.client.get(
            self.path(),
            headers={"If-None-Match": first.headers["etag"]},
        )

        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b"")

    async def test_another_owners_figure_is_indistinguishable_from_missing(self):
        app.dependency_overrides[current_owner] = lambda: self.stranger_id

        response = await self.client.get(self.path())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "image not found")

    async def test_a_block_from_a_different_book_is_refused(self):
        response = await self.client.get(self.path(book_id=self.book_id + 9999))
        self.assertEqual(response.status_code, 404)

    async def test_a_missing_block_is_refused(self):
        response = await self.client.get(self.path(block_id=99_999_999))
        self.assertEqual(response.status_code, 404)

    async def test_the_endpoint_requires_authentication(self):
        app.dependency_overrides.clear()
        response = await self.client.get(self.path())
        self.assertEqual(response.status_code, 401)
        # Restored by asyncTearDown; set here so teardown has a valid override.
        app.dependency_overrides[current_owner] = lambda: self.owner_id


if __name__ == "__main__":
    unittest.main()
