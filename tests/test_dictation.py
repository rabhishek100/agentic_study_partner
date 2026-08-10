"""Dictated questions reach the composer as plain, bounded text."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from study.dictation import (
    DEFAULT_DICTATION_MODEL,
    MAX_ATTEMPTS,
    MAXIMUM_QUESTION_BYTES,
    DictationError,
    audio_extension,
    is_probable_silence_hallucination,
    transcribe_spoken_question,
)


class DictationClientTests(unittest.TestCase):
    def test_recognizes_only_complete_known_silence_hallucinations(self) -> None:
        for text in (
            "Thank you.",
            " THANK  YOU! ",
            "Thank you. Thank you.",
            "Thanks for watching.",
            "[Music]",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_probable_silence_hallucination(text))

        for text in (
            "Thank you. I would next estimate capacity.",
            "The user data belongs in a durable store.",
            "Music embeddings can support retrieval.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_probable_silence_hallucination(text))

    def test_posts_a_named_clip_and_returns_collapsed_text(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200, json={"text": "  How does\n gradient  clipping work? \n"}
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            text = transcribe_spoken_question(
                b"opus-bytes", media_type="audio/webm;codecs=opus", client=client
            )

        self.assertEqual(text, "How does gradient clipping work?")
        multipart = requests[0].content.decode("utf-8", "replace")
        # The extension is how Whisper learns the container, so a webm clip
        # must not be posted under a generic name.
        self.assertIn('filename="question.webm"', multipart)
        self.assertIn(DEFAULT_DICTATION_MODEL, multipart)
        self.assertIn('name="response_format"', multipart)
        self.assertIn("json", multipart)
        self.assertNotIn("verbose_json", multipart)

    def test_uses_the_audio_model_ingestion_already_configures(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"text": "hello"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(
                os.environ, {"OPENROUTER_AUDIO_MODEL": "openai/gpt-4o-transcribe"}
            ):
                transcribe_spoken_question(
                    b"opus-bytes", media_type="audio/webm", client=client
                )

        self.assertIn(
            "openai/gpt-4o-transcribe", requests[0].content.decode("utf-8", "replace")
        )

    def test_silence_is_an_empty_string_rather_than_an_error(self) -> None:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"text": "   "})
            )
        ) as client:
            self.assertEqual(
                transcribe_spoken_question(
                    b"opus-bytes", media_type="audio/webm", client=client
                ),
                "",
            )

    def test_rejects_input_the_provider_should_never_see(self) -> None:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"text": "unreachable"})
            )
        ) as client:
            for audio, media_type in (
                (b"clip", "video/mp4"),
                (b"clip", "text/plain"),
                (b"", "audio/webm"),
                (b"x" * (MAXIMUM_QUESTION_BYTES + 1), "audio/webm"),
            ):
                with self.subTest(media_type=media_type, size=len(audio)):
                    with self.assertRaises(ValueError):
                        transcribe_spoken_question(
                            audio, media_type=media_type, client=client
                        )

    def test_retries_a_transient_failure_once_then_reports_unavailable(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503, json={"error": "overloaded"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(DictationError):
                transcribe_spoken_question(
                    b"opus-bytes", media_type="audio/webm", client=client
                )

        self.assertEqual(len(attempts), MAX_ATTEMPTS)

    def test_a_rejected_request_is_not_retried(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(400, json={"error": "bad clip"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(DictationError):
                transcribe_spoken_question(
                    b"opus-bytes", media_type="audio/webm", client=client
                )

        self.assertEqual(len(attempts), 1)

    def test_an_unintelligible_response_is_an_error_not_an_empty_question(self) -> None:
        for body in ({"segments": []}, {"text": 12}, ["text"]):
            with self.subTest(body=body):
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request, body=body: httpx.Response(200, json=body)
                    )
                ) as client:
                    with self.assertRaises(DictationError):
                        transcribe_spoken_question(
                            b"opus-bytes", media_type="audio/webm", client=client
                        )

    def test_media_types_carry_their_container(self) -> None:
        self.assertEqual(audio_extension("audio/mp4"), "mp4")
        self.assertEqual(audio_extension("AUDIO/WEBM; codecs=opus"), "webm")
        self.assertIsNone(audio_extension("application/json"))


class DictationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: uuid4()

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def post(self, content: bytes, *, media_type: str = "audio/webm"):
        return await self.client.post(
            "/api/transcriptions",
            content=content,
            headers={"Content-Type": media_type},
        )

    async def test_returns_the_spoken_question(self) -> None:
        with patch(
            "api.main.transcribe_spoken_question", return_value="What is LoRA?"
        ) as transcriber:
            response = await self.post(b"opus-bytes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"text": "What is LoRA?"})
        self.assertEqual(transcriber.call_args.args[0], b"opus-bytes")
        self.assertEqual(transcriber.call_args.kwargs["media_type"], "audio/webm")

    async def test_rejects_anything_that_is_not_recorded_audio(self) -> None:
        response = await self.post(b"{}", media_type="application/json")
        self.assertEqual(response.status_code, 415)

    async def test_rejects_an_empty_recording(self) -> None:
        response = await self.post(b"")
        self.assertEqual(response.status_code, 422)

    async def test_rejects_a_recording_past_the_ceiling(self) -> None:
        response = await self.post(b"x" * (MAXIMUM_QUESTION_BYTES + 1))
        self.assertEqual(response.status_code, 413)

    async def test_silence_asks_the_reader_to_try_again(self) -> None:
        with patch("api.main.transcribe_spoken_question", return_value=""):
            response = await self.post(b"opus-bytes")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "no speech was recorded")

    async def test_a_provider_failure_points_at_the_keyboard(self) -> None:
        with patch(
            "api.main.transcribe_spoken_question",
            side_effect=DictationError("provider is down"),
        ):
            response = await self.post(b"opus-bytes")

        self.assertEqual(response.status_code, 502)
        # The provider's words never reach the client, only the way out.
        self.assertNotIn("provider is down", response.text)
        self.assertIn("type the question", response.json()["detail"])

    async def test_dictation_requires_a_signed_in_reader(self) -> None:
        app.dependency_overrides.clear()
        response = await self.post(b"opus-bytes")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
