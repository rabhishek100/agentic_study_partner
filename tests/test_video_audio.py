"""Focused contracts for the OpenRouter audio-transcription fallback."""

from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import httpx

from video.audio import (
    AudioBudgetExceeded,
    AudioTranscriptionError,
    DEFAULT_TRANSCRIPTION_MODEL,
    MAX_ATTEMPTS,
    OpenRouterAudioClient,
)


def _response(
    *,
    generation_id: str,
    cost: float,
    segments: list[dict] | None = None,
) -> dict:
    return {
        "id": generation_id,
        "model": "openai/whisper-1-2026-07",
        "language": "en",
        "text": "transcript",
        "segments": segments
        or [{"start": 0.25, "end": 1.5, "text": "  Gradient flow  "}],
        "usage": {"cost": cost, "duration": 600},
    }


class ChunkRunner:
    def __init__(self, count: int) -> None:
        self.count = count
        self.calls: list[tuple[tuple[str, ...], int]] = []

    def __call__(self, argv, *, timeout_seconds: int):
        values = tuple(argv)
        self.calls.append((values, timeout_seconds))
        pattern = values[-1]
        for index in range(self.count):
            Path(pattern.replace("%05d", f"{index:05d}")).write_bytes(
                f"mp3-{index}".encode()
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class AudioClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.media = self.root / "source.mp4"
        self.media.write_bytes(b"canonical-video")
        self.work = self.root / "work"

    def test_splits_posts_offsets_and_exposes_provider_provenance(self) -> None:
        runner = ChunkRunner(2)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            index = len(requests) - 1
            return httpx.Response(
                200,
                json=_response(
                    generation_id=f"gen-{index}",
                    cost=0.02 if index == 0 else 0.01,
                    segments=[
                        {
                            "start": 0.25,
                            "end": 1.5,
                            "text": f" chunk {index} text ",
                        }
                    ],
                ),
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            result = OpenRouterAudioClient(
                client=http_client, command_runner=runner
            ).transcribe(
                self.media,
                duration_ms=15 * 60 * 1_000,
                work_dir=self.work,
                remaining_budget_usd="0.20",
            )

        command = runner.calls[0][0]
        self.assertIn("-ac", command)
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-b:a") + 1], "64k")
        self.assertEqual(command[command.index("-segment_time") + 1], "600")
        self.assertEqual(len(requests), 2)
        multipart = requests[0].content.decode("utf-8")
        self.assertIn('name="model"', multipart)
        self.assertIn(DEFAULT_TRANSCRIPTION_MODEL, multipart)
        self.assertIn("verbose_json", multipart)
        self.assertIn('name="timestamp_granularities[]"', multipart)
        self.assertIn("segment", multipart)
        self.assertIn('filename="chunk-00000.mp3"', multipart)

        self.assertEqual([cue.cue_index for cue in result.cues], [0, 1])
        self.assertEqual(result.cues[0].start_ms, 250)
        self.assertEqual(result.cues[1].start_ms, 600_250)
        self.assertEqual(result.cues[1].text, "chunk 1 text")
        self.assertEqual(result.provenance.provider, "openrouter")
        self.assertEqual(result.provenance.generation_ids, ("gen-0", "gen-1"))
        self.assertAlmostEqual(result.provenance.total_estimated_cost_usd, 0.09)
        self.assertAlmostEqual(result.provenance.total_cost_usd, 0.03)
        self.assertEqual(result.provenance.chunks[0].usage["duration"], 600)
        self.assertRegex(
            result.provenance.chunks[0].input_hash, r"^[0-9a-f]{64}$"
        )

    def test_budget_estimate_blocks_before_the_first_provider_call(self) -> None:
        requests = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(500)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            )
            with self.assertRaises(AudioBudgetExceeded) as raised:
                client.transcribe(
                    self.media,
                    duration_ms=10 * 60 * 1_000,
                    work_dir=self.work,
                    remaining_budget_usd="0.059",
                )

        self.assertEqual(requests, 0)
        self.assertEqual(raised.exception.incurred_cost_usd, Decimal("0"))
        self.assertEqual(raised.exception.required_cost_usd, Decimal("0.060"))

    def test_custom_model_requires_an_explicit_price_guard(self) -> None:
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ) as http_client:
            with patch.dict(
                os.environ,
                {"OPENROUTER_AUDIO_COST_PER_MINUTE_USD": ""},
            ):
                with self.assertRaisesRegex(ValueError, "COST_PER_MINUTE"):
                    OpenRouterAudioClient(
                        model="vendor/custom-stt",
                        client=http_client,
                        command_runner=ChunkRunner(1),
                    )

    def test_complete_estimate_blocks_before_partial_transcription(self) -> None:
        requests = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(
                200, json=_response(generation_id="gen-paid", cost=0.05)
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(2)
            )
            with self.assertRaises(AudioBudgetExceeded) as raised:
                client.transcribe(
                    self.media,
                    duration_ms=20 * 60 * 1_000,
                    work_dir=self.work,
                    remaining_budget_usd="0.10",
                )

        self.assertEqual(requests, 0)
        self.assertEqual(raised.exception.incurred_cost_usd, Decimal("0"))
        self.assertEqual(raised.exception.required_cost_usd, Decimal("0.120"))
        self.assertEqual(raised.exception.generation_ids, ())

    def test_actual_response_cost_cannot_cross_the_cap(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_response(generation_id="gen-over", cost=0.08)
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            )
            with self.assertRaises(AudioBudgetExceeded) as raised:
                client.transcribe(
                    self.media,
                    duration_ms=10 * 60 * 1_000,
                    work_dir=self.work,
                    remaining_budget_usd="0.07",
                )

        self.assertEqual(raised.exception.incurred_cost_usd, Decimal("0.08"))
        self.assertEqual(raised.exception.generation_ids, ("gen-over",))

    def test_network_failure_is_retried_once(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary", request=request)
            return httpx.Response(
                200, json=_response(generation_id="gen-retry", cost=0.01)
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            result = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            ).transcribe(
                self.media,
                duration_ms=60_000,
                work_dir=self.work,
                remaining_budget_usd="0.02",
            )

        self.assertEqual(attempts, MAX_ATTEMPTS)
        self.assertEqual(result.provenance.chunks[0].attempt, 2)

    def test_retryable_provider_error_is_bounded_and_safe(self) -> None:
        attempts = 0
        secret = "signed-provider-secret"

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, text=secret)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            )
            with self.assertRaises(AudioTranscriptionError) as raised:
                client.transcribe(
                    self.media,
                    duration_ms=60_000,
                    work_dir=self.work,
                    remaining_budget_usd="0.02",
                )

        self.assertEqual(attempts, MAX_ATTEMPTS)
        self.assertNotIn(secret, str(raised.exception))

    def test_non_retryable_rejection_is_not_retried(self) -> None:
        attempts = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(400, json={"error": "private detail"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            )
            with self.assertRaisesRegex(AudioTranscriptionError, "rejected"):
                client.transcribe(
                    self.media,
                    duration_ms=60_000,
                    work_dir=self.work,
                    remaining_budget_usd="0.02",
                )

        self.assertEqual(attempts, 1)

    def test_official_generation_header_is_preserved(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            body = _response(generation_id="temporary-body-id", cost=0.01)
            del body["id"]
            return httpx.Response(
                200,
                headers={"X-Generation-Id": "gen-from-header"},
                json=body,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            result = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            ).transcribe(
                self.media,
                duration_ms=60_000,
                work_dir=self.work,
                remaining_budget_usd="0.02",
            )

        self.assertEqual(result.provenance.generation_ids, ("gen-from-header",))

    def test_invalid_timestamped_segments_are_rejected_without_retry(self) -> None:
        attempts = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                json=_response(
                    generation_id="gen-invalid",
                    cost=0.01,
                    segments=[
                        {"start": 2.0, "end": 3.0, "text": "later"},
                        {"start": 1.0, "end": 1.5, "text": "earlier"},
                    ],
                ),
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterAudioClient(
                client=http_client, command_runner=ChunkRunner(1)
            )
            with self.assertRaises(AudioTranscriptionError):
                client.transcribe(
                    self.media,
                    duration_ms=60_000,
                    work_dir=self.work,
                    remaining_budget_usd="0.02",
                )

        self.assertEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
