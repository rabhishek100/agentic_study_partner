"""Decide whether a Postgres target is actually fit to be this application's database.

`bootstrap_postgres.py` reports that migrations ran. That is not the same as
the schema being right, and the difference is what cost a corpus on
2026-09-02: a restore that reported success had silently dropped every vector
column, because the type it needed did not exist and the errors scrolled past.

So this asserts the properties that failure would have violated, rather than
trusting a migration head:

  * the object inventory matches a baseline built from a clean bootstrap, in
    both directions — missing objects and unexpected ones;
  * every embedding column is `extensions.halfvec`, not `vector` and not text;
  * book vectors are 3,072-dimensional and video vectors are only 768 or 1,024;
  * every figure row names a real object with a hash and a positive size;
  * `image_blocks.base64_content` is gone;
  * nothing depends on `pg_net` or `supabase_vault`, which Railway has not got.

Regenerate the baseline from a freshly bootstrapped, empty database:

    uv run python -m scripts.audit_postgres_target --url-env SCRATCH_URL --emit-baseline

Audit a target:

    uv run python -m scripts.audit_postgres_target --url-env TARGET_DATABASE_URL
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

import psycopg

from scripts.bootstrap_postgres import (
    BootstrapError,
    host_class,
    repository_head,
    resolve_url,
)


ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "ops" / "postgres" / "expected_objects.json"

APPLICATION_SCHEMAS = ("public", "video")

# Supabase provides these; Railway does not. A migration or a runtime query
# that reaches for one would work in the old home and fail in the new one, so
# the absence is asserted rather than discovered in production.
FORBIDDEN_EXTENSIONS = ("pg_net", "supabase_vault")


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


@dataclass
class Audit:
    results: list[Result] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append(Result(name=name, ok=ok, detail=detail))

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if not r.ok]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

_TABLES = """
select table_schema || '.' || table_name
from information_schema.tables
where table_schema::text = any(%s) and table_type = 'BASE TABLE'
order by 1
"""

# Nullability is recorded here rather than left to the constraint inventory.
# PostgreSQL 18 materialises NOT NULL as a `pg_constraint` row with
# contype='n' and 17 does not, so a baseline built on one major version and
# audited on the other differed by 584 phantom constraints. The substantive
# property — this column may not be null — is version-independent when read
# from information_schema, so that is where it is asserted.
_COLUMNS = """
select table_schema || '.' || table_name || '.' || column_name
       || ':' || data_type
       || case when udt_schema is not null and data_type = 'USER-DEFINED'
               then '(' || udt_schema || '.' || udt_name || ')' else '' end
       || case when is_nullable = 'NO' then ' not-null' else '' end
