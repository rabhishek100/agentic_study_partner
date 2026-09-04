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
BOOK_ORDER: tuple[tuple[str, str], ...] = (
    ("public.books", "id = any(%(books)s)"),
    ("public.ingestion_jobs", "book_id = any(%(books)s)"),
    ("public.nodes", "book_id = any(%(books)s)"),
    ("public.content_blocks", "book_id = any(%(books)s)"),
    ("public.table_blocks", "book_id = any(%(books)s)"),
    ("public.image_blocks", "book_id = any(%(books)s)"),
    ("public.image_captions", "book_id = any(%(books)s)"),
    ("public.chunk_builds", "source_book_id = any(%(books)s)"),
    ("public.chunks", "source_book_id = any(%(books)s)"),
    ("public.chunk_sources", "source_book_id = any(%(books)s)"),
    ("public.chunk_embeddings", "source_book_id = any(%(books)s)"),
)

# Video is a deeper graph than books, but almost every table carries
# `video_id` directly, so the filters stay as simple as the book ones. The two
# that do not are the resource a lecture cites — reached through
# `video_resources` — and its rendered pages, which 270 of this lecture's
# evidence units point at. Leaving them out would clone evidence whose
# citations dangle.
#
# `videos.current_ingestion_version_id` references a row inserted later, which
# would normally need the cycle broken by hand. It does not here: that key is
# DEFERRABLE INITIALLY DEFERRED, and this whole clone is one transaction.
_RESOURCES_OF = (
    "id in (select resource_id from video.video_resources"
    " where video_id = any(%(videos)s))"
)
VIDEO_ORDER: tuple[tuple[str, str], ...] = (
    ("video.videos", "id = any(%(videos)s)"),
    ("video.video_sources", "video_id = any(%(videos)s)"),
    ("video.ingestion_versions", "video_id = any(%(videos)s)"),
    ("video.ingestion_jobs", "video_id = any(%(videos)s)"),
    ("video.ingestion_stage_checkpoints", "video_id = any(%(videos)s)"),
    ("video.transcript_sources", "video_id = any(%(videos)s)"),
    ("video.transcript_segments", "video_id = any(%(videos)s)"),
    ("video.chapters", "video_id = any(%(videos)s)"),
    ("video.caption_uploads", "video_id = any(%(videos)s)"),
    ("video.frames", "video_id = any(%(videos)s)"),
    ("video.visual_observations", "video_id = any(%(videos)s)"),
    ("video.visual_regions", "video_id = any(%(videos)s)"),
    ("video.visual_events", "video_id = any(%(videos)s)"),
    ("video.resources", _RESOURCES_OF),
    ("video.resource_pages", _RESOURCES_OF.replace("id in", "resource_id in")),
    ("video.video_resources", "video_id = any(%(videos)s)"),
    ("video.evidence_units", "video_id = any(%(videos)s)"),
    ("video.evidence_embeddings", "video_id = any(%(videos)s)"),
)

CLONE_ORDER: tuple[tuple[str, str], ...] = BOOK_ORDER + VIDEO_ORDER

# Deliberately not cloned: decks, deck_jobs, interview_sessions, conversations
# and the ingestion job's event/OCR history. A guest starts with a library and
# no activity, which is the point — and job history is not something a demo
# ever looks at.

CLONED_TABLES = {name for name, _ in CLONE_ORDER}

# States a worker will pick up. A cloned library must contain none of them.
CLAIMABLE_STATES = ("awaiting_upload", "queued", "running", "retry_scheduled")

# How a video is inserted before its ingestion versions exist; see the comment
# in `clone_table`. Promoted to the source's real values afterwards.
_VIDEO_STAGED_COLUMNS = {
    "current_ingestion_version_id": sql.SQL("null"),
    "readiness_status": sql.Literal("processing"),
    "ready_at": sql.SQL("null"),
}


@dataclass
class Plan:
    guest_owner: UUID
    offset: int
    slot: int
    books: list[int]
    videos: list[UUID] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _short(table: str) -> str:
    return table.split(".", 1)[1]


def derived_owner(email: str) -> UUID:
    """A guest's owner id, derived from its email so a rerun is idempotent.

    Derivation is by address, so renaming a guest moves what this function
    would return. The identity itself does not move — the owner id is what
    holds the rows — so a renamed guest must be addressed by `--guest-owner`.
    """

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


