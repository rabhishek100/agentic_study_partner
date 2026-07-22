"""Local-Supabase helpers for single-owner Postgres integration tests."""

import os
from unittest.mock import patch
from uuid import uuid4

from storage.database import connection, resolve_database_url


class PostgresOwnerMixin:
    """Provision one isolated owner without asserting cross-user behavior."""

    database_url: str
    owner_id: str

    def setUpPostgresOwner(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_id = str(uuid4())
        self._owner_environment = patch.dict(
            os.environ,
            {"DEFAULT_OWNER_ID": self.owner_id},
        )
        self._owner_environment.start()
        with connection(self.database_url) as database:
            database.execute(
                """
                insert into auth.users (id, email, raw_user_meta_data)
                values (%s, %s, '{}'::jsonb)
                """,
                (self.owner_id, f"{self.owner_id}@test.local"),
            )

    def tearDownPostgresOwner(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s",
                (self.owner_id,),
            )
        self._owner_environment.stop()
