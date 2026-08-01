"""Captioning figures at ingest, and what it deliberately skips."""

from base64 import b64encode
import unittest

from ingestion.captions import (
    BOILERPLATE_REPEAT_THRESHOLD,
    MINIMUM_FIGURE_BYTES,
    caption_book_figures,
    content_hash,
    load_captions,
)
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class RecordingCaptioner:
    """A stand-in vision model: no network, and it counts its own calls."""

    model_name = "test/vision-1"

    def __init__(self, caption: str | None = "A scatter plot of horsepower.") -> None:
        self.caption = caption
        self.calls = 0

    def describe(self, payload: bytes, mime_type: str) -> str | None:
        del payload, mime_type
        self.calls += 1
        return self.caption


class FailingCaptioner:
    model_name = "test/vision-1"

    def describe(self, payload: bytes, mime_type: str) -> str | None:
        raise RuntimeError("provider unavailable")


class CaptionTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
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
                "select block_id from image_blocks where book_id = %s",
                (self.book_id,),
            ).fetchone()["block_id"]

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def set_image(self, payload: bytes, *, block_id: int | None = None) -> None:
        with database_connection(self.database_url) as connection:
            connection.execute(
                "update image_blocks set base64_content = %s where block_id = %s",
                (b64encode(payload).decode(), block_id or self.block_id),
            )

    def run_captioner(self, captioner, **kwargs):
        with database_connection(self.database_url) as connection:
            return caption_book_figures(
                connection,
                self.book_id,
                owner_id=self.owner_id,
                captioner=captioner,
                **kwargs,
            )

    def captions(self) -> dict[int, str]:
        with database_connection(self.database_url, readonly=True) as connection:
            return load_captions(
                connection, [self.block_id], owner_id=self.owner_id
            )

    def test_a_substantive_figure_is_captioned(self):
        self.set_image(b"x" * (MINIMUM_FIGURE_BYTES + 1))
        captioner = RecordingCaptioner()

        summary = self.run_captioner(captioner)

        self.assertEqual(summary.captioned, 1)
        self.assertEqual(captioner.calls, 1)
        self.assertEqual(
            self.captions()[self.block_id],
            "A scatter plot of horsepower.",
        )

    def test_a_tiny_image_is_skipped_without_calling_the_model(self):
        """A rendered heading or rule is not worth a vision call."""

        self.set_image(b"x" * (MINIMUM_FIGURE_BYTES - 1))
        captioner = RecordingCaptioner()

        summary = self.run_captioner(captioner)

        self.assertEqual(summary.skipped_small, 1)
        self.assertEqual(captioner.calls, 0)
        self.assertEqual(self.captions(), {})

    def test_a_model_that_calls_it_decorative_is_recorded_as_such(self):
        self.set_image(b"x" * (MINIMUM_FIGURE_BYTES + 1))

        summary = self.run_captioner(RecordingCaptioner(caption=None))

        self.assertEqual(summary.skipped_boilerplate, 1)
        with database_connection(self.database_url, readonly=True) as connection:
            row = connection.execute(
                "select caption, skipped_reason from image_captions where block_id = %s",
                (self.block_id,),
            ).fetchone()
        # "Not captioned" and "deliberately skipped" must be distinguishable.
        self.assertIsNone(row["caption"])
        self.assertEqual(row["skipped_reason"], "boilerplate")

    def test_repeated_bytes_are_treated_as_publisher_boilerplate(self):
        """The measured signal: the same badge recurs across pages and books."""

        payload = b"x" * (MINIMUM_FIGURE_BYTES + 1)
        encoded = b64encode(payload).decode()
        with database_connection(self.database_url) as connection:
            # Give the same bytes to enough blocks to cross the threshold.
            for index in range(BOILERPLATE_REPEAT_THRESHOLD):
                connection.execute(
                    """
                    insert into content_blocks (
                        owner_id, book_id, node_id, block_index, block_type,
                        category, page_number, text_content
                    )
                    select owner_id, book_id, node_id, 900 + %s, 'image',
                           'ImagePlaceholder', page_number, null
                    from content_blocks where id = %s
                    returning id
                    """,
                    (index, self.block_id),
                )
                new_id = connection.execute(
                    "select max(id) as id from content_blocks where book_id = %s",
                    (self.book_id,),
                ).fetchone()["id"]
                connection.execute(
                    """
                    insert into image_blocks (
                        block_id, owner_id, book_id, mime_type, base64_content
                    ) values (%s, %s, %s, 'image/png', %s)
                    """,
                    (new_id, self.owner_id, self.book_id, encoded),
                )
            connection.execute(
                "update image_blocks set base64_content = %s where block_id = %s",
                (encoded, self.block_id),
            )

        captioner = RecordingCaptioner()
        summary = self.run_captioner(captioner)

        self.assertEqual(captioner.calls, 0)
        self.assertGreaterEqual(summary.skipped_boilerplate, 1)

    def test_a_second_run_reuses_existing_work(self):
        """A retry after a partial failure must not pay for captions twice."""

        self.set_image(b"x" * (MINIMUM_FIGURE_BYTES + 1))
        first = RecordingCaptioner()
        self.run_captioner(first)

        second = RecordingCaptioner()
        summary = self.run_captioner(second)

        self.assertEqual(second.calls, 0)
        self.assertEqual(summary.reused, 1)

    def test_a_provider_failure_is_recorded_and_does_not_raise(self):
        """One bad figure must not sink an otherwise complete book."""

        self.set_image(b"x" * (MINIMUM_FIGURE_BYTES + 1))

        summary = self.run_captioner(FailingCaptioner())

        self.assertEqual(summary.failed, 1)
        self.assertEqual(self.captions(), {})

    def test_content_hash_tracks_the_bytes(self):
        self.assertEqual(content_hash("abc"), content_hash("abc"))
        self.assertNotEqual(content_hash("abc"), content_hash("abd"))