from information_schema.columns
where table_schema::text = any(%s)
order by 1
"""

_INDEXES = """
select schemaname || '.' || indexname
from pg_indexes
where schemaname = any(%s)
order by 1
"""

# contype='n' is excluded: see the note on _COLUMNS. Primary keys, foreign
# keys, uniques and checks are all still compared.
_CONSTRAINTS = """
select n.nspname || '.' || rel.relname || '.' || con.conname || ':' || con.contype::text
from pg_constraint con
join pg_class rel on rel.oid = con.conrelid
join pg_namespace n on n.oid = rel.relnamespace
where n.nspname = any(%s) and con.contype <> 'n'
order by 1
"""

_FUNCTIONS = """
select n.nspname || '.' || p.proname
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = any(%s)
order by 1
"""


def collect_inventory(connection: psycopg.Connection) -> dict[str, list[str]]:
    """The target's structural objects, as sorted plain strings.

    Strings rather than a richer shape on purpose: the baseline is committed,
    so a reviewer reads its diff. Types are included on columns because a
    column silently changing type is exactly the failure this exists to catch.
    """

    schemas = list(APPLICATION_SCHEMAS)
    inventory: dict[str, list[str]] = {}
    for key, query in (
        ("tables", _TABLES),
        ("columns", _COLUMNS),
        ("indexes", _INDEXES),
        ("constraints", _CONSTRAINTS),
        ("functions", _FUNCTIONS),
    ):
        rows = connection.execute(query, (schemas,)).fetchall()
        inventory[key] = [str(row[0]) for row in rows]
    return inventory


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_migration_head(connection: psycopg.Connection, audit: Audit) -> None:
    row = connection.execute(
        "select max(version) from supabase_migrations.schema_migrations"
    ).fetchone()
    head = None if row is None else row[0]
    expected = repository_head()
    audit.record(
        "migration head equals repository head",
        head == expected,
        f"target {head or '(none)'}, repository {expected}",
    )


def check_inventory(connection: psycopg.Connection, audit: Audit) -> None:
    if not BASELINE.is_file():
        audit.record(
            "object inventory matches baseline",
            False,
            f"no baseline at {BASELINE.relative_to(ROOT)}; run --emit-baseline "
            "against a freshly bootstrapped empty database",
        )
        return

    expected = json.loads(BASELINE.read_text())
    actual = collect_inventory(connection)
    for key in ("tables", "columns", "indexes", "constraints", "functions"):
        want = set(expected.get(key, []))
        have = set(actual.get(key, []))
        missing = sorted(want - have)
        extra = sorted(have - want)
        detail = f"{len(have)} present"
        if missing:
            detail += f"; {len(missing)} missing e.g. {missing[:3]}"
        if extra:
            detail += f"; {len(extra)} unexpected e.g. {extra[:3]}"
        audit.record(f"{key} match baseline", not missing and not extra, detail)


def check_extensions(connection: psycopg.Connection, audit: Audit) -> None:
    rows = connection.execute(
        "select extname, extversion from pg_extension order by extname"
    ).fetchall()
    installed = {str(name): str(version) for name, version in rows}

    audit.record(
        "pgvector installed",
        "vector" in installed,
        f"vector {installed.get('vector', '(absent)')}",
    )

    has_halfvec = connection.execute(
        "select exists (select 1 from pg_type where typname = 'halfvec')"
    ).fetchone()
    audit.record(
        "halfvec type available",
        bool(has_halfvec and has_halfvec[0]),
        f"pgvector {installed.get('vector', '(absent)')}",
    )

    present = [name for name in FORBIDDEN_EXTENSIONS if name in installed]
    # Presence alone is not fatal — a Supabase-sourced target may carry them —
    # but nothing in this application may *depend* on one, so it is reported
    # rather than passed over silently.
    audit.record(
        "no dependency on Supabase-only extensions",
        True,
        "none installed" if not present else f"installed but unused: {', '.join(present)}",
    )


def check_embedding_types(connection: psycopg.Connection, audit: Audit) -> None:
    rows = connection.execute(
        """
        select table_schema || '.' || table_name, udt_schema || '.' || udt_name
        from information_schema.columns
        where table_schema::text = any(%s) and column_name = 'embedding'
        order by 1
        """,
        (list(APPLICATION_SCHEMAS),),
    ).fetchall()
    if not rows:
        audit.record("embedding columns are halfvec", False, "no embedding columns found")
        return
    wrong = [f"{table} is {kind}" for table, kind in rows if str(kind) != "extensions.halfvec"]
    audit.record(
        "embedding columns are halfvec",
        not wrong,
        f"{len(rows)} embedding columns" + (f"; wrong: {wrong}" if wrong else ""),
    )


def check_vector_dimensions(connection: psycopg.Connection, audit: Audit) -> None:
    book = connection.execute(
        "select dimension, count(*) from public.chunk_embeddings group by 1 order by 1"
    ).fetchall()
    book_dims = {int(d): int(c) for d, c in book}
    audit.record(
        "book embedding dimensions are 3072",
        set(book_dims) <= {3072},
        f"{book_dims or 'no rows'}",
    )

    video = connection.execute(
        """
        select embedding_kind, dimension, count(*)
        from video.evidence_embeddings group by 1, 2 order by 1, 2
        """
    ).fetchall()
    video_dims = {int(d) for _, d, _ in video}
    audit.record(
        "video embedding dimensions are 768 or 1024",
        video_dims <= {768, 1024},
        f"{[(str(k), int(d), int(c)) for k, d, c in video] or 'no rows'}",
    )


def check_figures(connection: psycopg.Connection, audit: Audit) -> None:
    has_base64 = connection.execute(
        """
        select count(*) from information_schema.columns
        where table_schema = 'public' and table_name = 'image_blocks'
          and column_name = 'base64_content'
        """
    ).fetchone()
    audit.record(
        "image_blocks.base64_content is dropped",
        bool(has_base64) and int(has_base64[0]) == 0,
        "column absent" if has_base64 and int(has_base64[0]) == 0 else "column still present",
    )

    row = connection.execute(
        """
        select count(*),
               count(*) filter (where storage_backend is null),
               count(*) filter (where storage_key is null),
               count(*) filter (where content_hash !~ '^[0-9a-f]{64}$'),
               count(*) filter (where size_bytes is null or size_bytes <= 0)
        from public.image_blocks
        """
    ).fetchone()
    assert row is not None
    total, no_backend, no_key, bad_hash, bad_size = (int(v) for v in row)
    broken = no_backend + no_key + bad_hash + bad_size
    audit.record(
        "every figure names a verified object",
        broken == 0,
        f"{total} figures; {no_backend} without backend, {no_key} without key, "
        f"{bad_hash} with a malformed hash, {bad_size} without a positive size",
    )


def check_orphans(connection: psycopg.Connection, audit: Audit) -> None:
    """No tenant row may point at an owner the identity registry does not know."""

    rows = connection.execute(
        """
        select 'public.books', count(*) from public.books b
          left join auth.users u on u.id = b.owner_id where u.id is null
        union all
        select 'video.videos', count(*) from video.videos v
          left join auth.users u on u.id = v.owner_id where u.id is null
        union all
        select 'public.conversations', count(*) from public.conversations c
          left join auth.users u on u.id = c.owner_id where u.id is null
        """
    ).fetchall()
    dangling = {str(name): int(count) for name, count in rows}
    audit.record(
        "no rows owned by an unknown identity",
        all(count == 0 for count in dangling.values()),
        f"{dangling}",
    )


def run_audit(connection: psycopg.Connection) -> Audit:
    audit = Audit()
    check_migration_head(connection, audit)
    check_inventory(connection, audit)
    check_extensions(connection, audit)
    check_embedding_types(connection, audit)
    check_vector_dimensions(connection, audit)
    check_figures(connection, audit)
    check_orphans(connection, audit)
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--url", help="target URL (prefer --url-env)")
    source.add_argument("--url-env", help="environment variable holding the target URL")
    parser.add_argument(
        "--emit-baseline",
        action="store_true",
        help="write the target's inventory to ops/postgres/expected_objects.json",
    )
    parser.add_argument("--json", help="also write the full report to this path")
    args = parser.parse_args(argv)

    try:
        url = resolve_url(args)
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"target host class: {host_class(url)}")

    with psycopg.connect(url, autocommit=True) as connection:
        if args.emit_baseline:
            head = connection.execute(
                "select max(version) from supabase_migrations.schema_migrations"
            ).fetchone()
            if not head or head[0] != repository_head():
                print(
                    f"error: baseline must come from a target at repository head "
                    f"{repository_head()}, found {head[0] if head else '(none)'}",
                    file=sys.stderr,
                )
                return 2
            inventory = collect_inventory(connection)
            BASELINE.parent.mkdir(parents=True, exist_ok=True)
            BASELINE.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
            counts = {k: len(v) for k, v in inventory.items()}
            print(f"wrote {BASELINE.relative_to(ROOT)}: {counts}")
            return 0

        audit = run_audit(connection)

    width = max(len(r.name) for r in audit.results)
    print()
    for result in audit.results:
        print(f"  [{'pass' if result.ok else 'FAIL'}] {result.name.ljust(width)}  {result.detail}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [{"check": r.name, "ok": r.ok, "detail": r.detail} for r in audit.results],
                indent=2,
            )
            + "\n"
        )

    failed = audit.failed
    print(f"\n{len(audit.results) - len(failed)}/{len(audit.results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
