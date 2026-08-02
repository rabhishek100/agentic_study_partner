"""Signing short-lived links to a book's original PDF."""

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from ingestion.errors import ErrorCode, IngestionError


OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")


@contextmanager
def stored_book(
    bucket="book-sources",
    path="owner/job/original.pdf",
    pages=386,
    viewer_bucket=None,
    viewer_path=None,
):
    """A ready book with, or without, a stored source and a viewer copy."""

    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = {
        "source_storage_bucket": bucket,
        "source_storage_path": path,
        "viewer_storage_bucket": viewer_bucket,
        "viewer_storage_path": viewer_path,
        "page_count": pages,
    }
    with (
        patch("api.main.database_connection") as open_connection,
        patch("api.main.ready_book", return_value={"id": 7, "status": "ready"}),
        patch("api.main.signed_object_url") as sign,
    ):
        open_connection.return_value.__enter__.return_value = connection
        sign.return_value = "https://storage.test/signed?token=abc"
        yield sign


class BookSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_a_ready_book_returns_a_signed_url_and_expiry(self):
        with stored_book() as sign:
            response = await self.client.get("/api/books/7/source")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["url"], "https://storage.test/signed?token=abc")
        self.assertEqual(payload["page_count"], 386)
        self.assertTrue(payload["expires_at"])
        # The link is short-lived rather than permanent.
        self.assertLessEqual(sign.call_args.kwargs["expires_in"], 3600)

    async def test_a_book_the_caller_cannot_use_is_refused(self):
        connection = MagicMock()
        with (
            patch("api.main.database_connection") as open_connection,
            patch("api.main.ready_book", return_value=None),
        ):
            open_connection.return_value.__enter__.return_value = connection
            response = await self.client.get("/api/books/4242/source")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "book not found")

    async def test_a_book_with_no_stored_object_is_explained(self):
        """Books imported by the manual CLI never had a stored source."""

        with stored_book(bucket=None, path=None):
            response = await self.client.get("/api/books/7/source")

        self.assertEqual(response.status_code, 404)
        self.assertIn("no longer stored", response.json()["detail"])

    async def test_a_deleted_object_is_explained_rather_than_broken(self):
        with stored_book() as sign:
            sign.return_value = None
            response = await self.client.get("/api/books/7/source")

        self.assertEqual(response.status_code, 404)
        self.assertIn("no longer stored", response.json()["detail"])

    async def test_a_storage_outage_is_a_503_not_a_missing_book(self):
        # "Temporarily unavailable" and "gone" must not look the same.
        with stored_book() as sign:
            sign.side_effect = IngestionError(ErrorCode.STORAGE_UNAVAILABLE)
            response = await self.client.get("/api/books/7/source")

        self.assertEqual(response.status_code, 503)

    async def test_the_endpoint_requires_authentication(self):
        app.dependency_overrides.clear()
        response = await self.client.get("/api/books/7/source")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class ViewerCopyPreferenceTests(unittest.IsolatedAsyncioTestCase):
    """Books too large to store their own bytes are read from a rendering."""

    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_a_viewer_copy_is_served_instead_of_the_source(self):
        with stored_book(
            viewer_bucket="book-sources", viewer_path="owner/book-536/viewer.pdf"
        ) as sign:
            response = await self.client.get(
                "/api/books/7/source"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sign.call_args.args, ("book-sources", "owner/book-536/viewer.pdf")
        )

    async def test_the_source_is_served_when_there_is_no_viewer_copy(self):
        with stored_book() as sign:
            response = await self.client.get(
                "/api/books/7/source"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sign.call_args.args, ("book-sources", "owner/job/original.pdf")
        )

    async def test_a_viewer_copy_rescues_a_book_whose_source_was_never_stored(self):
        """The operator ingest path uploads nothing, so this is its only source."""

        with stored_book(
            bucket=None,
            path=None,
            viewer_bucket="book-sources",
            viewer_path="owner/book-536/viewer.pdf",
        ) as sign:
            response = await self.client.get(
                "/api/books/7/source"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sign.call_args.args, ("book-sources", "owner/book-536/viewer.pdf")
        )
