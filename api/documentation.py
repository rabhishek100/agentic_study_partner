"""Shared OpenAPI metadata. These declarations do not change response handling."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI
from pydantic import BaseModel

from revision_sheets.ask import Answer


API_DESCRIPTION = """
Grounded study over books, papers, lectures, and courses, with durable ingestion,
flashcards, revision sheets, interview practice, and read-aloud.

**Authentication:** sign in through Supabase Auth, then click **Authorize** and
paste the session's access JWT without the `Bearer` prefix. Swagger adds the
`Authorization: Bearer <JWT>` header. There is no login/token endpoint in this
API. A user's verified token subject determines ownership.

**Jobs:** an accepted ingestion/generation request is not a finished artifact.
Poll its job endpoint; a separate Python worker performs the queued work.
Creation endpoints that require `Idempotency-Key` document it as a header.

**Streams and files:** chat streams use `text/event-stream`; PDF, image, audio,
and video endpoints return bytes. Use a streaming HTTP client for incremental
events. Swagger's Execute action performs real requests, including mutations
and configured model calls.
"""

TAGS = [
    {"name": name, "description": description}
    for name, description in (
        ("health", "Service readiness, build identity, and queue health."),
        ("books", "Ready books and papers, chapter hierarchy, source passages, figures, and signed PDF links."),
        ("ingestion", "PDF upload reservations, outline review, durable progress, cancellation, and retries."),
        ("study", "Grounded book/paper chat and saved conversations."),
        ("reading", "Reading positions, anchors, and focused side conversations."),
        ("prompts", "Owner-specific answer-style settings and prompt previews."),
        ("transcription", "Ephemeral dictated questions returned as editable text."),
        ("videos", "Lecture sources, ingestion reservations, captions, and supporting resources."),
        ("video-ingestion", "Lecture upload bytes and durable ingestion progress/events."),
        ("video-chat", "Lecture questions, watch sessions, side chats, timelines, and private media."),
        ("courses", "Ordered lecture collections and batch/playlist ingestion."),
        ("course-chat", "Questions and saved conversations across selected course lectures."),
        ("decks", "Cited cards, generation jobs, source automation preferences, and daily reviews."),
        ("notifications", "Daily reminder preferences and persistent in-app notification history."),
        ("revision sheets", "Complete-scope revision artifacts, generation jobs, PDFs, and grounded follow-ups."),
        ("interviews", "Adaptive interview sessions, answer evaluation, reports, and optional speech."),
        ("ideal-interviews", "Generated ideal exchanges and optional voice playback connections."),
        ("narration", "Figure descriptions, read-aloud audio, and optional voice connections."),
    )
]

AUTH_RESPONSES = {
    401: {
        "description": "Missing, expired, malformed, or untrusted Supabase access JWT.",
        "content": {"application/json": {
            "schema": {"type": "object", "properties": {"detail": {"type": "string"}}, "required": ["detail"]},
            "example": {"detail": "authentication required"},
        }},
        "headers": {"WWW-Authenticate": {"schema": {"type": "string"}, "example": "Bearer"}},
    }
}


def configure_openapi(app: FastAPI) -> None:
    """Document the common bearer failure wherever native security is declared."""
    generate = app.openapi

    def documented_openapi() -> dict:
        schema = generate()
        for methods in schema["paths"].values():
            for operation in methods.values():
                if isinstance(operation, dict) and operation.get("security"):
                    operation["responses"].setdefault("401", AUTH_RESPONSES[401])
        return schema

    app.openapi = documented_openapi


def binary_responses(*media_types: str, description: str = "Binary response") -> dict:
    return {
        200: {
            "description": description,
            "content": {
                media_type: {"schema": {"type": "string", "format": "binary"}}
                for media_type in media_types
            },
        }
    }


SSE_RESPONSES = {
    200: {
        "description": "Incremental token events followed by a final result or error event; heartbeat comments keep idle streams open.",
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "example": 'event: token\ndata: {"text":"Grounded explanation"}\n\n',
            }
        },
    }
}


def binary_body(*media_types: str) -> dict:
    """Document bytes consumed via Request.stream(), rather than multipart data."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                media_type: {"schema": {"type": "string", "format": "binary"}}
                for media_type in media_types
            },
        }
    }


# Additional-response models describe the existing dictionary wire format.
# They are OpenAPI metadata only: revision handlers retain their current
# serialization and the model-generated artifact schema remains unchanged.
class RevisionSummary(BaseModel):
    id: UUID
    book_id: int
    chapter_node_id: int | None
    scope_kind: Literal["chapter", "paper"]
    scope_key: str
    version: int
    source_title: str
    scope_title: str
    source_fingerprint: str
    config_key: str
    created_at: datetime


class RevisionJob(BaseModel):
    id: UUID
    book_id: int
    chapter_node_id: int | None
    scope_kind: Literal["chapter", "paper"]
    scope_key: str
    status: str
    stage: str
    source_title: str
    scope_title: str
    sheet_id: UUID | None
    error_code: str | None
    error_detail: str | None
    cancellation_requested: bool
    created_at: datetime
    updated_at: datetime


class RevisionJobResult(BaseModel):
    job: RevisionJob


class RevisionSheetResult(BaseModel):
    sheet: RevisionSummary


class RevisionList(BaseModel):
    sheets: list[RevisionSummary]
    jobs: list[RevisionJob]


class RevisionDetail(RevisionSummary):
    content: dict[str, Any]
    source_references: dict[str, Any]
    provenance: dict[str, Any]
    source_changed: bool
    settings_changed: bool
    diagram_layout: dict[str, Any]


class RevisionFollowup(Answer):
    source_references: dict[str, Any]
