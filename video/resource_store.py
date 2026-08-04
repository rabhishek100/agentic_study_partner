"""Persist linked-document pages under the claimed resources stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from video.resources import ResourcePage
from video.states import Stage


class VideoResourceStoreConflictError(RuntimeError):
    """The resources stage is no longer owned by this worker attempt."""


@dataclass(frozen=True)
class PendingResource:
    id: UUID
    title: str
    origin: str
    status: str
    source_url: str | None
    storage_backend: str | None
    storage_key: str | None
    content_hash: str | None
    page_count: int | None
    role: str
    required: bool

    @property
    def is_stored(self) -> bool:
        return bool(self.storage_backend and self.storage_key)


def load_linked_pdfs(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
) -> tuple[PendingResource, ...]:
    """Return every PDF confirmed for this video, ready or not."""

    scope = _locked_scope(
        connection,
        job_id=job_id,
        worker_id=worker_id,
        attempt_count=attempt_count,
    )
    rows = connection.execute(
        """
        select r.id, r.title, r.origin, r.status, r.source_url,
               r.storage_backend, r.storage_key, r.content_hash, r.page_count,
               link.role, link.required
        from video.video_resources as link
        join video.resources as r
          on r.id = link.resource_id and r.owner_id = link.owner_id
        where link.owner_id = %s and link.video_id = %s
          and r.resource_kind = 'pdf'
        order by link.attached_at, r.id
        """,
        (scope["owner_id"], scope["video_id"]),
    ).fetchall()
    return tuple(
        PendingResource(
            id=row["id"],
            title=row["title"],
            origin=row["origin"],
            status=row["status"],
            source_url=row["source_url"],
            storage_backend=row["storage_backend"],
            storage_key=row["storage_key"],
            content_hash=row["content_hash"],
            page_count=row["page_count"],
            role=row["role"],
            required=row["required"],
        )
        for row in rows
    )


def stored_page_count(
    connection: Connection,
    *,
    owner_id: str | UUID,
    resource_id: str | UUID,
    parser_config_hash: str,
) -> int:
    """Count pages already parsed by exactly this parsing behavior."""

    return int(
        connection.execute(
            """
            select count(*) as count from video.resource_pages
            where owner_id = %s and resource_id = %s and parser_config_hash = %s
            """,
            (UUID(str(owner_id)), UUID(str(resource_id)), parser_config_hash),
        ).fetchone()["count"]
    )


def persist_resource_pages(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    resource_id: str | UUID,
    storage_backend: str,
    storage_key: str,
    content_hash: str,
    size_bytes: int,
    page_count: int,
    pages: Sequence[ResourcePage],
    parser_version: str,
    parser_config_hash: str,
    provenance: dict[str, Any] | None = None,
) -> int:
    """Replace a document's pages and publish it as ready in one transaction."""

    if not pages:
        raise ValueError("a ready PDF resource must have at least one page")
    resource = UUID(str(resource_id))
    with connection.transaction():
        scope = _locked_scope(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        _owned_resource(connection, scope=scope, resource_id=resource)
        connection.execute(
            "delete from video.resource_pages where owner_id = %s and resource_id = %s",
            (scope["owner_id"], resource),
        )
        for page in pages:
            connection.execute(
                """
                insert into video.resource_pages (
                    owner_id, resource_id, page_number, text_content,
                    layout_json, content_hash, parser_version,
                    parser_config_hash
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scope["owner_id"],
                    resource,
                    page.page_number,
                    page.text,
                    Jsonb(page.layout),
                    page.content_hash,
                    parser_version,
                    parser_config_hash,
                ),
            )
        connection.execute(
            """
            update video.resources
            set status = 'ready', storage_backend = %s, storage_key = %s,
                content_hash = %s, size_bytes = %s, media_type = 'application/pdf',
                page_count = %s, provenance_json = %s, updated_at = now()
            where id = %s and owner_id = %s
            """,
            (
                storage_backend,
                storage_key,
                content_hash,
                size_bytes,
                page_count,
                Jsonb(provenance or {}),
                resource,
                scope["owner_id"],
            ),
        )
    return len(pages)


def mark_resource_failed(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
    resource_id: str | UUID,
    reason: str,
) -> None:
    """Record a document failure without dropping the association.

    A failed optional PDF must remain visible as failed rather than silently
    vanishing from the resource panel, so the row keeps its link and title.
    """

    resource = UUID(str(resource_id))
    with connection.transaction():
        scope = _locked_scope(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
        )
        _owned_resource(connection, scope=scope, resource_id=resource)
        connection.execute(
            """
            update video.resources
            set status = 'failed',
                provenance_json = provenance_json || %s,
                updated_at = now()
            where id = %s and owner_id = %s
            """,
            (Jsonb({"failure_reason": reason[:500]}), resource, scope["owner_id"]),
        )


def _owned_resource(
    connection: Connection, *, scope: dict[str, Any], resource_id: UUID
) -> None:
    row = connection.execute(
        """
        select 1 from video.video_resources
        where owner_id = %s and video_id = %s and resource_id = %s
        """,
        (scope["owner_id"], scope["video_id"], resource_id),
    ).fetchone()
    if row is None:
        raise VideoResourceStoreConflictError(
            "resource is not linked to this video"
        )


def _locked_scope(
    connection: Connection,
    *,
    job_id: str | UUID,
    worker_id: str,
    attempt_count: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select j.owner_id, j.video_id, j.target_version_id as version_id
        from video.ingestion_jobs as j
        where j.id = %s and j.status = 'running' and j.stage = %s
          and j.lease_owner = %s and j.attempt_count = %s
          and j.lease_expires_at >= now()
        for update
        """,
        (UUID(str(job_id)), str(Stage.RESOURCES), worker_id, attempt_count),
    ).fetchone()
    if row is None:
        raise VideoResourceStoreConflictError(
            "video resources stage is unavailable to this worker attempt"
        )
    return row
