"""OpenAPI coverage, published docs, and unchanged response transports."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse

from api.auth import current_owner
from api.main import app
from scripts.export_api_reference import render_reference


ROOT = Path(__file__).resolve().parents[1]
OWNER = UUID("11111111-1111-4111-8111-111111111111")
SSE_PATHS = (
    "/api/chat/stream",
    "/api/side-chats/{side_chat_id}/turns/stream",
    "/api/video-conversations/{conversation_id}/turns/stream",
    "/api/video-side-chats/{side_chat_id}/turns/stream",
    "/api/course-conversations/{conversation_id}/turns/stream",
)


class OpenApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = app.openapi()

    def test_every_operation_has_a_description_declared_group_and_unique_id(self):
        tags = {tag["name"] for tag in self.schema["tags"]}
        seen = set()
        for path, methods in self.schema["paths"].items():
            for method, operation in methods.items():
                with self.subTest(path=path, method=method):
                    self.assertTrue(operation.get("description"))
                    self.assertTrue(operation.get("tags"))
                    self.assertTrue(set(operation["tags"]) <= tags)
                    self.assertNotIn(operation["operationId"], seen)
                    seen.add(operation["operationId"])

    def test_bearer_operations_document_jwt_and_authentication_failure(self):
        scheme = self.schema["components"]["securitySchemes"]["HTTPBearer"]
        self.assertEqual(scheme["scheme"], "bearer")
        self.assertEqual(scheme["bearerFormat"], "JWT")
        for methods in self.schema["paths"].values():
            for operation in methods.values():
                if operation.get("security"):
                    self.assertIn("401", operation["responses"])
        for path in ("/api/health", "/api/ingestions/limits"):
            operation = self.schema["paths"][path]["get"]
            self.assertNotIn("security", operation)
            self.assertNotIn("401", operation["responses"])
        playback = self.schema["paths"]["/api/videos/{video_id}/stream"]["get"]
        self.assertNotIn("security", playback)
        self.assertIn("403", playback["responses"])
        self.assertIn("206", playback["responses"])

    def test_streams_and_files_are_not_advertised_as_json(self):
        for path in SSE_PATHS:
            content = self.schema["paths"][path]["post"]["responses"]["200"]["content"]
            self.assertEqual(set(content), {"text/event-stream"})
        for path, media_type in (
            ("/api/revision-sheets/{sheet_id}/pdf", "application/pdf"),
            ("/api/videos/{video_id}/resources/{resource_id}/content", "application/pdf"),
            ("/api/videos/{video_id}/frames/{frame_id}/image", "image/jpeg"),
            ("/api/books/{book_id}/blocks/{block_id}/image", "image/*"),
            ("/api/interviews/{session_id}/turns/{turn_index}/speech", "audio/*"),
        ):
            content = self.schema["paths"][path]["get"]["responses"]["200"]["content"]
            self.assertEqual(set(content), {media_type})
            self.assertEqual(content[media_type]["schema"]["format"], "binary")

    def test_raw_uploads_have_binary_request_bodies(self):
        for path, method, media_type in (
            ("/api/transcriptions", "post", "audio/webm"),
            ("/api/interviews/{session_id}/transcriptions", "post", "audio/webm"),
            ("/api/interviews/{session_id}/screen-checkpoints", "post", "image/png"),
            ("/api/video-ingestions/{job_id}/source", "put", "video/mp4"),
            ("/api/videos/{video_id}/resources/{resource_id}/content", "put", "application/pdf"),
            ("/api/videos/{video_id}/captions", "put", "text/vtt"),
        ):
            with self.subTest(path=path):
                body = self.schema["paths"][path][method]["requestBody"]
                self.assertTrue(body["required"])
                self.assertEqual(body["content"][media_type]["schema"]["format"], "binary")

    def test_revision_and_replay_responses_have_specific_schemas(self):
        create = self.schema["paths"]["/api/revision-sheets"]["post"]
        for code, model in (("200", "RevisionSheetResult"), ("202", "RevisionJobResult")):
            self.assertEqual(create["responses"][code]["content"]["application/json"]["schema"]["$ref"], f"#/components/schemas/{model}")
        for path in ("/api/ingestions", "/api/videos/uploads", "/api/courses"):
            responses = self.schema["paths"][path]["post"]["responses"]
            self.assertIn("200", responses)
            self.assertIn("201", responses)
            self.assertEqual(responses["200"]["content"]["application/json"]["schema"], responses["201"]["content"]["application/json"]["schema"])

    def test_all_success_payloads_and_schema_references_are_defined(self):
        def resolve(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    target = self.schema
                    for part in value["$ref"].removeprefix("#/").split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                for child in value.values():
                    resolve(child)
            elif isinstance(value, list):
                for child in value:
                    resolve(child)
        resolve(self.schema)
        for path, methods in self.schema["paths"].items():
            for method, operation in methods.items():
                for code, response in operation["responses"].items():
                    if code.startswith("2") and code != "204":
                        with self.subTest(path=path, method=method, code=code):
                            self.assertTrue(response.get("content"))
                            for media in response["content"].values():
                                self.assertTrue(media.get("schema"))

    def test_repository_catalog_matches_openapi_and_preserves_the_guide(self):
        document = (ROOT / "docs/api.md").read_text()
        self.assertEqual(render_reference(document, self.schema), document)
        edited = document.replace("GET | `/api/books`", "GET | `/api/outdated-books`", 1)
        self.assertNotEqual(edited, document)
        self.assertEqual(render_reference(edited, self.schema), document)
        with self.assertRaises(ValueError):
            render_reference("No generated markers", self.schema)


class DocumentationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.previous_overrides = dict(app.dependency_overrides)

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.previous_overrides)
        await self.client.aclose()

    async def test_docs_and_schema_are_served_without_database_or_provider_calls(self):
        with patch("api.main.database_connection", side_effect=AssertionError("Unexpected database call")):
            for path, marker in (("/docs", "SwaggerUIBundle"), ("/redoc", "redoc"), ("/openapi.json", "openapi")):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.text)
            protected = await self.client.get("/api/books")
            self.assertEqual(protected.status_code, 401)
            self.assertEqual(protected.headers["www-authenticate"], "Bearer")

    async def test_revision_metadata_preserves_json_and_dynamic_status(self):
        app.dependency_overrides[current_owner] = lambda: OWNER
        with patch("api.revision_sheets.run", new_callable=AsyncMock) as run:
            run.return_value = {"sheets": [], "jobs": [], "extra": "preserved"}
            response = await self.client.get("/api/revision-sheets")
            self.assertEqual(response.json(), run.return_value)
            self.assertEqual(response.headers["content-type"], "application/json")
            for payload, code in (({"job": {"id": str(OWNER)}}, 202), ({"sheet": {"id": str(OWNER)}}, 200)):
                run.return_value = payload
                response = await self.client.post("/api/revision-sheets", json={"scope_kind": "paper", "book_id": 1}, headers={"Idempotency-Key": "docs-test"})
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.json(), payload)

    async def test_file_stream_and_raw_audio_transports_are_preserved(self):
        app.dependency_overrides[current_owner] = lambda: OWNER
        with patch("api.revision_sheets.run", new_callable=AsyncMock, return_value=b"%PDF-test"):
            response = await self.client.get(f"/api/revision-sheets/{OWNER}/pdf")
            self.assertEqual(response.content, b"%PDF-test")
            self.assertEqual(response.headers["content-type"], "application/pdf")
        events = 'event: token\ndata: {"text":"Grounded"}\n\n'
        with patch("api.main._require_ready_books"), patch("api.main._streamed_turn", return_value=StreamingResponse(iter([events]), media_type="text/event-stream")):
            response = await self.client.post("/api/chat/stream", json={"question": "Explain", "book_ids": [1]})
            self.assertEqual(response.text, events)
            self.assertIn("text/event-stream", response.headers["content-type"])
        with patch("api.main.transcribe_spoken_question", return_value="Editable question") as transcribe:
            response = await self.client.post("/api/transcriptions", content=b"audio-bytes", headers={"Content-Type": "audio/webm"})
            self.assertEqual(response.json(), {"text": "Editable question"})
            transcribe.assert_called_once_with(b"audio-bytes", media_type="audio/webm")


if __name__ == "__main__":
    unittest.main()
