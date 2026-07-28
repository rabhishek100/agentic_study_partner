"""Signing short-lived URLs for private source objects."""

import unittest
from unittest.mock import patch

import httpx

from ingestion.errors import IngestionError
from ingestion.storage_objects import signed_object_url


def client_returning(status: int, payload: dict) -> httpx.Client:
    return httpx.Client(
        base_url="https://project.supabase.co/storage/v1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload)
        ),
    )


class SignedUrlTests(unittest.TestCase):
    def sign(self, status: int, payload: dict):
        with patch("ingestion.storage_objects.storage_client") as factory:
            factory.return_value.__enter__.return_value = client_returning(
                status, payload
            )
            return signed_object_url("book-sources", "owner/job/original.pdf")

    def test_a_signed_path_is_returned_as_an_absolute_url(self):
        with patch.dict(
            "os.environ", {"SUPABASE_URL": "https://project.supabase.co"}
        ):
            url = self.sign(
                200,
                {"signedURL": "/object/sign/book-sources/o/j/original.pdf?token=x"},
            )
        self.assertTrue(url.startswith("https://project.supabase.co/storage/v1/"))
        self.assertIn("token=x", url)

    def test_a_missing_object_reads_as_gone_not_as_an_outage(self):
        """Storage answers 400 with a not_found body rather than a 404."""

        self.assertIsNone(
            self.sign(400, {"statusCode": "404", "error": "not_found"})
        )

    def test_a_real_outage_still_raises(self):
        with self.assertRaises(IngestionError):
            self.sign(500, {"error": "internal"})

    def test_a_response_without_a_url_raises(self):
        with self.assertRaises(IngestionError):
            self.sign(200, {})

    def test_a_non_positive_expiry_is_rejected(self):
        with self.assertRaises(ValueError):
            signed_object_url("book-sources", "o/j/original.pdf", expires_in=0)


if __name__ == "__main__":
    unittest.main()
