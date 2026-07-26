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
    """Skip queue tests when this database has live jobs or a live worker.

    The worker's claim intentionally serves every owner, so a worker pointed
    at the same database competes with these tests for the jobs they create.
    Both directions matter: the tests would mutate a real user's job, and a
    running worker steals the jobs a test just queued, which surfaces as an
    intermittent "claim returned nothing" failure that looks like a bug in
    the queue. Both have happened.

    A recent heartbeat is the only evidence of a worker this can see; an idle
    worker with nothing to claim leaves no trace, so stop local workers before
    running these tests. CI's database is empty and has no worker.
    """

    with connection(resolve_database_url()) as database:
        row = database.execute(
            """
            select
                count(*) filter (where status = any(%s)) as busy,
                count(*) filter (
                    where lease_owner is not null
                      and heartbeat_at > now() - interval '2 minutes'
                ) as leased
            from ingestion_jobs
            """,
            (list(CLAIMABLE_OR_RUNNING),),
        ).fetchone()
    if row["leased"]:
        test.skipTest(
            "a worker is actively leasing jobs in this database; it would "
            "race these tests for the jobs they create. Stop the worker."
        )
    if row["busy"]:
        test.skipTest(
            f"{row['busy']} live ingestion job(s) in this database; queue "
            "tests would claim them. Let them finish or use a clean database."
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
