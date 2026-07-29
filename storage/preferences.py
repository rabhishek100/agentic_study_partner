"""Owner-scoped editable study preferences."""

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id


def load_prompt_profile(
    connection: Connection,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        select prompt_profile_json
        from study_preferences
        where owner_id = %s
        """,
        (parse_owner_id(owner_id),),
    ).fetchone()
    return dict(row["prompt_profile_json"]) if row else None


def save_prompt_profile(
    connection: Connection,
    *,
    owner_id: str | UUID,
    profile: dict[str, Any],
) -> dict[str, Any]:
    row = connection.execute(
        """
        insert into study_preferences (owner_id, prompt_profile_json)
        values (%s, %s)
        on conflict (owner_id) do update
        set prompt_profile_json = excluded.prompt_profile_json,
            updated_at = now()
        returning prompt_profile_json
        """,
        (parse_owner_id(owner_id), Jsonb(profile)),
    ).fetchone()
    return dict(row["prompt_profile_json"])
