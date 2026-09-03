"""Build this schema on any PostgreSQL 17, not only on a Supabase project.

`supabase db reset` is the local path and Railway has no equivalent, so this is
the provider-neutral one: apply `ops/postgres/bootstrap.sql` to get the objects
the historical migrations bind to, then replay `supabase/migrations/` exactly
once each, recording every version in `supabase_migrations.schema_migrations`
the same way Supabase does.

Two properties matter more than convenience here:

*Exit on the first error.* A migration that half-applies leaves a database that
looks bootstrapped and is not. On 2026-09-02 a restore whose vector columns
silently failed produced exactly that — a database that passed a glance and had
lost every embedding — which is the failure this refuses to reproduce.

*Never print a connection string.* URLs arrive by environment or by `--url` and
are summarised as host class only.

    uv run python -m scripts.bootstrap_postgres --url-env TARGET_DATABASE_URL
    uv run python -m scripts.bootstrap_postgres --url-env TARGET_DATABASE_URL --audit-only
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import psycopg
from psycopg import sql as psycopg_sql


ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SQL = ROOT / "ops" / "postgres" / "bootstrap.sql"
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"

MINIMUM_SERVER_VERSION_NUM = 170000
MINIMUM_PGVECTOR = (0, 7, 0)

# `\ir` is a psql client directive. This runner speaks to the server directly,
# so it resolves the include itself rather than requiring psql on the path.
_INCLUDE = re.compile(r"^\s*\\ir\s+(?P<path>\S+)\s*$", re.MULTILINE)
_PSQL_DIRECTIVE = re.compile(r"^\s*\\(?!ir\b)\w+.*$", re.MULTILINE)


class BootstrapError(RuntimeError):
    """The target cannot be brought to head. The message is safe to print."""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


def host_class(url: str) -> str:
    """A one-word description of where a URL points, carrying no credentials.

    Every log line about a target goes through this. It is the only thing this
    module is willing to say about a connection string.
    """

    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return "unknown"
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return "local"
    if "supabase" in host:
        return "supabase"
    if "railway" in host or host.endswith(".railway.internal"):
        return "railway"
    if host.endswith(".rlwy.net"):
        return "railway-proxy"
    return "external"


def read_sql(path: Path) -> str:
    """Read a SQL file, inlining `\\ir` includes relative to the including file.

    Keeps `ops/postgres/bootstrap.sql` usable by both psql (which handles `\\ir`
    natively, so `scripts/local_postgres.sh` is unchanged) and this runner.
    """

    text = path.read_text()

    def replace(match: re.Match[str]) -> str:
        included = (path.parent / match.group("path")).resolve()
        if not included.is_file():
            raise BootstrapError(f"{path.name} includes a missing file: {match.group('path')}")
        return read_sql(included)

    text = _INCLUDE.sub(replace, text)
    # `\set ON_ERROR_STOP on` and friends are meaningless over a direct
    # connection — psycopg raises on the first error regardless — and would be
    # syntax errors if sent. Dropping them is safe precisely because the
    # behaviour they ask for is the behaviour this runner already has.
    return _PSQL_DIRECTIVE.sub("", text)


def discover_migrations() -> list[Migration]:
    """Every migration, in filename order, which is timestamp order."""

    found: list[Migration] = []
    seen: dict[str, Path] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version.isdigit():
            raise BootstrapError(f"migration filename does not start with a version: {path.name}")
        if version in seen:
            raise BootstrapError(
                f"two migrations share version {version}: {seen[version].name}, {path.name}"
            )
        seen[version] = path
        found.append(Migration(version=version, path=path))
    if not found:
        raise BootstrapError(f"no migrations found under {MIGRATIONS_DIR}")
    return found


def repository_head() -> str:
    return discover_migrations()[-1].version


def _server_version(connection: psycopg.Connection) -> tuple[int, str]:
    row = connection.execute(
        "select current_setting('server_version_num')::int, current_setting('server_version')"
    ).fetchone()
    assert row is not None
    return int(row[0]), str(row[1])


def _pgvector_version(connection: psycopg.Connection) -> str | None:
    row = connection.execute("select extversion from pg_extension where extname = 'vector'").fetchone()
    return None if row is None else str(row[0])


def _parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_preconditions(connection: psycopg.Connection) -> None:
    """Refuse a target that cannot hold this schema before writing to it."""

    version_num, version_text = _server_version(connection)
    if version_num < MINIMUM_SERVER_VERSION_NUM:
        raise BootstrapError(
            f"PostgreSQL 17 or newer is required, target reports {version_text}"
        )


def check_vector_support(connection: psycopg.Connection) -> None:
    """pgvector must be present and new enough for `halfvec`.

    Checked after the bootstrap, since the bootstrap is what installs it. The
    type check is the real gate — a build could carry the version and not the
    type — and every embedding column in this schema is a `halfvec`.
    """

    installed = _pgvector_version(connection)
    if installed is None:
        raise BootstrapError("pgvector is not installed on the target")
    has_halfvec = connection.execute(
        "select exists (select 1 from pg_type where typname = 'halfvec')"
    ).fetchone()
    if not (has_halfvec and has_halfvec[0]):
        raise BootstrapError(
            f"pgvector {installed} does not provide halfvec; "
            f"{'.'.join(str(p) for p in MINIMUM_PGVECTOR)} or newer is required"
        )
    if _parse_version(installed) < MINIMUM_PGVECTOR:
        raise BootstrapError(
            f"pgvector {installed} is older than "
            f"{'.'.join(str(p) for p in MINIMUM_PGVECTOR)}"
        )


def applied_versions(connection: psycopg.Connection) -> set[str]:
    exists = connection.execute(
        "select to_regclass('supabase_migrations.schema_migrations') is not null"
    ).fetchone()
    if not (exists and exists[0]):
        return set()
    rows = connection.execute("select version from supabase_migrations.schema_migrations").fetchall()
    return {str(row[0]) for row in rows}


def apply_bootstrap(connection: psycopg.Connection) -> None:
    """Apply the compatibility layer. Idempotent, so it runs every time."""

    connection.execute(read_sql(BOOTSTRAP_SQL))
    connection.commit()


def apply_migrations(connection: psycopg.Connection, *, dry_run: bool = False) -> tuple[int, int]:
    """Replay what the ledger does not already record. Returns (applied, skipped).

    Each migration and its ledger row commit together, so an interrupted run
    leaves the ledger honest: what it records is exactly what is in the schema,
    and a rerun resumes at the right place instead of failing on a repeated
    `add column`.
    """

    already = applied_versions(connection)
    applied = skipped = 0
    for migration in discover_migrations():
        if migration.version in already:
            skipped += 1
            continue
        if dry_run:
            print(f"  would apply {migration.name}")
            applied += 1
            continue
        print(f"  applying {migration.name}", flush=True)
        try:
            connection.execute(migration.path.read_text())
            connection.execute(
                "insert into supabase_migrations.schema_migrations (version) values (%s)",
                (migration.version,),
            )
        except psycopg.Error as error:
            connection.rollback()
            raise BootstrapError(
                f"migration {migration.name} failed and was rolled back: "
                f"{str(error).strip().splitlines()[0]}"
            ) from error
        connection.commit()
        applied += 1
    return applied, skipped


def summarise(connection: psycopg.Connection) -> dict[str, object]:
    """What the target now is. Deliberately says nothing about how to reach it."""

    version_num, version_text = _server_version(connection)
    extensions = connection.execute(
        "select extname, extversion from pg_extension order by extname"
    ).fetchall()
    head = connection.execute(
        "select max(version) from supabase_migrations.schema_migrations"
    ).fetchone()
    tables = connection.execute(
        """
        select count(*) from information_schema.tables
        where table_schema in ('public', 'video') and table_type = 'BASE TABLE'
        """
    ).fetchone()
    size = connection.execute(
        "select pg_size_pretty(pg_database_size(current_database())), current_database()"
    ).fetchone()
    assert head is not None and tables is not None and size is not None
    return {
        "server_version": version_text,
        "server_version_num": version_num,
        "database": size[1],
        "size": size[0],
        "extensions": {str(name): str(ver) for name, ver in extensions},
        "migration_head": head[0],
        "repository_head": repository_head(),
        "application_tables": int(tables[0]),
    }


def print_summary(summary: dict[str, object]) -> None:
    print("\ntarget summary")
    print(f"  database            {summary['database']}")
    print(f"  server              PostgreSQL {summary['server_version']}")
    print(f"  size                {summary['size']}")
    print(f"  application tables  {summary['application_tables']}")
    print(f"  migration head      {summary['migration_head']}")
    print(f"  repository head     {summary['repository_head']}")
    extensions = summary["extensions"]
    assert isinstance(extensions, dict)
    print(f"  extensions          {', '.join(f'{k} {v}' for k, v in extensions.items())}")


def resolve_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    if args.url_env:
        value = os.getenv(args.url_env, "").strip()
        if not value:
            raise BootstrapError(f"{args.url_env} is not set")
        return value
    value = os.getenv("TARGET_DATABASE_URL", "").strip()
    if not value:
        raise BootstrapError(
            "no target given: pass --url, --url-env NAME, or set TARGET_DATABASE_URL"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--url", help="target URL (prefer --url-env; argv is visible in ps)")
    source.add_argument("--url-env", help="environment variable holding the target URL")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="report the target's state and change nothing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="apply the bootstrap, then list migrations that would run",
    )
    args = parser.parse_args(argv)

    try:
        url = resolve_url(args)
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"target host class: {host_class(url)}")

    try:
        with psycopg.connect(url, autocommit=False) as connection:
            check_preconditions(connection)
            if args.audit_only:
                summary = summarise(connection)
                print_summary(summary)
                at_head = summary["migration_head"] == summary["repository_head"]
                print(f"\nat repository head: {'yes' if at_head else 'NO'}")
                return 0 if at_head else 1

            print("applying compatibility bootstrap")
            apply_bootstrap(connection)
            check_vector_support(connection)

            print("replaying migrations")
            applied, skipped = apply_migrations(connection, dry_run=args.dry_run)
            print(f"  {applied} applied, {skipped} already recorded")

            summary = summarise(connection)
            print_summary(summary)
            if not args.dry_run and summary["migration_head"] != summary["repository_head"]:
                print(
                    f"\nerror: target head {summary['migration_head']} is not "
                    f"repository head {summary['repository_head']}",
                    file=sys.stderr,
                )
                return 1
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except psycopg.Error as error:
        # Connection strings can appear in psycopg's own messages; the first
        # line is the server's complaint and is what is wanted.
        print(f"error: {str(error).strip().splitlines()[0]}", file=sys.stderr)
        return 1

    print("\nbootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
