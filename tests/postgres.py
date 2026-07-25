"""Local-Supabase helpers for owner-scoped Postgres integration tests."""

from uuid import uuid4

from storage.database import connection, resolve_database_url


class PostgresOwnerMixin:
    """Provision one isolated owner that tests must pass explicitly."""

    database_url: str
    owner_id: str

    def setUpPostgresOwner(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_id = str(uuid4())
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
