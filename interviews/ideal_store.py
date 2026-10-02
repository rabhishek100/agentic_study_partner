"""Owner-scoped persistence for generated ideal interview flows."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id

from .ideal_contracts import IdealInterviewExchange, IdealInterviewFlow


class IdealInterviewNotFoundError(LookupError):
    pass


COLUMNS = """
    id, book_id, node_id, scope_key, title, source_title, interview_format,
    target_level, topic_count, covered_topic_count, estimated_duration_seconds,
    transcript_json, generation_model, prompt_version, total_cost_usd,
    voice_cost_usd, created_at, updated_at
"""


def lock_generation(
    connection: Connection,
    *,
    owner_id: str | UUID,
    scope_key: str,
    interview_format: str,
    target_level: str,
    generation_model: str,
    prompt_version: str,
) -> None:
    """Serialize matching generations until the caller's transaction commits.

    The key matches the saved-flow uniqueness contract. After waiting, a retry
    reads the committed winner instead of making duplicate paid model calls.
    Other owners, scopes and generation settings have independent locks.
    """
    key = json.dumps([
        "ideal-interview", str(parse_owner_id(owner_id)), scope_key,
        interview_format, target_level, generation_model, prompt_version,
    ])
    connection.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))


def _flow(row: dict[str, Any]) -> IdealInterviewFlow:
    exchanges = [
        IdealInterviewExchange.model_validate(item)
        for item in row["transcript_json"] or []
    ]
    topic_count = int(row["topic_count"])
    covered = int(row["covered_topic_count"])
    return IdealInterviewFlow(
        flow_id=str(row["id"]),
        book_id=row["book_id"],
        node_id=row["node_id"],
        scope_key=row["scope_key"],
        title=row["title"],
        source_title=row["source_title"],
        interview_format=row["interview_format"],
        target_level=row["target_level"],
        topic_count=topic_count,
        covered_topic_count=covered,
        coverage_ratio=covered / topic_count,
        estimated_duration_seconds=row["estimated_duration_seconds"],
        exchanges=exchanges,
        generation_model=row["generation_model"],
        prompt_version=row["prompt_version"],
        total_cost_usd=float(row["total_cost_usd"] or 0),
        voice_cost_usd=float(row["voice_cost_usd"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_flow(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_id: int,
    node_id: int,
    scope_key: str,
    title: str,
    source_title: str,
    interview_format: str,
    target_level: str,
    exchanges: list[IdealInterviewExchange],
    estimated_duration_seconds: int,
    generation_model: str,
    prompt_version: str,
    total_cost_usd: float,
) -> IdealInterviewFlow:
    row = connection.execute(
        f"""
        insert into public.ideal_interview_flows (
            owner_id, book_id, node_id, scope_key, title, source_title,
            interview_format, target_level, topic_count, covered_topic_count,
            estimated_duration_seconds, transcript_json, generation_model,
            prompt_version, total_cost_usd
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {COLUMNS}
        """,
        (
            parse_owner_id(owner_id), book_id, node_id, scope_key, title,
            source_title, interview_format, target_level, len(exchanges),
            len({item.topic_key for item in exchanges}), estimated_duration_seconds,
            Jsonb([item.model_dump(mode="json") for item in exchanges]),
            generation_model, prompt_version, total_cost_usd,
        ),
    ).fetchone()
    return _flow(row)


def find_reusable_flow(
    connection: Connection,
    *,
    owner_id: str | UUID,
    scope_key: str,
    interview_format: str,
    target_level: str,
    generation_model: str,
    prompt_version: str,
) -> IdealInterviewFlow | None:
    row = connection.execute(
        f"""
        select {COLUMNS}
        from public.ideal_interview_flows
        where owner_id = %s and scope_key = %s and interview_format = %s
          and target_level = %s and generation_model = %s and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        (
            parse_owner_id(owner_id), scope_key, interview_format, target_level,
            generation_model, prompt_version,
        ),
    ).fetchone()
    return _flow(row) if row is not None else None


def load_flow(
    connection: Connection,
    flow_id: str | UUID,
    *,
    owner_id: str | UUID,
) -> IdealInterviewFlow:
    row = connection.execute(
        f"select {COLUMNS} from public.ideal_interview_flows where id = %s and owner_id = %s",
        (UUID(str(flow_id)), parse_owner_id(owner_id)),
    ).fetchone()
    if row is None:
        raise IdealInterviewNotFoundError("ideal interview flow not found")
    return _flow(row)


def list_flows(
    connection: Connection,
    *,
    owner_id: str | UUID,
    limit: int = 20,
) -> list[IdealInterviewFlow]:
    rows = connection.execute(
        f"""
        select {COLUMNS}
        from public.ideal_interview_flows
        where owner_id = %s
        order by updated_at desc
        limit %s
        """,
        (parse_owner_id(owner_id), limit),
    ).fetchall()
    return [_flow(row) for row in rows]


def add_voice_cost(
    connection: Connection,
    flow_id: str | UUID,
    *,
    owner_id: str | UUID,
    cost_usd: float,
) -> None:
    if cost_usd <= 0:
        return
    connection.execute(
        """
        update public.ideal_interview_flows
        set voice_cost_usd = voice_cost_usd + %s, updated_at = now()
        where id = %s and owner_id = %s
        """,
        (cost_usd, UUID(str(flow_id)), parse_owner_id(owner_id)),
    )