def remapped_columns(
    connection: psycopg.Connection,
    table: str,
    _visiting: frozenset[str] = frozenset(),
) -> set[str]:
    """Columns whose value must be derived for a different owner.

    A column is derived when it carries identity: this table's own key, or a
    column that references an identity column of a table this run also clones.

    That second half is recursive, and has to be. `ingestion_stage_checkpoints`
    references `ingestion_versions(id, video_id, owner_id)`; `video_id` is not
    part of that parent's primary key, so a rule that only looked at primary
    keys left it pointing at the source owner's video and the insert was
    refused. It is derived in the parent, so it must be derived here.

    Introspected rather than listed, so a migration that adds a relationship
    does not silently leave a guest pointing at the primary owner's rows.

    `_visiting` breaks the cycles the video graph really has — videos and
    ingestion_versions reference each other — by falling back to the primary
    key for a table already being resolved.
    """

    if table in _visiting:
        return {c for c in primary_key_columns(connection, table) if c != "owner_id"}
    _visiting = _visiting | {table}

    columns = set(primary_key_columns(connection, table))
    rows = connection.execute(
        """
        select pn.nspname || '.' || p.relname as parent,
               con.conkey, con.confkey
        from pg_constraint con
        join pg_class c on c.oid = con.conrelid
        join pg_class p on p.oid = con.confrelid
        join pg_namespace pn on pn.oid = p.relnamespace
        where con.contype = 'f' and con.conrelid = %s::regclass
        """,
        (table,),
    ).fetchall()
    for row in rows:
        parent = str(row["parent"])
        if parent not in CLONED_TABLES:
            continue
        parent_keys = remapped_columns(connection, parent, _visiting)
        child_names = _attribute_names(connection, table, list(row["conkey"]))
        parent_names = _attribute_names(connection, parent, list(row["confkey"]))
        # Only the columns that carry the parent's *identity*. A composite
        # foreign key can also carry plain values — `video_sources` references
        # `videos(id, owner_id, source_kind)` — and deriving one of those
        # rewrites real data. `source_kind` became a sha256 and the row was
        # rejected by its own check constraint, which is the lucky version of
        # that mistake.
        for child, referenced in zip(child_names, parent_names):
            if referenced in parent_keys:
                columns.add(child)
    # owner_id appears in almost every composite key; it has its own rule.
    columns.discard("owner_id")
    return columns


def _attribute_names(
    connection: psycopg.Connection, table: str, numbers: list[int]
) -> list[str]:
    """Column names for attribute numbers, in the order given."""

    rows = connection.execute(
        """
        select attnum, attname from pg_attribute
        where attrelid = %s::regclass and attnum = any(%s)
        """,
        (table, numbers),
    ).fetchall()
    by_number = {int(r["attnum"]): str(r["attname"]) for r in rows}
    return [by_number[n] for n in numbers]


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


def is_object_key(column: str) -> bool:
    """Whether a column holds an owner-prefixed object key.

    Matched by convention rather than listed, because the video tables spell
    it five different ways — full_, preview_, crop_, staging_, render_ — and
    the schema *requires* the prefix to match the row's owner:
    `frames_check` is `full_storage_key ~~ (owner_id || '/%')`. A missed
    column is not a subtle bug; the insert is refused.
    """

    return column.endswith("storage_key") or column.endswith("storage_path")


