"""Copy this application's data into a target that already has its schema.

Not `pg_restore`. A dump of the old Supabase database carries `supabase_admin`
ownership, `SET ROLE` statements, grants to platform roles, and vector columns
declared `extensions.vector` — none of which an ordinary target reproduces
reliably. On 2026-09-02 restoring one produced a database that looked restored
and had silently lost every embedding, because the type it needed did not
exist and twenty-six cascading errors scrolled past.

So the split is deliberate: `bootstrap_postgres.py` builds the schema from the
repository's own migrations, and this moves only rows into it. Nothing about
the source's roles, ownership or platform extensions comes along.

What it guarantees:

  * the target is at repository head before a single row is written;
  * the identity registry is populated before tenant rows that reference it;
  * tables are copied parents-first, with foreign-key checks suspended for
    the session and then proved by anti-join afterwards, because books and
    ingestion_jobs reference each other and no ordering satisfies both;
  * every sequence is reset and ANALYZE is run, so the target is not merely
    correct but usable;
  * exact counts, primary-key digests and vector dimensions are compared
    afterwards, and a mismatch is a nonzero exit.

    uv run python -m scripts.copy_database \
        --source-env SOURCE_DATABASE_URL --target-env TARGET_DATABASE_URL
    uv run python -m scripts.copy_database ... --verify-only
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
import sys
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from scripts.bootstrap_postgres import BootstrapError, host_class, repository_head


DEFAULT_BATCH_ROWS = 5_000

APPLICATION_SCHEMAS = ("public", "video")

# Copied first and by hand. Tenant tables carry `owner_id` foreign keys into
# it, so every one of them would fail without this, and only the two columns
# this application actually uses are moved — a hosted `auth.users` has dozens
# of GoTrue columns the shadow registry does not have and must not inherit.
IDENTITY_TABLE = "auth.users"
IDENTITY_COLUMNS = ("id", "email")


@dataclass
class TableReport:
    name: str
    source_rows: int = 0
    target_rows: int = 0
    copied: int = 0
    source_digest: str | None = None
    target_digest: str | None = None

    @property
    def counts_match(self) -> bool:
        return self.source_rows == self.target_rows

    @property
    def digests_match(self) -> bool:
        return self.source_digest == self.target_digest

    @property
    def ok(self) -> bool:
        return self.counts_match and self.digests_match


@dataclass
class CopyReport:
    tables: list[TableReport] = field(default_factory=list)
    identity_rows: int = 0
    notes: list[str] = field(default_factory=list)
    claimable: dict[str, int] = field(default_factory=dict)

    @property
    def failures(self) -> list[TableReport]:
        return [t for t in self.tables if not t.ok]


def _endpoint(url: str) -> tuple[str, int | None, str]:
    parts = urlsplit(url)
    return (parts.hostname or "", parts.port, (parts.path or "").lstrip("/"))


def refuse_same_endpoint(source: str, target: str) -> None:
    """A copy onto itself would be a slow, destructive no-op."""

    if _endpoint(source) == _endpoint(target):
        raise BootstrapError(
            "source and target name the same host, port and database"
        )


def _major_version(connection: psycopg.Connection) -> int:
    row = connection.execute("select current_setting('server_version_num')::int").fetchone()
    assert row is not None
    return int(row[0]) // 10000


def _server_version_text(connection: psycopg.Connection) -> str:
    row = connection.execute("select current_setting('server_version')").fetchone()
    assert row is not None
    return str(row[0])


def _migration_head(connection: psycopg.Connection) -> str | None:
    exists = connection.execute(
        "select to_regclass('supabase_migrations.schema_migrations') is not null"
    ).fetchone()
    if not (exists and exists[0]):
        return None
    row = connection.execute(
        "select max(version) from supabase_migrations.schema_migrations"
    ).fetchone()
    return None if row is None else row[0]


def ordered_tables(connection: psycopg.Connection) -> list[str]:
    """Application tables, parents before children.

    Derived from the live foreign-key graph rather than hardcoded, because a
    hand-maintained order silently rots the moment a migration adds a table —
    and the failure it produces is a foreign-key violation two thousand rows
    into a copy.

    Foreign-key checks are suspended during the copy anyway, so this is not
    load-bearing; it makes a failure legible, because the table that broke is
    the one being copied rather than a random later one.
    """

    rows = connection.execute(
        """
        select format('%%I.%%I', n.nspname, c.relname) as name,
               coalesce(array_agg(distinct format('%%I.%%I', pn.nspname, p.relname))
                        filter (where p.oid is not null and p.oid <> c.oid), '{}') as parents
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        left join pg_constraint fk
               on fk.conrelid = c.oid and fk.contype = 'f'
        left join pg_class p on p.oid = fk.confrelid
        left join pg_namespace pn on pn.oid = p.relnamespace
        where c.relkind = 'r' and n.nspname = any(%s)
        group by 1
        """,
        (list(APPLICATION_SCHEMAS),),
    ).fetchall()

    parents = {str(name): {str(p) for p in parent_list} for name, parent_list in rows}
    known = set(parents)
    ordered: list[str] = []
    placed: set[str] = set()

    # Kahn's algorithm, alphabetical within a level so the order is stable and
    # a run's log can be diffed against another's.
    while len(placed) < len(known):
        ready = sorted(
            name
            for name in known - placed
            if not (parents[name] & known) - placed
        )
        if not ready:
            # A cycle. Real here: books.ingestion_job_id and
            # ingestion_jobs.book_id point at each other. Placing the rest
            # alphabetically is safe because the checks are off regardless.
            ready = sorted(known - placed)
            ordered.extend(ready)
            placed.update(ready)
            break
        ordered.extend(ready)
        placed.update(ready)
    return ordered


def table_columns(connection: psycopg.Connection, table: str) -> list[str]:
    schema, name = table.replace('"', "").split(".")
    rows = connection.execute(
        """
        select column_name from information_schema.columns
        where table_schema = %s and table_name = %s
          and is_generated = 'NEVER'
        order by ordinal_position
        """,
        (schema, name),
    ).fetchall()
    return [str(row[0]) for row in rows]


def exact_count(connection: psycopg.Connection, table: str) -> int:
    row = connection.execute(
        sql.SQL("select count(*) from {}").format(sql.SQL(table))
    ).fetchone()
    assert row is not None
    return int(row[0])


def primary_key_columns(connection: psycopg.Connection, table: str) -> list[str]:
    rows = connection.execute(
        """
        select a.attname
        from pg_index i
        join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
        where i.indrelid = %s::regclass and i.indisprimary
        order by array_position(i.indkey, a.attnum)
        """,
        (table,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def table_digest(connection: psycopg.Connection, table: str) -> str | None:
    """An order-independent digest of a table's primary keys.

    Order-independent on purpose: two databases holding identical rows can
    return them in different physical orders, and a digest that cared would
    report a difference that is not one. Summing per-row hashes is commutative,
    so it does not.

    Primary keys rather than whole rows because whole-row text depends on
    column order and on how each type renders, and the copy preserves neither
    trivially. Counts plus key sets is what "the same rows are there" means;
    the per-table column checks in the audit cover shape.
    """

    keys = primary_key_columns(connection, table)
    if not keys:
        return None
    expression = sql.SQL(" || '|' || ").join(
        sql.SQL("coalesce({}::text, '')").format(sql.Identifier(k)) for k in keys
    )
    row = connection.execute(
        sql.SQL(
            """
            select coalesce(
                sum(('x' || substr(md5({expr}), 1, 15))::bit(60)::bigint)::text,
                'empty'
            )
            from {table}
            """
        ).format(expr=expression, table=sql.SQL(table))
    ).fetchone()
    return None if row is None else str(row[0])


def copy_identity_rows(
    source: psycopg.Connection, target: psycopg.Connection, *, dry_run: bool
) -> int:
    """Move the identity registry first, with only the columns this app uses."""

    rows = source.execute(
        sql.SQL("select {} from {}").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in IDENTITY_COLUMNS),
            sql.SQL(IDENTITY_TABLE),
        )
    ).fetchall()
    if dry_run:
        return len(rows)
    with target.cursor() as cursor:
        for row in rows:
            cursor.execute(
                sql.SQL(
                    """
                    insert into {} ({}) values (%s, %s)
                    on conflict (id) do update set email = excluded.email
                    """
                ).format(
                    sql.SQL(IDENTITY_TABLE),
                    sql.SQL(", ").join(sql.Identifier(c) for c in IDENTITY_COLUMNS),
                ),
                tuple(row),
            )
    target.commit()
    return len(rows)


def suspend_referential_integrity(target: psycopg.Connection) -> None:
    """Stop foreign keys firing during the copy, and say so if that is refused.

    `set constraints all deferred` looks like the right tool and does nothing
    here: it defers only constraints declared DEFERRABLE, and none of these
    are. The graph also contains a genuine cycle — `books.ingestion_job_id`
    references `ingestion_jobs`, whose `book_id` references `books` — so no
    ordering of whole-table copies can satisfy every key as it is written.
    There is no order to find; the checks have to be off and then verified.

    `session_replication_role` is the mechanism, and it needs superuser. If it
    is refused, that is worth failing on rather than working around: the
    alternative is disabling triggers table by table, which needs ownership of
    all 55 and fails halfway with the copy half done.
    """

    try:
        target.execute("set session_replication_role = 'replica'")
        target.commit()
    except psycopg.Error as error:
        target.rollback()
        raise BootstrapError(
            "cannot suspend foreign-key checks on the target: "
            f"{str(error).strip().splitlines()[0]}. This copy needs a role that "
            "may set session_replication_role, because books and ingestion_jobs "
            "reference each other and no copy order satisfies both."
        ) from error


def restore_referential_integrity(target: psycopg.Connection) -> None:
    target.execute("set session_replication_role = 'origin'")
    target.commit()


def copy_table(
    source: psycopg.Connection,
    target: psycopg.Connection,
    table: str,
    *,
    batch_rows: int,
    dry_run: bool,
) -> int:
    """Stream one table across.

    COPY in both directions rather than row-by-row inserts: it is the only
    mechanism that moves a 123,000-row `halfvec` table in reasonable time, and
    it round-trips vectors through their own text representation rather than
    through a Python client that would have to know the type.

    Text format, not binary. Binary COPY is faster and is defined per type by
    that type's send/recv functions, which makes it a wire format shared
    between two servers that may not be the same major version — the source
    here is PostgreSQL 17 and the Railway target is 18. Text format is the
    portable one, and at a quarter of a million rows the difference is seconds.

    Foreign-key checks are already suspended for the session by
    `suspend_referential_integrity`, and `verify_foreign_keys` proves afterwards
    that nothing dangled.
    """

    columns = table_columns(source, table)
    if not columns:
        return 0
    if dry_run:
        return exact_count(source, table)

    column_list = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    with target.cursor() as writer:
        with source.cursor() as reader:
            with reader.copy(
                sql.SQL("copy (select {} from {}) to stdout (format text)").format(
                    column_list, sql.SQL(table)
                )
            ) as outbound:
                with writer.copy(
                    sql.SQL("copy {} ({}) from stdin (format text)").format(
                        sql.SQL(table), column_list
                    )
                ) as inbound:
                    for block in outbound:
                        inbound.write(block)
    target.commit()
    return exact_count(target, table)


def verify_foreign_keys(target: psycopg.Connection) -> list[str]:
    """Prove every foreign key the copy bypassed actually holds.

    This is the other half of suspending the checks. Postgres will not
    re-validate a constraint it believes is already valid, so asking it to is
    not an option; each key is instead checked by the anti-join it stands for.
    A copy that silently left dangling references would otherwise look like a
    complete success.
    """

    constraints = target.execute(
        """
        select con.conname,
               format('%%I.%%I', cn.nspname, child.relname),
               format('%%I.%%I', pn.nspname, parent.relname),
               con.conkey, con.confkey
        from pg_constraint con
        join pg_class child on child.oid = con.conrelid
        join pg_namespace cn on cn.oid = child.relnamespace
        join pg_class parent on parent.oid = con.confrelid
        join pg_namespace pn on pn.oid = parent.relnamespace
        where con.contype = 'f' and cn.nspname = any(%s)
        order by 2, 1
        """,
        (list(APPLICATION_SCHEMAS),),
    ).fetchall()

    problems: list[str] = []
    for name, child, parent, child_cols, parent_cols in constraints:
        child_names = _attribute_names(target, child, list(child_cols))
        parent_names = _attribute_names(target, parent, list(parent_cols))
        # MATCH SIMPLE: a row with any NULL in the key is exempt, so those are
        # excluded rather than counted as dangling.
        not_null = sql.SQL(" and ").join(
            sql.SQL("c.{} is not null").format(sql.Identifier(c)) for c in child_names
        )
        joined = sql.SQL(" and ").join(
            sql.SQL("p.{} = c.{}").format(sql.Identifier(p), sql.Identifier(c))
            for p, c in zip(parent_names, child_names)
        )
        row = target.execute(
            sql.SQL(
                """
                select count(*) from {child} c
                where {not_null}
                  and not exists (select 1 from {parent} p where {joined})
                """
            ).format(
                child=sql.SQL(str(child)),
                parent=sql.SQL(str(parent)),
                not_null=not_null,
                joined=joined,
            )
        ).fetchone()
        dangling = int(row[0]) if row else 0
        if dangling:
            problems.append(f"{child}.{name} has {dangling} dangling references")
    return problems


def _attribute_names(
    connection: psycopg.Connection, table: str, numbers: list[int]
) -> list[str]:
    rows = connection.execute(
        """
        select attnum, attname from pg_attribute
        where attrelid = %s::regclass and attnum = any(%s)
        """,
        (table, numbers),
    ).fetchall()
    by_number = {int(n): str(name) for n, name in rows}
    return [by_number[n] for n in numbers]


def reset_sequences(target: psycopg.Connection) -> int:
    """Point every identity/serial sequence past the rows just copied.

    Without this the target holds the whole corpus and the next insert collides
    with row one — which looks like a corrupt database rather than a forgotten
    step.
    """

    rows = target.execute(
        """
        select format('%%I.%%I', s.schemaname, s.sequencename),
               format('%%I.%%I', n.nspname, c.relname),
               a.attname
        from pg_sequences s
        join pg_class sc on sc.relname = s.sequencename
        join pg_namespace sn on sn.oid = sc.relnamespace and sn.nspname = s.schemaname
        join pg_depend d on d.objid = sc.oid and d.deptype in ('a', 'i')
        join pg_class c on c.oid = d.refobjid
        join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid and a.attnum = d.refobjsubid
        where s.schemaname = any(%s)
        """,
        (list(APPLICATION_SCHEMAS),),
    ).fetchall()

    for sequence, table, column in rows:
        target.execute(
            sql.SQL(
                "select setval({seq}, coalesce((select max({col}) from {tbl}), 0) + 1, false)"
            ).format(
                seq=sql.Literal(str(sequence)),
                col=sql.Identifier(str(column)),
                tbl=sql.SQL(str(table)),
            )
        )
    target.commit()
    return len(rows)


def target_is_empty(target: psycopg.Connection, tables: list[str]) -> bool:
    for table in tables:
        if exact_count(target, table) > 0:
            return False
    return True


CLAIMABLE_STATES = ("queued", "retry_scheduled", "running", "awaiting_upload")


def claimable_jobs(target: psycopg.Connection) -> dict[str, int]:
    """Jobs a worker will pick up the moment one points at this database.

    Reported loudly because ignoring it cost real money on 2026-09-03. The
    copied database inherited ten claimable video jobs from a partial clone.
    Their lectures' media had not been migrated, so when the production worker
    came up it claimed them: six failed on `video media object not found`, and
    one ran an entire lecture back through frame selection, OCR, visual
    analysis, embeddings and publish, adding 1,822 evidence rows and spending
    model calls on work nobody asked for.

    A copy is not finished when the rows match. It is finished when nothing is
    about to act on them.
    """

    counts: dict[str, int] = {}
    for table in ("public.ingestion_jobs", "video.ingestion_jobs"):
        exists = target.execute(
            "select to_regclass(%s) is not null", (table,)
        ).fetchone()
        if not (exists and exists[0]):
            continue
        row = target.execute(
            sql.SQL("select count(*) from {} where status = any(%s)").format(
                sql.SQL(table)
            ),
            (list(CLAIMABLE_STATES),),
        ).fetchone()
        if row and int(row[0]):
            counts[table] = int(row[0])
    return counts


def verify(
    source: psycopg.Connection, target: psycopg.Connection, tables: list[str]
) -> CopyReport:
    report = CopyReport()
    for table in tables:
        entry = TableReport(name=table)
        entry.source_rows = exact_count(source, table)
        entry.target_rows = exact_count(target, table)
        entry.source_digest = table_digest(source, table)
        entry.target_digest = table_digest(target, table)
        report.tables.append(entry)

    # Vectors get their own check: a count and a key digest are both blind to
    # an embedding column that arrived empty, which is exactly the 2026-09-02
    # failure.
    for label, query in (
        (
            "book embeddings",
            "select dimension, count(*) from public.chunk_embeddings group by 1 order by 1",
        ),
        (
            "video embeddings",
            "select embedding_kind, dimension, count(*) "
            "from video.evidence_embeddings group by 1, 2 order by 1, 2",
        ),
    ):
        left = source.execute(query).fetchall()
        right = target.execute(query).fetchall()
        if left != right:
            report.notes.append(
                f"{label} differ: source {left} target {right}"
            )
        else:
            report.notes.append(f"{label} match: {left}")

    return report


def print_report(report: CopyReport, *, verbose: bool) -> None:
    print(f"\nidentity rows: {report.identity_rows}")
    width = max((len(t.name) for t in report.tables), default=10)
    for entry in report.tables:
        if entry.ok and not verbose:
            continue
        flag = "ok  " if entry.ok else "FAIL"
        print(
            f"  [{flag}] {entry.name.ljust(width)}  "
            f"source={entry.source_rows:<8} target={entry.target_rows:<8} "
            f"digest={'match' if entry.digests_match else 'DIFFER'}"
        )
    for note in report.notes:
        print(f"  {note}")
    total_source = sum(t.source_rows for t in report.tables)
    total_target = sum(t.target_rows for t in report.tables)
    print(
        f"\n{len(report.tables) - len(report.failures)}/{len(report.tables)} tables match; "
        f"{total_source} source rows, {total_target} target rows"
    )
    if report.claimable:
        print("\n*** claimable ingestion jobs in the target ***")
        for table, count in report.claimable.items():
            print(f"    {table}: {count}")
        print(
            "    A worker pointed at this database will claim these. If their\n"
            "    media did not migrate they will fail or re-derive at model cost.\n"
            "    Park them before deploying a worker."
        )


def resolve(url: str | None, env: str | None, label: str) -> str:
    if url:
        return url
    if env:
        value = os.getenv(env, "").strip()
        if not value:
            raise BootstrapError(f"{env} is not set")
        return value
    raise BootstrapError(f"no {label} given: pass --{label} or --{label}-env NAME")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source")
    parser.add_argument("--source-env")
    parser.add_argument("--target")
    parser.add_argument("--target-env")
    parser.add_argument("--batch-rows", type=int, default=DEFAULT_BATCH_ROWS)
    parser.add_argument("--dry-run", action="store_true", help="report what would move")
    parser.add_argument("--verify-only", action="store_true", help="compare, copy nothing")
    parser.add_argument(
        "--replace-nonempty",
        action="store_true",
        help="permit copying into a target that already holds rows",
    )
    parser.add_argument("--verbose", action="store_true", help="list matching tables too")
    args = parser.parse_args(argv)

    try:
        source_url = resolve(args.source, args.source_env, "source")
        target_url = resolve(args.target, args.target_env, "target")
        refuse_same_endpoint(source_url, target_url)
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"source host class: {host_class(source_url)}")
    print(f"target host class: {host_class(target_url)}")

    try:
        with psycopg.connect(source_url, autocommit=True) as source, psycopg.connect(
            target_url, autocommit=False
        ) as target:
            source.execute("set transaction read only")

            source_major = _major_version(source)
            target_major = _major_version(target)
            # A newer target is fine and is the actual situation: Railway's
            # managed Postgres is 18 and the recovered source is 17. An older
            # target is not — the schema is built from migrations that assume
            # at least 17, and moving rows backwards across a major version
            # has no supported path.
            if target_major < source_major:
                raise BootstrapError(
                    f"target PostgreSQL {target_major} is older than source "
                    f"{source_major}; rows cannot move backwards across a major version"
                )
            if target_major != source_major:
                print(
                    f"note: source is PostgreSQL {source_major} and target is "
                    f"{target_major}; copying in text format"
                )
            print(
                f"both PostgreSQL {_server_version_text(source)} / "
                f"{_server_version_text(target)}"
            )

            head = _migration_head(target)
            expected = repository_head()
            if head != expected:
                raise BootstrapError(
                    f"target is at migration {head or '(none)'}, repository head is "
                    f"{expected}; run scripts.bootstrap_postgres first"
                )

            tables = ordered_tables(source)
            print(f"{len(tables)} application tables", flush=True)

            if args.verify_only:
                report = verify(source, target, tables)
                report.identity_rows = exact_count(target, IDENTITY_TABLE)
                report.claimable = claimable_jobs(target)
                print_report(report, verbose=args.verbose)
                return 1 if (report.failures or any("differ" in n for n in report.notes)) else 0

            if not args.dry_run and not target_is_empty(target, tables):
                if not args.replace_nonempty:
                    raise BootstrapError(
                        "target already holds application rows; re-run against an "
                        "empty target, or pass --replace-nonempty after checking "
                        "the target really is the one you mean"
                    )
                print("target is not empty and --replace-nonempty was given")

            report = CopyReport()

            if not args.dry_run:
                suspend_referential_integrity(target)
            report.identity_rows = copy_identity_rows(source, target, dry_run=args.dry_run)
            print(f"identity registry: {report.identity_rows} rows", flush=True)

            try:
                for table in tables:
                    copied = copy_table(
                        source,
                        target,
                        table,
                        batch_rows=args.batch_rows,
                        dry_run=args.dry_run,
                    )
                    print(f"  {table}: {copied}", flush=True)
            finally:
                if not args.dry_run:
                    restore_referential_integrity(target)

            if args.dry_run:
                print("\ndry run; nothing was written")
                return 0

            sequences = reset_sequences(target)
            print(f"reset {sequences} sequences")

            dangling = verify_foreign_keys(target)
            if dangling:
                for problem in dangling:
                    print(f"  FK FAIL {problem}", file=sys.stderr)
                print(
                    "\nerror: the copy left dangling references", file=sys.stderr
                )
                return 1
            print("every foreign key holds")

            target.commit()
            with psycopg.connect(target_url, autocommit=True) as analyser:
                analyser.execute("analyze")
            print("analyze complete")

            report = verify(source, target, tables)
            report.identity_rows = exact_count(target, IDENTITY_TABLE)
            report.claimable = claimable_jobs(target)
            print_report(report, verbose=args.verbose)
            if report.failures or any("differ" in n for n in report.notes):
                print("\nerror: the target does not match the source", file=sys.stderr)
                return 1
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f"error: {str(error).strip().splitlines()[0]}", file=sys.stderr)
        return 1

    print("\ncopy complete and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
