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
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-large"
EMBEDDING_DIMENSION = 3072
EMBEDDING_DOCUMENT_FORMAT = "hierarchy-v1"
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


def database_readiness(database_url: str | None = None) -> tuple[bool, bool]:
    """Return canonical and retrieval-schema readiness independently."""

    try:
        owner = resolve_owner_id()
        embedding_model = (
            os.getenv("OPENROUTER_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
        )
        with connection(database_url, readonly=True) as current:
            row = current.execute(
                """
                select
                    to_regclass('public.books') is not null as canonical_ready,
                    to_regclass('public.nodes') is not null as nodes_ready,
                    to_regclass('public.content_blocks') is not null
                        as content_ready,
                    to_regclass('public.chunks') is not null as chunks_ready,
                    to_regclass('public.chunk_embeddings') is not null
                        as embeddings_ready,
                    to_regtype('extensions.vector') is not null as vector_ready,
                    (select count(*) from public.chunks where owner_id = %s)
                        as chunk_count,
                    (select count(*) from public.chunk_embeddings
                     where owner_id = %s
                       and model_name = %s
                       and dimension = %s
                       and document_format_version = %s)
                        as compatible_embedding_count
                """,
                (
                    owner,
                    owner,
                    embedding_model,
                    EMBEDDING_DIMENSION,
                    EMBEDDING_DOCUMENT_FORMAT,
                ),
            ).fetchone()
        canonical_ready = all(
            row[key] for key in ("canonical_ready", "nodes_ready", "content_ready")
        )
        retrieval_ready = (
            all(
                row[key] for key in ("chunks_ready", "embeddings_ready", "vector_ready")
            )
            and row["compatible_embedding_count"] == row["chunk_count"]
        )
        return canonical_ready, retrieval_ready
    except Exception:
        return False, False


def check_database(database_url: str | None = None) -> bool:
    """Return whether the complete migrated Postgres schema is reachable."""

    return all(database_readiness(database_url))


def close_pools() -> None:
    """Close process-global pools during application shutdown/tests."""

    for pool in _POOLS:
        pool.close()
    _POOLS.clear()
    _pool.cache_clear()