if __name__ == "__main__":
    unittest.main()


class SentinelDetectionTests(unittest.TestCase):
    """The model announces a decorative image in its own words first."""

    def responder(self, content: str):
        import httpx

        from ingestion.captions import OpenRouterCaptioner

        captioner = OpenRouterCaptioner.__new__(OpenRouterCaptioner)
        captioner.model_name = "test/vision-1"
        captioner._max_attempts = 1
        captioner._client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": content}}]},
                )
            )
        )
        return captioner

    def test_a_trailing_sentinel_is_detected(self):
        # The exact production failure: a heading image was captioned and
        # shown because the sentinel came last.
        captioner = self.responder(
            "This text constitutes a heading, not a substantive figure "
            "for citation. NOT_A_FIGURE"
        )
        self.assertIsNone(captioner.describe(b"bytes", "image/png"))

    def test_a_leading_sentinel_is_detected(self):
        captioner = self.responder("NOT_A_FIGURE")
        self.assertIsNone(captioner.describe(b"bytes", "image/png"))

    def test_an_ordinary_caption_survives(self):
        captioner = self.responder("A scatter plot of sales against TV spend.")
        self.assertEqual(
            captioner.describe(b"bytes", "image/png"),
            "A scatter plot of sales against TV spend.",
        )

    def test_an_empty_response_is_treated_as_no_caption(self):
        self.assertIsNone(self.responder("   ").describe(b"bytes", "image/png"))


class CaptionCheckpointTests(PostgresOwnerMixin, unittest.TestCase):
    """Captioning commits as it goes, for the reason the OCR stage does.

    A whole pass held in one transaction is a pass a dropped connection
    discards entirely. This book's 288 figures were captioned and lost twice in
    one evening to a network fault, each time costing 288 vision calls to redo,
    while the transcription stage beside it lost nothing to the same fault.
    """

    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.addCleanup(self.tearDownPostgresOwner)
        with database_connection(self.database_url) as connection:
            self.book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Captioned",
                author=None,
                file_hash=FILE_HASH,
                page_count=6,
                parser_version="test",
            )

    def test_work_survives_a_failure_partway_through(self) -> None:
        class FailsPartway:
            model_name = "test/vision-1"

            def __init__(self) -> None:
                self.calls = 0

            def describe(self, payload: bytes, mime_type: str) -> str | None:
                del payload, mime_type
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("the connection dropped")
                return "A scatter plot of horsepower against weight."

        with database_connection(self.database_url) as connection:
            caption_book_figures(
                connection,
                self.book_id,
                owner_id=self.owner_id,
                captioner=FailsPartway(),
            )

        # A fresh connection sees the caption written before the failure, which
        # is the whole point: the next attempt reuses it rather than paying again.
        with database_connection(self.database_url) as connection:
            stored = connection.execute(
                "select count(*) as n from image_captions where book_id = %s",
                (self.book_id,),
            ).fetchone()
        self.assertGreater(stored["n"], 0)
