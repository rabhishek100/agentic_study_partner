"""Reading answers aloud: voices, spoken figure descriptions, and the audio cache."""

from base64 import b64encode
from hashlib import sha256
import unittest
from unittest.mock import patch

import httpx

from interviews.speech import synthesize_interviewer_speech
from narration import cache
from narration.figures import (
    FigureRequest,
    MAXIMUM_DESCRIPTION_CHARACTERS,
    spoken_descriptions,
    without_opener,
)
from narration.synthesis import (
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    SpeechError,
    configured_model,
    configured_voice,
    synthesize_speech,
)
from storage.book_images import configured_book_image_store, store_figure
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


def audio_transport(recorded: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200, content=b"mp3-bytes", headers={"content-type": "audio/mpeg"}
        )

    return httpx.MockTransport(handler)


class VoiceSelectionTests(unittest.TestCase):
    """The reading voice is configurable without disturbing the interviewer."""

    def test_reading_falls_back_to_the_shared_voice_settings(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(configured_model("reading"), DEFAULT_TTS_MODEL)
            self.assertEqual(configured_voice("reading"), DEFAULT_TTS_VOICE)

    def test_a_reading_voice_override_leaves_the_interviewer_alone(self) -> None:
        environment = {
            "OPENROUTER_TTS_MODEL": "vendor/interview-tts",
            "OPENROUTER_TTS_VOICE": "en_brisk",
            "OPENROUTER_READING_TTS_VOICE": "en_warm_narrator",
        }
        with patch.dict("os.environ", environment, clear=True):
            self.assertEqual(configured_voice("reading"), "en_warm_narrator")
            self.assertEqual(configured_voice("interview"), "en_brisk")
            # No reading model override: the shared setting still applies, so
            # only the voice changes and not which provider speaks.
            self.assertEqual(configured_model("reading"), "vendor/interview-tts")

    def test_the_reading_request_carries_the_reading_voice(self) -> None:
        recorded: list[httpx.Request] = []
        environment = {"OPENROUTER_READING_TTS_VOICE": "en_warm_narrator"}
        with httpx.Client(transport=audio_transport(recorded)) as client:
            with patch.dict("os.environ", environment, clear=True):
                result = synthesize_speech(
                    "Gradient descent takes small steps downhill.",
                    purpose="reading",
                    client=client,
                )

        self.assertIn("en_warm_narrator", recorded[0].read().decode())
        self.assertEqual(result.content, b"mp3-bytes")
        self.assertEqual(result.voice, "en_warm_narrator")

    def test_the_interviewer_still_speaks_after_the_refactor(self) -> None:
        recorded: list[httpx.Request] = []
        with httpx.Client(transport=audio_transport(recorded)) as client:
            with patch.dict("os.environ", {}, clear=True):
                result = synthesize_interviewer_speech(
                    "Why logistic regression?", client=client
                )

        body = recorded[0].read().decode()
        self.assertIn(DEFAULT_TTS_MODEL, body)
        self.assertIn(DEFAULT_TTS_VOICE, body)
        self.assertLess(result.cost_usd, 0.001)

    def test_a_json_body_behind_a_200_is_not_audio(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"error": "provider returned no audio"},
                headers={"content-type": "application/json"},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(SpeechError, "invalid audio"):
                synthesize_speech("Read this out.", purpose="reading", client=client)


class CacheKeyTests(unittest.TestCase):
    def test_the_same_words_in_a_different_voice_are_different_audio(self) -> None:
        words = "The residual plot shows no pattern."

        self.assertNotEqual(
            cache.cache_key(words, model="m", voice="en_paul"),
            cache.cache_key(words, model="m", voice="en_warm"),
        )
        self.assertNotEqual(
            cache.cache_key(words, model="m1", voice="en_paul"),
            cache.cache_key(words, model="m2", voice="en_paul"),
        )
        self.assertEqual(
            cache.cache_key(words, model="m", voice="en_paul"),
            cache.cache_key(words, model="m", voice="en_paul"),
        )

    def test_the_key_cannot_be_confused_by_running_fields_together(self) -> None:
        # Without a separator, ("ab", "c") and ("a", "bc") would hash alike.
        self.assertNotEqual(
            cache.cache_key("text", model="ab", voice="c"),
            cache.cache_key("text", model="a", voice="bc"),
        )


class OpenerTests(unittest.TestCase):
    """The script has already said "Figure, page 257" by the time this is heard."""

    def test_a_preamble_the_model_was_asked_not_to_write_is_removed(self) -> None:
        # Observed verbatim from gemini-2.5-flash-lite on a real corpus figure,
        # despite the instruction against it.
        spoken = without_opener(
            "This diagram shows a summary of key elements in a machine learning "
            "project, breaking down tasks from clarifying requirements to serving."
        )

        self.assertTrue(spoken.startswith("A summary of key elements"))

    def test_a_description_that_starts_with_content_is_left_alone(self) -> None:
        original = "Training loss falls steeply for ten epochs and then flattens."

        self.assertEqual(without_opener(original), original)

    def test_a_preamble_is_kept_when_removing_it_leaves_a_fragment(self) -> None:
        # Better a redundant lead-in than a description that is one word long.
        original = "This chart shows accuracy."

        self.assertEqual(without_opener(original), original)


class RecordingNarrator:
    """A stand-in vision model: no network, and it counts its own calls."""

    model_name = "test/vision-1"

    def __init__(self, description: str | None = "A curve that flattens after ten epochs.") -> None:
        self.description = description
        self.calls = 0

    def narrate(self, payload: bytes, mime_type: str) -> str | None:
        del payload, mime_type
        self.calls += 1
        return self.description


class FailingNarrator:
    model_name = "test/vision-1"

    def narrate(self, payload: bytes, mime_type: str) -> str | None:
        raise RuntimeError("provider unavailable")


class FigureNarrationTests(PostgresOwnerMixin, unittest.TestCase):
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
        self.set_image(b"figure-bytes" * 500)

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def set_image(self, payload: bytes) -> None:
        store = configured_book_image_store()
        key, content_hash, size = store_figure(
            store, owner_id=self.owner_id, payload=payload, mime_type="image/png"
        )
        encoded = b64encode(payload).decode()
        with database_connection(self.database_url) as connection:
            connection.execute(
                """
                update image_blocks
                set storage_backend = %s, storage_key = %s,
                    content_hash = %s, size_bytes = %s, base64_hash = %s
                where block_id = %s
                """,
                (store.backend, key, content_hash, size,
                 sha256(encoded.encode("ascii")).hexdigest(), self.block_id),
            )

    def set_caption(self, caption: str | None, skipped_reason: str | None = None) -> None:
        with database_connection(self.database_url) as connection:
            hash_row = connection.execute(
                "select base64_hash from image_blocks where block_id = %s",
                (self.block_id,),
            ).fetchone()
            connection.execute(
                """
                insert into image_captions
                    (block_id, owner_id, book_id, caption, content_hash,
                     model_name, skipped_reason)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (block_id) do update
                    set caption = excluded.caption,
                        skipped_reason = excluded.skipped_reason
                """,
                (self.block_id, self.owner_id, self.book_id, caption,
                 hash_row["base64_hash"], "test/vision-1", skipped_reason),
            )

    def narrate(self, narrator, *, block_id: int | None = None, book_id: int | None = None):
        with database_connection(self.database_url) as connection:
            return spoken_descriptions(
                connection,
                owner_id=self.owner_id,
                figures=[
                    FigureRequest(
                        book_id=book_id or self.book_id,
                        block_id=block_id or self.block_id,
                    )
                ],
                narrator=narrator,
            )

    def test_a_figure_is_described_once_and_then_reused(self) -> None:
        narrator = RecordingNarrator()

        first = self.narrate(narrator)
        second = self.narrate(narrator)

        self.assertEqual(first[self.block_id], "A curve that flattens after ten epochs.")
        self.assertEqual(second, first)
        # The second read came from the stored description, not the model.
        self.assertEqual(narrator.calls, 1)

    def test_a_description_is_stored_against_the_image_not_the_block(self) -> None:
        self.narrate(RecordingNarrator())

        with database_connection(self.database_url, readonly=True) as connection:
            row = connection.execute(
                """
                select narration_figures.description
                from narration_figures
                join image_blocks
                  on image_blocks.base64_hash = narration_figures.content_hash
                 and image_blocks.owner_id = narration_figures.owner_id
                where image_blocks.block_id = %s
                """,
                (self.block_id,),
            ).fetchone()

        self.assertEqual(row["description"], "A curve that flattens after ten epochs.")

    def test_a_figure_the_captioner_called_furniture_is_not_described(self) -> None:
        self.set_caption(None, skipped_reason="boilerplate")
        narrator = RecordingNarrator()

        described = self.narrate(narrator)

        self.assertEqual(described, {})
        self.assertEqual(narrator.calls, 0)

    def test_a_provider_failure_falls_back_to_the_caption(self) -> None:
        self.set_caption("Training loss against epoch for three learning rates.")

        described = self.narrate(FailingNarrator())

        self.assertEqual(
            described[self.block_id],
            "Training loss against epoch for three learning rates.",
        )

    def test_a_provider_failure_with_no_caption_says_nothing(self) -> None:
        described = self.narrate(FailingNarrator())

        self.assertEqual(described, {})

    def test_a_figure_asked_for_under_the_wrong_book_does_not_exist(self) -> None:
        narrator = RecordingNarrator()

        described = self.narrate(narrator, book_id=self.book_id + 10_000)

        self.assertEqual(described, {})
        self.assertEqual(narrator.calls, 0)

    def test_the_stored_description_has_its_preamble_stripped(self) -> None:
        class PreamblingNarrator:
            model_name = "test/vision-1"

            def narrate(self, payload: bytes, mime_type: str) -> str | None:
                del payload, mime_type
                return "This figure shows a curve that flattens after ten epochs."

        described = self.narrate(PreamblingNarrator())

        self.assertEqual(
            described[self.block_id], "A curve that flattens after ten epochs."
        )

    def test_an_overlong_description_is_truncated_before_storage(self) -> None:
        self.narrate(RecordingNarrator("word " * 1000))

        with database_connection(self.database_url, readonly=True) as connection:
            stored = connection.execute(
                "select description from narration_figures where owner_id = %s",
                (self.owner_id,),
            ).fetchone()["description"]

        self.assertLessEqual(len(stored), MAXIMUM_DESCRIPTION_CHARACTERS)


class AudioCacheTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()

    def tearDown(self) -> None:
        self.tearDownPostgresOwner()

    def store(self, key: str, audio: bytes) -> None:
        with database_connection(self.database_url) as connection:
            cache.store(
                connection,
                owner_id=self.owner_id,
                content_hash=key,
                audio=audio,
                media_type="audio/mpeg",
                model_name="test/tts",
                voice="en_test",
                character_count=len(audio),
                cost_usd=0.001,
            )

    def test_a_replay_comes_back_from_the_cache(self) -> None:
        key = cache.cache_key("Hear this again.", model="test/tts", voice="en_test")
        self.store(key, b"mp3-one")

        with database_connection(self.database_url) as connection:
            hit = cache.load(connection, owner_id=self.owner_id, content_hash=key)

        self.assertIsNotNone(hit)
        self.assertEqual(hit.content, b"mp3-one")
        self.assertEqual(hit.media_type, "audio/mpeg")

    def test_another_owner_hears_nothing_of_this_one(self) -> None:
        key = cache.cache_key("Private.", model="test/tts", voice="en_test")
        self.store(key, b"mp3-one")

        with database_connection(self.database_url) as connection:
            hit = cache.load(
                connection,
                owner_id="00000000-0000-0000-0000-000000000001",
                content_hash=key,
            )

        self.assertIsNone(hit)

    def test_eviction_drops_the_least_recently_heard_first(self) -> None:
        for index in range(3):
            self.store(f"{index:064x}", b"x" * 100)
            with database_connection(self.database_url) as connection:
                # Distinguish the three rows' last_used_at deterministically:
                # `now()` is the transaction time and all three would tie.
                connection.execute(
                    """
                    update narration_audio set last_used_at = now() - %s * interval '1 minute'
                    where owner_id = %s and content_hash = %s
                    """,
                    (10 - index, self.owner_id, f"{index:064x}"),
                )

        with database_connection(self.database_url) as connection:
            cache.evict(connection, owner_id=self.owner_id, budget=250)
            remaining = {
                row["content_hash"]
                for row in connection.execute(
                    "select content_hash from narration_audio where owner_id = %s",
                    (self.owner_id,),
                ).fetchall()
            }

        # Two rows of 100 bytes fit in 250; the oldest-heard one goes.
        self.assertEqual(remaining, {f"{1:064x}", f"{2:064x}"})


if __name__ == "__main__":
    unittest.main()
