"""Give a guest account its own copy of a chosen handful of books and papers.

Nothing is re-ingested. The corpus is already parsed, chunked, embedded and
stored; a guest library is those same rows under a different `owner_id`, and
the same bytes under a different object prefix. No parsing, no model calls.

Why a copy rather than a shared library: 91 of the 92 foreign keys in this
schema include `owner_id`, so a child's owner must equal its parent's — a
guest's conversation about a book the primary owner owns cannot satisfy
`(book_id, owner_id) references books(id, owner_id)`. Sharing would mean
dropping `owner_id` from those keys, which is the property that makes
cross-user isolation structural rather than merely enforced in queries. A copy
keeps every constraint intact and needs no application change at all.

Ids are derived arithmetically rather than through mapping tables:

    bigint   id + offset          offset is above every existing id
    text     'g<slot>:' || id     chunk ids are text
    uuid     uuid_generate_v5     stable, and available from uuid-ossp

That makes every run reproducible, re-runnable, and reversible — a guest's
rows are exactly those in its own id range — without carrying a mapping
table between the twenty tables involved.

    uv run python -m scripts.clone_guest_library --list
    uv run python -m scripts.clone_guest_library --books 542,547,574,578 \
        --guest-email guest-1@study-partner.demo --dry-run
    uv run python -m scripts.clone_guest_library --books 542,547,574,578 \
        --guest-email guest-1@study-partner.demo
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
import sys
from uuid import UUID, uuid5

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from scripts.bootstrap_postgres import BootstrapError, host_class


# Every guest's derived ids live in their own block above the real corpus, so
# "which rows are this guest's" is answerable by range alone.
ID_BLOCK = 10_000_000

# Stable namespace, so re-running for the same guest derives the same uuids
# rather than orphaning the previous run's rows.
GUEST_NAMESPACE = UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Parents before children. `ingestion_jobs` and `books` reference each other,
# and neither foreign key is deferrable, so the cycle is broken on the side
# that tolerates a null: a book may have no ingestion job, while a *ready* job
# must name a book (`ingestion_jobs_check`). Books therefore land first with a
# null job reference, and are pointed at their job once it exists.
CLONE_ORDER: tuple[tuple[str, str | None], ...] = (
    ("public.books", "id"),
    ("public.ingestion_jobs", "book_id"),
    ("public.nodes", "book_id"),
    ("public.content_blocks", "book_id"),
    ("public.table_blocks", "book_id"),
    ("public.image_blocks", "book_id"),
    ("public.image_captions", "book_id"),
    ("public.chunk_builds", "source_book_id"),
    ("public.chunks", "source_book_id"),
    ("public.chunk_sources", "source_book_id"),
    ("public.chunk_embeddings", "source_book_id"),
)

# Deliberately not cloned: decks, deck_jobs, interview_sessions, conversations
# and the ingestion job's event/OCR history. A guest starts with a library and
# no activity, which is the point — and job history is not something a demo
# ever looks at.

CLONED_TABLES = {name for name, _ in CLONE_ORDER}


@dataclass
class Plan:
    guest_owner: UUID
    offset: int
    slot: int
    books: list[int]
    counts: dict[str, int] = field(default_factory=dict)


def _short(table: str) -> str:
    return table.split(".", 1)[1]


def derived_owner(email: str) -> UUID:
    """A guest's owner id, derived from its email so a rerun is idempotent."""

    return uuid5(GUEST_NAMESPACE, email.strip().lower())


def primary_key_columns(connection: psycopg.Connection, table: str) -> set[str]:
    rows = connection.execute(
        """
        select a.attname from pg_index i
        join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
        where i.indrelid = %s::regclass and i.indisprimary
        """,
        (table,),
    ).fetchall()
    return {str(r["attname"]) for r in rows}


