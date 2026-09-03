"""How Storage responses are classified, where "gone" and "broken" diverge."""

from contextlib import contextmanager
import unittest
from unittest.mock import patch

import httpx

from ingestion.errors import ErrorCode, IngestionError
from ingestion.storage_objects import delete_object


def _client_returning(response: httpx.Response):
    """Patch the module's client so one canned response comes back."""

    class FakeClient:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def delete(self, url: str) -> httpx.Response:
            self.requests.append(url)
            return response

    fake = FakeClient()

    @contextmanager
    def factory(*args, **kwargs):
        yield fake

    return patch("ingestion.source_store.SupabaseSourceStore.client", factory), fake


class DeleteObjectTests(unittest.TestCase):
    """Storage answers a missing object with 400, not 404.

    The production retention sweep counted one absent file as a failed
    deletion on every hourly pass because this function read that 400 as an
    outage. `object_info` and `signed_object_url` already classified it
    correctly through `_reports_missing`; only deletion did not.
    """

    def test_a_missing_object_reports_gone_rather_than_broken(self) -> None:
        response = httpx.Response(
            400,
            json={
                "statusCode": "404",
                "error": "not_found",
                "message": "Object not found",
            },
            request=httpx.Request("DELETE", "http://storage.test/object"),
        )
        patcher, _ = _client_returning(response)
        with patcher:
            self.assertFalse(delete_object("book-sources", "owner/job/original.pdf"))

    def test_a_plain_404_still_reports_gone(self) -> None:
        response = httpx.Response(
            404, request=httpx.Request("DELETE", "http://storage.test/object")
        )
        patcher, _ = _client_returning(response)
        with patcher:
            self.assertFalse(delete_object("book-sources", "owner/job/original.pdf"))

    def test_a_deleted_object_reports_that_it_was_removed(self) -> None:
        response = httpx.Response(
            200,
            json={"message": "Successfully deleted"},
            request=httpx.Request("DELETE", "http://storage.test/object"),
        )
        patcher, _ = _client_returning(response)
        with patcher:
            self.assertTrue(delete_object("book-sources", "owner/job/original.pdf"))

    def test_a_real_refusal_still_raises_and_carries_the_body(self) -> None:
        """A 400 that is not `not_found` is a genuine failure, not an absence."""

        response = httpx.Response(
            403,
            json={"statusCode": "403", "error": "Unauthorized", "message": "denied"},
            request=httpx.Request("DELETE", "http://storage.test/object"),
        )
        patcher, _ = _client_returning(response)
        with patcher:
            with self.assertRaises(IngestionError) as raised:
                delete_object("book-sources", "owner/job/original.pdf")

        self.assertEqual(raised.exception.code, ErrorCode.STORAGE_UNAVAILABLE)
        # The key and the body: a deletion failing every hour is undiagnosable
        # from a status code alone.
        self.assertIn("owner/job/original.pdf", raised.exception.detail)
        self.assertIn("denied", raised.exception.detail)

    def test_a_400_without_a_json_body_is_treated_as_a_failure(self) -> None:
        response = httpx.Response(
            400,
            text="upstream connect error",
            request=httpx.Request("DELETE", "http://storage.test/object"),
        )
        patcher, _ = _client_returning(response)
        with patcher:
            with self.assertRaises(IngestionError):
                delete_object("book-sources", "owner/job/original.pdf")

    def test_the_object_key_reaches_storage_unaltered(self) -> None:
        response = httpx.Response(
            200, request=httpx.Request("DELETE", "http://storage.test/object")
        )
        patcher, fake = _client_returning(response)
        with patcher:
            delete_object("book-sources", "owner/job/original.pdf")

        self.assertEqual(
            fake.requests, ["/object/book-sources/owner/job/original.pdf"]
        )


if __name__ == "__main__":
    unittest.main()