def clone_table(
    connection: psycopg.Connection,
    table: str,
    predicate: str,
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
        elif table == "video.videos" and column in _VIDEO_STAGED_COLUMNS:
            # A video and its ingestion version each require the other, and
            # neither escape hatch works alone. The foreign key is deferrable,
            # but a trigger also guards it and triggers do not defer; the
            # version's own key back to the video is *not* deferrable, so the
            # version cannot go first either.
            #
            # So the video lands mid-ingestion and is promoted once its
            # versions exist. Three columns have to move together, because two
            # check constraints tie them: readiness in (ready, degraded) must
            # agree with both `ready_at` being set and a current version being
            # named. `processing` with neither is the one consistent state a
            # video can be inserted in before its versions exist.
            values.append(_VIDEO_STAGED_COLUMNS[column])
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
        elif is_object_key(column):
            values.append(rewrite_object_key(column, plan, source_owner))
        elif kind == "jsonb":
            # Object keys hide in JSON too. `videos.playback_json` carries the
            # one the stream endpoint reads — `playback_json->>'storage_key'` —
            # so a guest whose column still named the source owner's key got a
            # 404 from the player while every other page worked.
            #
            # Rewritten by substitution on the document text, which is blunt
            # but exact for what these blobs hold: an owner id in one of these
            # is always part of a key or a reference to the owner, and both
            # must move.
            values.append(
                sql.SQL(
                    "(case when {col} is null then null else"
                    " replace({col}::text, {src}, {dst})::jsonb end)"
                ).format(
                    col=sql.Identifier(column),
                    src=sql.Literal(str(source_owner)),
                    dst=sql.Literal(str(plan.guest_owner)),
                )
            )
        else:
            values.append(sql.Identifier(column))

    where = sql.SQL(predicate)  # a fixed fragment from CLONE_ORDER, never user input
    statement = sql.SQL(
        "insert into {table} ({targets}) select {values} from {table}"
        " where owner_id = %(owner)s and {where}"
    ).format(
        table=sql.SQL(table),
        targets=sql.SQL(", ").join(targets),
        values=sql.SQL(", ").join(values),
        where=where,
    )

    parameters = {
        "owner": str(source_owner),
        "books": plan.books,
        "videos": [str(v) for v in plan.videos],
    }
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


def already_cloned(
    connection: psycopg.Connection, plan: Plan, source_owner: UUID
) -> tuple[list[str], list[str]]:
    """What this run would clone that the guest already has.

    Asked per selection rather than "does this guest have anything", so a
    library can be built up a piece at a time — adding a lecture to a guest
    that already has its papers is the normal case, not a rebuild.

    Books are matched on `file_hash` rather than on a derived id, because the
    bigint offset is computed from the highest id at run time and therefore
    moves once a guest's own rows exist. Videos are matched on the derived
    uuid, which is stable.
    """

    books: list[str] = []
    videos: list[str] = []
    if plan.books:
        rows = connection.execute(
            """
            select g.id, g.title from public.books g
             where g.owner_id = %(guest)s
               and g.file_hash in (
                   select file_hash from public.books
                    where owner_id = %(source)s and id = any(%(books)s)
               )
            """,
            {"guest": plan.guest_owner, "source": source_owner, "books": plan.books},
        ).fetchall()
        books = [f"{r['id']} {r['title'][:40]}" for r in rows]
    if plan.videos:
        rows = connection.execute(
            """
            select id, title from video.videos
             where owner_id = %s and id = any(%s)
            """,
            (plan.guest_owner, [str(derived_uuid(plan, v)) for v in plan.videos]),
        ).fetchall()
        videos = [f"{r['id']} {str(r['title'])[:40]}" for r in rows]
    return books, videos


def derived_uuid(plan: Plan, value: UUID) -> UUID:
    """The uuid5 this run derives, matching the SQL expression in `derive`."""

    return uuid5(plan.guest_owner, str(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--books", help="comma-separated book ids to include")
    parser.add_argument("--videos", help="comma-separated video ids (uuids) to include")
    parser.add_argument("--guest-email", help="the guest's login address")
    parser.add_argument("--slot", type=int, default=1, help="which guest id block to use")
    parser.add_argument(
        "--guest-owner",
        help=(
            "use this owner id instead of deriving one from the email. Needed "
            "whenever a guest has been renamed: the derivation is by email, so "
            "a renamed guest would otherwise resolve to a fresh, empty library "
            "while its real one sits under the old address's id."
        ),
    )
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

            if not args.guest_email or not (args.books or args.videos):
                print(
                    "error: --guest-email and at least one of --books/--videos "
                    "are required",
                    file=sys.stderr,
                )
                return 2

            books = [int(v) for v in (args.books or "").split(",") if v.strip()]
            videos = [UUID(v.strip()) for v in (args.videos or "").split(",") if v.strip()]
            plan = Plan(
                guest_owner=(
                    UUID(args.guest_owner) if args.guest_owner
                    else derived_owner(args.guest_email)
                ),
                offset=next_offset(connection, args.slot),
                slot=args.slot,
                books=books,
                videos=videos,
            )
            print(f"guest owner  {plan.guest_owner}")
            print(f"id block     {plan.offset}..{plan.offset + ID_BLOCK}")
            if books:
                print(f"documents    {books}")
            if videos:
                print(f"videos       {[str(v) for v in videos]}")

            if books:
                found = connection.execute(
                    "select count(*) as n from public.books"
                    " where owner_id = %s and id = any(%s)",
                    (source_owner, books),
                ).fetchone()
                if int(found["n"]) != len(books):
                    raise BootstrapError(
                        f"{int(found['n'])} of {len(books)} requested documents "
                        "belong to the primary owner"
                    )
            if videos:
                found = connection.execute(
                    "select count(*) as n from video.videos"
                    " where owner_id = %s and id = any(%s)",
                    (source_owner, [str(v) for v in videos]),
                ).fetchone()
                if int(found["n"]) != len(videos):
                    raise BootstrapError(
                        f"{int(found['n'])} of {len(videos)} requested videos "
                        "belong to the primary owner"
                    )

            have_books, have_videos = already_cloned(connection, plan, source_owner)
            if (have_books or have_videos) and not args.replace and not args.dry_run:
                raise BootstrapError(
                    "this guest already has "
                    + ", ".join(have_books + have_videos)
                    + "; pass --replace to rebuild those"
                )
            if args.replace and not args.dry_run:
                # Only what this run would re-create. Cascades handle the
                # children; the books/ingestion_jobs cycle is broken first,
                # because neither side can go while they name each other.
                if have_videos:
                    removed = connection.execute(
                        "delete from video.videos where owner_id = %s and id = any(%s)",
                        (plan.guest_owner, [v.split()[0] for v in have_videos]),
                    ).rowcount
                    print(f"removed {removed} existing guest videos")
                if have_books:
                    ids = [int(entry.split()[0]) for entry in have_books]
                    connection.execute(
                        "update public.books set ingestion_job_id = null"
                        " where owner_id = %s and id = any(%s)",
                        (plan.guest_owner, ids),
                    )
                    connection.execute(
                        "delete from public.ingestion_jobs"
                        " where owner_id = %s and book_id = any(%s)",
                        (plan.guest_owner, ids),
                    )
                    removed = connection.execute(
                        "delete from public.books where owner_id = %s and id = any(%s)",
                        (plan.guest_owner, ids),
                    ).rowcount
                    print(f"removed {removed} existing guest books")

            ensure_guest_identity(connection, plan, args.guest_email, dry_run=args.dry_run)

            selected_order = [
                (table, predicate)
                for table, predicate in CLONE_ORDER
                if (table.startswith("public.") and plan.books)
                or (table.startswith("video.") and plan.videos)
            ]
            for table, predicate in selected_order:
                moved = clone_table(
                    connection,
                    table,
                    predicate,
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
                # Third time this class of thing has cost something: inherited
                # video jobs re-derived a lecture at model cost during the
                # Railway cutover, and a cloned book generated a flashcard deck
                # unasked. A guest's library is a fixture, not a work queue, so
                # anything a worker could claim is parked here rather than left
                # to be noticed later.
                parked = 0
                for relation in ("public.ingestion_jobs", "video.ingestion_jobs"):
                    parked += connection.execute(
                        sql.SQL(
                            "update {} set status = 'cancelled',"
                            " completed_at = coalesce(completed_at, now())"
                            " where owner_id = %(guest)s"
                            "   and status = any(%(claimable)s)"
                        ).format(sql.SQL(relation)),
                        {
                            "guest": str(plan.guest_owner),
                            "claimable": list(CLAIMABLE_STATES),
                        },
                    ).rowcount
                if parked:
                    print(f"  parked {parked} claimable ingestion jobs")

                # Promote the staged videos: readiness, ready_at and the
                # current version restored together, because the check
                # constraints require them to agree.
                relinked = connection.execute(
                    """
                    update video.videos v
                       set current_ingestion_version_id =
                               extensions.uuid_generate_v5(
                                   %(guest)s::uuid,
                                   sv.current_ingestion_version_id::text
                               ),
                           readiness_status = sv.readiness_status,
                           ready_at = sv.ready_at
                      from video.videos sv
                     where sv.owner_id = %(source)s
                       and v.owner_id = %(guest)s
                       and v.id = extensions.uuid_generate_v5(
                               %(guest)s::uuid, sv.id::text
                           )
                       and sv.current_ingestion_version_id is not null
                    """,
                    {"guest": str(plan.guest_owner), "source": str(source_owner)},
                ).rowcount
                if relinked:
                    print(f"  promoted {relinked} videos to their real readiness")

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
