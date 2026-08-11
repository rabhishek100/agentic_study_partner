"""Persisted cache for dynamic suggested questions / starter prompts."""

from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id

SUGGESTED_QUESTIONS_CACHE_VERSION = "concise-v2"


def versioned_suggested_questions_key(scope_key: str) -> str:
    """Isolate cached questions when the generation contract changes."""
    return f"{scope_key}:{SUGGESTED_QUESTIONS_CACHE_VERSION}"


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