def remapped_columns(connection: psycopg.Connection, table: str) -> set[str]:
    """Columns whose value must be derived: this table's key, and any foreign
    key pointing at a table this run also clones.

    Introspected rather than listed, so a migration that adds a column or a
    relationship does not silently leave a guest pointing at the primary
    owner's rows.
    """

    columns = set(primary_key_columns(connection, table))
    rows = connection.execute(
        """
        select con.conname,
               pn.nspname || '.' || p.relname as parent,
               con.conkey
        from pg_constraint con
        join pg_class c on c.oid = con.conrelid
        join pg_class p on p.oid = con.confrelid
        join pg_namespace pn on pn.oid = p.relnamespace
        where con.contype = 'f' and con.conrelid = %s::regclass
        """,
        (table,),
    ).fetchall()
    for row in rows:
        if row["parent"] not in CLONED_TABLES:
            continue
        numbers = list(row["conkey"])
        names = connection.execute(
            """
            select attname from pg_attribute
            where attrelid = %s::regclass and attnum = any(%s)
            """,
            (table, numbers),
        ).fetchall()
        columns.update(str(n["attname"]) for n in names)
    # owner_id appears in almost every composite key; it has its own rule.
    columns.discard("owner_id")
    return columns


def column_types(connection: psycopg.Connection, table: str) -> dict[str, str]:
    """Insertable columns only.

    Generated columns are excluded: Postgres computes them and refuses an
    explicit value, so `nodes.direct_char_count` fails the insert outright
    rather than being quietly ignored.
    """

    rows = connection.execute(
        """
        select a.attname, format_type(a.atttypid, null) as kind
        from pg_attribute a
        where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped
          and a.attgenerated = ''
        order by a.attnum
        """,
        (table,),
    ).fetchall()
    return {str(r["attname"]): str(r["kind"]) for r in rows}


def derive(column: str, kind: str, plan: Plan) -> sql.Composable:
    """The expression that turns a source value into this guest's value."""

    name = sql.Identifier(column)
    if kind in {"bigint", "integer"}:
        return sql.SQL("({} + {})").format(name, sql.Literal(plan.offset))
    if kind == "uuid":
        return sql.SQL(
            "(case when {col} is null then null else"
            " extensions.uuid_generate_v5({ns}::uuid, {col}::text) end)"
        ).format(col=name, ns=sql.Literal(str(plan.guest_owner)))
    if kind.startswith("text") or kind.startswith("character"):
        # A chunk id is a sha256 and `chunks_id_check` enforces
        # `^[0-9a-f]{64}$`, so a prefix is not a legal id. Re-hashing the
        # original with the guest's owner keeps the shape, stays deterministic,
        # and cannot collide with the source's ids.
        #
        # The value is no longer the hash of the chunk's own content. Nothing
        # recomputes it — chunks are built once at ingestion and this clone
        # skips ingestion entirely — but it is the reason a guest library is a
        # demo copy rather than a second canonical corpus.
        return sql.SQL(
            "(case when {col} is null then null else"
            " encode(extensions.digest({salt} || {col}, 'sha256'), 'hex') end)"
        ).format(col=name, salt=sql.Literal(f"{plan.guest_owner}:"))
    raise BootstrapError(f"cannot derive a guest value for {column} of type {kind}")


def rewrite_object_key(column: str, plan: Plan, source_owner: UUID) -> sql.Composable:
    """Point a stored-object column at the guest's own prefix.

    Figure keys are content-addressed — `{owner}/canonical/book-images/sha256/…`
    — so only the owner segment changes and the hash stays, which is what lets
    the copy be a byte-for-byte server-side copy.

    Source PDFs are `{owner}/{job_id}/original.pdf`, and the job id changes
    too. That matters beyond tidiness: the retention sweep reconstructs that
    exact shape from bucket listings and matches it against job rows, so a key
    whose middle segment named a job that does not exist would be swept as an
    orphan.
    """

    name = sql.Identifier(column)
    return sql.SQL(
        "(case when {col} is null then null else"
        " replace("
        "   replace({col}, {source_owner}, {guest_owner}),"
        "   {source_owner}, {guest_owner}"
        " ) end)"
    ).format(
        col=name,
        source_owner=sql.Literal(str(source_owner)),
        guest_owner=sql.Literal(str(plan.guest_owner)),
    )


