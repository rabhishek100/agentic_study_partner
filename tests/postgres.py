"""Local-Supabase helpers for owner-scoped Postgres integration tests."""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url


CLAIMABLE_OR_RUNNING = (
    "queued",
    "retry_scheduled",
    "validating",
    "parsing",
    "persisting",
    "chunking",
    "embedding",
    "verifying",
)


def require_empty_ingestion_queue(test: unittest.TestCase) -> None:
    """Skip queue tests when this database has live ingestion jobs.

    The worker's claim intentionally serves every owner, so a test worker
    pointed at a shared development database would claim - and mutate - a
    real user's job. That happened once; these tests now refuse to run
    beside live work instead. CI's database is always empty.
    """

    with connection(resolve_database_url()) as database:
        busy = database.execute(
            "select count(*) as busy from ingestion_jobs where status = any(%s)",
            (list(CLAIMABLE_OR_RUNNING),),
        ).fetchone()["busy"]
    if busy:
        test.skipTest(
            f"{busy} live ingestion job(s) in this database; queue tests "
            "would claim them. Let them finish or use a clean database."
        )


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
