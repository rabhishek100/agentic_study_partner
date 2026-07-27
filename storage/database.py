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


def parse_owner_id(value: str | UUID) -> UUID:
    """Validate an owner UUID. There is deliberately no implicit default.

    Every request-serving path must derive its owner from a verified access
    token, so a missing owner is a programming error rather than something to
    quietly fill in from the environment.
    """

    if isinstance(value, UUID):
        return value
    if value is None or not str(value).strip():
        raise ValueError("owner_id is required")
    try:
        return UUID(str(value))
    except ValueError as error:
        raise ValueError("owner_id must be a valid UUID") from error


def environment_owner_id() -> UUID:
    """Return the owner used by local CLI and evaluation commands only.

    Request handlers and the ingestion worker must never call this: they carry
    the verified token subject or the owner stored on the claimed job.
    """

    configured = os.getenv("DEFAULT_OWNER_ID") or LOCAL_BOOTSTRAP_OWNER_ID
    return parse_owner_id(configured)


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
    """Return canonical and retrieval schema readiness independently.

    This is infrastructure readiness, not per-book completeness. A book that
    is still being chunked or embedded must never make the whole API report
    itself unavailable; that check belongs to ``book_retrieval_completeness``.
    """

    try:
        with connection(database_url, readonly=True) as current:
            row = current.execute(
                """
                select
                    to_regclass('public.books') is not null as canonical_ready,
                    to_regclass('public.nodes') is not null as nodes_ready,
                    to_regclass('public.content_blocks') is not null
                        as content_ready,
                    to_regclass('public.ingestion_jobs') is not null
                        as jobs_ready,
                    to_regclass('public.chunks') is not null as chunks_ready,
                    to_regclass('public.chunk_embeddings') is not null
                        as embeddings_ready,
                    to_regtype('extensions.vector') is not null as vector_ready
                """
            ).fetchone()
        canonical_ready = all(
            row[key]
            for key in ("canonical_ready", "nodes_ready", "content_ready", "jobs_ready")
        )
        retrieval_ready = all(
            row[key] for key in ("chunks_ready", "embeddings_ready", "vector_ready")
        )
        return canonical_ready, retrieval_ready
    except Exception:
        return False, False


def book_retrieval_completeness(
    connection_or_url: Connection | str | None,
    *,
    owner_id: str | UUID,
    book_id: int,
) -> tuple[int, int]:
    """Return one book's chunk count and its compatible embedding count.

    Equality means the book can be answered with every configured retrieval
    mode. Callers decide what to do about a shortfall; this never inspects
    another owner's data.
    """

    owner = parse_owner_id(owner_id)
    embedding_model = os.getenv("OPENROUTER_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
    statement = """
        select
            (select count(*) from public.chunks
             where owner_id = %s and source_book_id = %s) as chunk_count,
            (select count(*) from public.chunk_embeddings
             where owner_id = %s
               and source_book_id = %s
               and model_name = %s
               and dimension = %s
               and document_format_version = %s) as embedding_count
    """
    parameters = (
        owner,
        book_id,
        owner,
        book_id,
        embedding_model,
        EMBEDDING_DIMENSION,
        EMBEDDING_DOCUMENT_FORMAT,
    )
    if isinstance(connection_or_url, Connection):
        row = connection_or_url.execute(statement, parameters).fetchone()
    else:
        with connection(connection_or_url, readonly=True) as current:
            row = current.execute(statement, parameters).fetchone()
    return int(row["chunk_count"]), int(row["embedding_count"])


def check_database(database_url: str | None = None) -> bool:
    """Return whether the complete migrated Postgres schema is reachable."""

    return all(database_readiness(database_url))


def close_pools() -> None:
    """Close process-global pools during application shutdown/tests."""

    for pool in _POOLS:
        pool.close()
    _POOLS.clear()
    _pool.cache_clear()