OBJECT_KEY_COLUMNS = {"storage_key", "storage_path", "source_storage_path", "viewer_storage_path"}


def clone_table(
    connection: psycopg.Connection,
    table: str,
    book_column: str | None,
    *,
    plan: Plan,
    source_owner: UUID,
    dry_run: bool,
) -> int:
    types = column_types(connection, table)
    remapped = remapped_columns(connection, table)

    targets: list[sql.Composable] = []
    values: list[sql.Composable] = []
    for column, kind in types.items():
        targets.append(sql.Identifier(column))
        if column == "owner_id":
            values.append(sql.Literal(str(plan.guest_owner)))
        elif table == "public.books" and column == "ingestion_job_id":
            # The cycle: the job does not exist yet. Filled in afterwards.
            values.append(sql.SQL("null"))
        elif table == "public.books" and column == "cards_automation_eligible_at":
            # Cloning a *ready* document with automation still armed makes the
            # worker start generating flashcard sets on its own — one job per
            # chapter for a book, each a model call. The first guest built here
            # generated a deck nobody asked for within four minutes.
            #
            # A guest starts with automation off. Generating a deck on demand
            # is a better demo anyway, because the visitor watches it happen.
            values.append(sql.SQL("null"))
        elif column in remapped:
            values.append(derive(column, kind, plan))
        elif column in OBJECT_KEY_COLUMNS:
            values.append(rewrite_object_key(column, plan, source_owner))
        else:
            values.append(sql.Identifier(column))

    where = (
        sql.SQL("{} = any(%(books)s)").format(sql.Identifier(book_column))
        if book_column
        else sql.SQL("true")
    )
    statement = sql.SQL(
        "insert into {table} ({targets}) select {values} from {table}"
        " where owner_id = %(owner)s and {where}"
    ).format(
        table=sql.SQL(table),
        targets=sql.SQL(", ").join(targets),
        values=sql.SQL(", ").join(values),
        where=where,
    )

    parameters = {"owner": str(source_owner), "books": plan.books}
    if dry_run:
        counted = connection.execute(
            sql.SQL("select count(*) as n from {table} where owner_id = %(owner)s and {where}").format(
                table=sql.SQL(table), where=where
            ),
            parameters,
        ).fetchone()
        return int(counted["n"])

    result = connection.execute(statement, parameters)
    return result.rowcount


