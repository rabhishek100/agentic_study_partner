"""Persisted cache for dynamic suggested questions / starter prompts."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id

SUGGESTED_QUESTIONS_CACHE_VERSION = "concise-v2"


def versioned_suggested_questions_key(scope_key: str) -> str:
    """Isolate cached questions when the generation contract changes."""
    return f"{scope_key}:{SUGGESTED_QUESTIONS_CACHE_VERSION}"


def ensure_suggested_questions_table(connection: Connection) -> None:
    """Ensure the suggested_questions_cache table exists."""
    connection.execute(
        """
        create table if not exists public.suggested_questions_cache (
            owner_id uuid not null,
            scope_type text not null check (scope_type in ('book', 'library', 'video')),
            scope_key text not null,
            questions_json jsonb not null default '[]'::jsonb,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            primary key (owner_id, scope_type, scope_key),
            constraint suggested_questions_are_array check (jsonb_typeof(questions_json) = 'array')
        );
        """
    )


def get_cached_suggested_questions(
    connection: Connection,
    *,
    owner_id: str | UUID,
    scope_type: str,
    scope_key: str,
) -> list[str] | None:
    """Fetch cached suggested questions for a given owner and scope if present."""
    # Check if table exists first before querying to support unmigrated test DBs safely
    table_check = connection.execute(
        "select to_regclass('public.suggested_questions_cache') is not null as exists"
    ).fetchone()
    if not table_check or not table_check["exists"]:
        return None

    row = connection.execute(
        """
        select questions_json
        from public.suggested_questions_cache
        where owner_id = %s
          and scope_type = %s
          and scope_key = %s
        """,
        (parse_owner_id(owner_id), scope_type, scope_key),
    ).fetchone()
    if not row or not row["questions_json"]:
        return None
    return list(row["questions_json"])


def save_cached_suggested_questions(
    connection: Connection,
    *,
    owner_id: str | UUID,
    scope_type: str,
    scope_key: str,
    questions: list[str],
) -> list[str]:
    """Store or update cached suggested questions for a given owner and scope."""
    ensure_suggested_questions_table(connection)
    row = connection.execute(
        """
        insert into public.suggested_questions_cache
            (owner_id, scope_type, scope_key, questions_json, updated_at)
        values (%s, %s, %s, %s, now())
        on conflict (owner_id, scope_type, scope_key) do update
        set questions_json = excluded.questions_json,
            updated_at = now()
        returning questions_json
        """,
        (parse_owner_id(owner_id), scope_type, scope_key, Jsonb(questions)),
    ).fetchone()
    return list(row["questions_json"])
