"""Shared Postgres connection and bootstrap-owner configuration."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
import os
from uuid import UUID

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


LOCAL_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
LOCAL_BOOTSTRAP_OWNER_ID = UUID("00000000-0000-4000-8000-000000000001")
_POOLS: list[ConnectionPool] = []


def resolve_database_url(value: str | None = None) -> str:
    """Return an explicit/runtime Postgres URL without logging credentials."""

    return value or os.getenv("DATABASE_URL", LOCAL_DATABASE_URL)


def resolve_owner_id(value: str | UUID | None = None) -> UUID:
    """Return the server-controlled owner used during bootstrap mode."""

    configured = value or os.getenv("DEFAULT_OWNER_ID") or LOCAL_BOOTSTRAP_OWNER_ID
    try:
        return configured if isinstance(configured, UUID) else UUID(str(configured))
    except ValueError as error:
        raise ValueError("DEFAULT_OWNER_ID must be a valid UUID") from error


def _configure(connection: Connection) -> None:
    register_vector(connection)


@lru_cache(maxsize=4)
def _pool(database_url: str) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=int(os.getenv("DATABASE_POOL_MAX_SIZE", "8")),
        timeout=float(os.getenv("DATABASE_POOL_TIMEOUT_SECONDS", "10")),
        kwargs={"row_factory": dict_row},
        configure=_configure,
        open=True,
    )
    _POOLS.append(pool)
    return pool


@contextmanager
def connection(
    database_url: str | None = None,
    *,
    readonly: bool = False,
) -> Iterator[Connection]:
    """Borrow a configured connection and finish one short transaction."""

    with _pool(resolve_database_url(database_url)).connection() as current:
        if readonly:
            current.execute("set transaction read only")
        yield current


def check_database(database_url: str | None = None) -> bool:
    """Return whether the migrated Postgres schema is reachable."""

    try:
        with connection(database_url, readonly=True) as current:
            current.execute("select 1 from public.books limit 1").fetchone()
        return True
    except Exception:
        return False


def close_pools() -> None:
    """Close process-global pools during application shutdown/tests."""

    for pool in _POOLS:
        pool.close()
    _POOLS.clear()
    _pool.cache_clear()