def next_offset(connection: psycopg.Connection, slot: int) -> int:
    """A block of ids above everything that exists, one block per guest slot."""

    highest = 0
    for table, _ in CLONE_ORDER:
        types = column_types(connection, table)
        for column, kind in types.items():
            if kind not in {"bigint", "integer"}:
                continue
            if column not in primary_key_columns(connection, table):
                continue
            row = connection.execute(
                sql.SQL("select coalesce(max({}), 0) as m from {}").format(
                    sql.Identifier(column), sql.SQL(table)
                )
            ).fetchone()
            highest = max(highest, int(row["m"]))
    base = ((highest // ID_BLOCK) + 1) * ID_BLOCK
    return base + (slot - 1) * ID_BLOCK


def resolve_owner(connection: psycopg.Connection) -> UUID:
    row = connection.execute(
        "select owner_id from public.books group by owner_id order by count(*) desc limit 1"
    ).fetchone()
    if row is None:
        raise BootstrapError("no books to clone from")
    return row["owner_id"]


def ensure_guest_identity(
    connection: psycopg.Connection, plan: Plan, email: str, *, dry_run: bool
) -> None:
    if dry_run:
        return
    connection.execute(
        """
        insert into auth.users (id, email) values (%s, %s)
        on conflict (id) do update set email = excluded.email
        """,
        (plan.guest_owner, email),
    )


def existing_rows(connection: psycopg.Connection, plan: Plan) -> int:
    row = connection.execute(
        "select count(*) as n from public.books where owner_id = %s", (plan.guest_owner,)
    ).fetchone()
    return int(row["n"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--books", help="comma-separated book ids to include")
    parser.add_argument("--guest-email", help="the guest's login address")
    parser.add_argument("--slot", type=int, default=1, help="which guest id block to use")
    parser.add_argument("--list", action="store_true", help="list candidate documents")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true", help="delete this guest's rows first")
    args = parser.parse_args(argv)

    url = os.getenv(args.database_url_env, "").strip()
    if not url:
        print(f"error: {args.database_url_env} is not set", file=sys.stderr)
        return 2
    print(f"database host class: {host_class(url)}")

    try:
        with psycopg.connect(url, autocommit=False, row_factory=dict_row) as connection:
            source_owner = resolve_owner(connection)

            if args.list:
                rows = connection.execute(
                    """
                    select id, document_type, left(title, 60) as title, page_count
                    from public.books where owner_id = %s and status = 'ready'
                    order by document_type, id
                    """,
                    (source_owner,),
                ).fetchall()
                for row in rows:
                    print(
                        f"  {row['id']:>4}  {row['document_type']:<6} "
                        f"{str(row['page_count'] or '?'):>4}pp  {row['title']}"
                    )
                return 0

            if not args.books or not args.guest_email:
                print("error: --books and --guest-email are required", file=sys.stderr)
                return 2

            books = [int(v) for v in args.books.split(",") if v.strip()]
            plan = Plan(
                guest_owner=derived_owner(args.guest_email),
                offset=next_offset(connection, args.slot),
                slot=args.slot,
                books=books,
            )
            print(f"guest owner  {plan.guest_owner}")
            print(f"id block     {plan.offset}..{plan.offset + ID_BLOCK}")
            print(f"documents    {books}")

            found = connection.execute(
                "select count(*) as n from public.books where owner_id = %s and id = any(%s)",
                (source_owner, books),
            ).fetchone()
            if int(found["n"]) != len(books):
                raise BootstrapError(
                    f"{int(found['n'])} of {len(books)} requested documents belong to the "
                    "primary owner"
                )

            already = existing_rows(connection, plan)
            if already and not args.replace and not args.dry_run:
                raise BootstrapError(
                    f"this guest already has {already} books; pass --replace to rebuild it"
                )
            if already and args.replace and not args.dry_run:
                # Cascades do the rest: every child names the book.
                # Break the cycle before deleting, or neither side can go.
                connection.execute(
                    "update public.books set ingestion_job_id = null where owner_id = %s",
                    (plan.guest_owner,),
                )
                connection.execute(
                    "delete from public.ingestion_jobs where owner_id = %s", (plan.guest_owner,)
                )
                removed = connection.execute(
                    "delete from public.books where owner_id = %s", (plan.guest_owner,)
                ).rowcount
                print(f"removed {removed} existing guest books")

            ensure_guest_identity(connection, plan, args.guest_email, dry_run=args.dry_run)

            for table, book_column in CLONE_ORDER:
                moved = clone_table(
                    connection,
                    table,
                    book_column,
                    plan=plan,
                    source_owner=source_owner,
                    dry_run=args.dry_run,
                )
                plan.counts[table] = moved
                print(f"  {_short(table):<20} {moved}")

            if not args.dry_run:
                # Close the cycle now that both sides exist. The join is on
                # the job's own book_id, which was derived arithmetically and
                # so already points at the guest's book.
                linked = connection.execute(
                    """
                    update public.books b
                       set ingestion_job_id = j.id
                      from public.ingestion_jobs j
                     where b.owner_id = %(guest)s
                       and j.owner_id = %(guest)s
                       and j.book_id = b.id
                    """,
                    {"guest": str(plan.guest_owner)},
                ).rowcount
                print(f"  linked {linked} books to their ingestion jobs")
                connection.commit()

            if args.dry_run:
                connection.rollback()
                print("\ndry run; nothing was written")
                return 0

    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f"error: {str(error).strip().splitlines()[0]}", file=sys.stderr)
        return 1

    print(f"\ncloned {sum(plan.counts.values())} rows for {args.guest_email}")
    print("next: copy the guest's objects with scripts/clone_guest_objects.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
