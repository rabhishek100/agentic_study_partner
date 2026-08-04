"""Contract tests for production OpenRouter lecture-frame analysis."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx

from video.vision import (
    strict_schema,
    DEFAULT_VISUAL_MODEL,
    MAX_ATTEMPTS,
    PROMPT_VERSION,
    VISUAL_ANALYSIS_SCHEMA,
    OpenRouterVisualClient,
    VisualAnalysisError,
    VisualFrame,
)


def _frame(index: int, timestamp_ms: int, image: bytes = b"png") -> VisualFrame:
    return VisualFrame(
        frame_index=index,
        timestamp_ms=timestamp_ms,
        image=image,
        mime_type="image/png",
        ocr_text="Gradient flows backward through the computation graph",
    )


def _details() -> dict[str, list[str]]:
    return {
        "concepts": ["backpropagation"],
        "relationships": ["loss sends gradients toward parameters"],
        "equations": [],
        "code_or_commands": [],
        "chart_or_ui_details": [],
    }


def _valid_result(*, first_index: int = 11, second_index: int | None = 12) -> dict:
    frames = [
        {
            "frame_index": first_index,
            "visual_types": ["slide", "diagram"],
            "importance": 0.92,
            "confidence": 0.96,
            "summary": "A computation graph shows backward gradient flow.",
            "visible_text": "loss, gradient, weights",
            "technical_details": _details(),
            "transition_after": (
                {
                    "event_type": "annotation",
                    "summary": "Gradient arrows are added to the graph.",
                    "technical_changes": ["backward arrows become visible"],
                }
                if second_index is not None
                else None
            ),
            "regions": [
                {
                    "region_type": "diagram",
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.7,
                    "height": 0.6,
                    "summary": "Gradient-flow computation graph",
                    "confidence": 0.94,
                }
            ],
        }
    ]
    if second_index is not None:
        frames.append(
            {
                "frame_index": second_index,
                "visual_types": ["slide"],
                "importance": 0.8,
                "confidence": 0.9,
                "summary": "The completed slide explains gradient flow.",
                "visible_text": "backpropagation",
                "technical_details": _details(),
                "transition_after": None,
                "regions": [],
            }
        )
    return {
        "sequence_summary": "A slide builds a backpropagation explanation.",
        "frames": frames,
    }


def _provider_body(result: dict, *, model: str = "openai/gpt-5.6-luna-202608") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": json.dumps(result)}}],
        "usage": {
            "prompt_tokens": 410,
            "completion_tokens": 145,
            "cost": 0.0017,
        },
    }


class OpenRouterVisualClientTests(unittest.TestCase):
    def test_sends_strict_multimodal_request_and_returns_db_shaped_result(
        self,
    ) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_provider_body(_valid_result()))

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as http_client:
            client = OpenRouterVisualClient(client=http_client)
            result = client.analyze([_frame(11, 1_000), _frame(12, 2_000)])

        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["model"], DEFAULT_VISUAL_MODEL)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["reasoning"], {"effort": "low", "exclude": True})
        self.assertEqual(payload["usage"], {"include": True})
        structured = payload["response_format"]["json_schema"]
        self.assertTrue(structured["strict"])
        # Sent sanitized: the provider rejects "uniqueItems" outright.
        self.assertEqual(structured["schema"], strict_schema(VISUAL_ANALYSIS_SCHEMA))
        self.assertNotIn("uniqueItems", json.dumps(structured["schema"]))
        content = payload["messages"][0]["content"]
        self.assertIn("Frame 11", content[0]["text"])
        self.assertEqual(len(content), 3)
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )

        first = result.frames[0]
        self.assertEqual(first.visual_types, ("slide", "diagram"))
        self.assertEqual(first.technical_details_json["concepts"], ["backpropagation"])
        self.assertEqual(first.regions[0].region_type, "diagram")
        self.assertEqual(first.transition_after.event_type, "annotation")
        self.assertEqual(
            first.transition_after.details_json(),
            {"technical_changes": ["backward arrows become visible"]},
        )
        self.assertIsNone(result.frames[1].transition_after)
        self.assertEqual(result.provenance.provider, "openrouter")
        self.assertEqual(result.provenance.requested_model, DEFAULT_VISUAL_MODEL)
        self.assertEqual(result.provenance.model, "openai/gpt-5.6-luna-202608")
        self.assertEqual(result.provenance.input_tokens, 410)
        self.assertEqual(result.provenance.output_tokens, 145)
        self.assertAlmostEqual(result.provenance.cost_usd, 0.0017)
        self.assertRegex(result.provenance.input_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(result.provenance.prompt_version, PROMPT_VERSION)
        self.assertEqual(result.provenance.attempt, 1)

    def test_accepts_a_single_frame_and_environment_model_override(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_provider_body(_valid_result(second_index=None))
            )

        with patch.dict(
            os.environ, {"OPENROUTER_VIDEO_VISION_MODEL": "test/cheap-vision"}
        ):
            with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
                client = OpenRouterVisualClient(client=http_client)
                result = client.analyze([_frame(11, 1_000)])

        self.assertEqual(result.provenance.requested_model, "test/cheap-vision")
        self.assertEqual(len(result.frames), 1)
        self.assertIsNone(result.frames[0].transition_after)

    def test_retries_invalid_structured_output_once(self) -> None:
        attempts = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "not json"}}],
                        "usage": {},
                    },
                )
            return httpx.Response(
                200, json=_provider_body(_valid_result(second_index=None))
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            result = OpenRouterVisualClient(client=http_client).analyze(
                [_frame(11, 1_000)]
            )

        self.assertEqual(attempts, MAX_ATTEMPTS)
        self.assertEqual(result.provenance.attempt, 2)

    def test_final_error_does_not_expose_provider_body(self) -> None:
        attempts = 0
        secret = "signed-token-that-must-not-escape"

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, text=secret)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterVisualClient(client=http_client)
            with self.assertRaises(VisualAnalysisError) as raised:
                client.analyze([_frame(11, 1_000)])

        self.assertEqual(attempts, MAX_ATTEMPTS)
        self.assertNotIn(secret, str(raised.exception))
        # The provider's reason survives; its secrets do not.
        self.assertTrue(
            str(raised.exception).startswith(
                f"visual analysis failed after {MAX_ATTEMPTS} attempts"
            )
        )

    def test_rejects_non_db_visual_types_and_regions_outside_the_frame(self) -> None:
        attempts = 0
        invalid = _valid_result(second_index=None)
        invalid["frames"][0]["visual_types"] = ["whiteboard"]
        invalid["frames"][0]["regions"][0]["x"] = 0.8

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json=_provider_body(invalid))

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            with self.assertRaises(VisualAnalysisError):
                OpenRouterVisualClient(client=http_client).analyze(
                    [_frame(11, 1_000)]
                )

        self.assertEqual(attempts, MAX_ATTEMPTS)

    def test_input_hash_is_stable_and_changes_with_pixels(self) -> None:
        hashes: list[str] = []

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_provider_body(_valid_result(second_index=None))
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterVisualClient(client=http_client)
            hashes.append(client.analyze([_frame(11, 1_000, b"one")]).provenance.input_hash)
            hashes.append(client.analyze([_frame(11, 1_000, b"one")]).provenance.input_hash)
            hashes.append(client.analyze([_frame(11, 1_000, b"two")]).provenance.input_hash)

        self.assertEqual(hashes[0], hashes[1])
        self.assertNotEqual(hashes[0], hashes[2])

    def test_rejects_invalid_frame_batches_before_any_request(self) -> None:
        requests = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(500)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenRouterVisualClient(client=http_client)
            with self.assertRaisesRegex(ValueError, "one or two"):
                client.analyze([])
            with self.assertRaisesRegex(ValueError, "PNG, JPEG, or WebP"):
                client.analyze(
                    [
                        VisualFrame(
                            frame_index=0,
                            timestamp_ms=0,
                            image=b"gif",
                            mime_type="image/gif",
                        )
                    ]
                )
            with self.assertRaisesRegex(ValueError, "chronological"):
                client.analyze([_frame(0, 2_000), _frame(1, 1_000)])

        self.assertEqual(requests, 0)

    def test_api_key_is_required_only_when_constructing_the_network_client(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                OpenRouterVisualClient()


if __name__ == "__main__":
    unittest.main()


class VisualSchemaTests(unittest.TestCase):
    """The request schema must be one the provider will actually accept."""

    def test_validation_keywords_are_stripped_from_the_request_schema(self) -> None:
        from video.vision import VISUAL_ANALYSIS_SCHEMA, strict_schema

        def keywords(node) -> set[str]:
            found: set[str] = set()
            if isinstance(node, dict):
                found.update(node)
                for value in node.values():
                    found |= keywords(value)
            elif isinstance(node, list):
                for value in node:
                    found |= keywords(value)
            return found

        # The hand-written schema carries uniqueItems, which strict structured
        # output rejects outright: every call 400'd in production because of it.
        self.assertIn("uniqueItems", keywords(VISUAL_ANALYSIS_SCHEMA))
        sanitized = keywords(strict_schema(VISUAL_ANALYSIS_SCHEMA))
        for keyword in ("uniqueItems", "minItems", "maxItems", "maxLength"):
            self.assertNotIn(keyword, sanitized)
        # Shape survives; only validation is dropped.
        for keyword in ("type", "properties", "required", "enum", "items"):
            self.assertIn(keyword, sanitized)

    def test_a_rejected_request_reports_the_provider_reason(self) -> None:
        import httpx

        from video.vision import _provider_failure

        error = httpx.HTTPStatusError(
            "400",
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/x"),
            response=httpx.Response(
                400,
                text='{"error":{"message":"Invalid schema for response_format: '
                "'uniqueItems' is not permitted.\"}}",
            ),
        )
        detail = _provider_failure(error)
        self.assertIn("400", detail)
        self.assertIn("uniqueItems", detail)
