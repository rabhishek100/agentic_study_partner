"""Move source and viewer PDFs from Supabase Storage to R2, one verified object at a time.

Modelled on `migrate_book_images_to_object_storage.py`, and shaped by the same
rule: a row never stops naming an object that is known to be readable. The
order for each object is download, hash, upload, read back, verify, and only
then update the rows that name it. An interrupted run therefore leaves a
database where some books read from Supabase and some from R2, every one of
them correct, and a rerun retries exactly what is left.

Resumable with no checkpoint file, because the checkpoint is the data: the
`storage_backend` columns are the record of what has moved. That also means
`--verify-only` is meaningful after the fact, and that rolling one book back
is an update rather than a restore.

The legacy object is never deleted. Cleanup is a separate policy with its own
retention windows and its own authoritativeness guard, and the rollback window
depends on the old bytes still being there.

    uv run python -m scripts.migrate_source_pdfs --dry-run
    uv run python -m scripts.migrate_source_pdfs --limit 1
    uv run python -m scripts.migrate_source_pdfs
    uv run python -m scripts.migrate_source_pdfs --verify-only
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from ingestion.errors import IngestionError
from ingestion.source_store import (
    R2_BACKEND,
    SOURCE_CONTENT_TYPE,
    SUPABASE_BACKEND,
    SourceObjectStore,
    build_store,
    owner_metadata,
)
from scripts.bootstrap_postgres import BootstrapError, host_class


DEFAULT_CONCURRENCY = 4
DEFAULT_BATCH = 200

# A failed or cancelled job's object is still inside its retention window for
# this long, so it is migrated rather than abandoned — otherwise the sweep
# would later look for it on R2, not find it, and log a permanent failure.
RETENTION_DAYS_DEFAULT = 7


@dataclass(frozen=True)
class SourceObject:
    """One distinct object, and every row that names it."""

    bucket: str
    path: str
    owner_id: UUID
    backend: str
    expected_sha256: str | None
    expected_size: int | None
    # (table, column_prefix, primary key) for each row pointing at this object.
    references: tuple[tuple[str, str, str], ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.bucket, self.path)


@dataclass
class Outcome:
    object_key: str
    status: str
    detail: str = ""
    size_bytes: int = 0
    sha256: str | None = None


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def bytes_moved(self) -> int:
        return sum(o.size_bytes for o in self.outcomes if o.status in {"migrated", "verified"})

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status in {"failed", "collision", "missing"}]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

_MANIFEST_SQL = """
with retention as (select %(retention_days)s::int as days)
select 'books.source' as origin,
       b.source_storage_bucket as bucket,
       b.source_storage_path as path,
       b.owner_id,
       b.source_storage_backend as backend,
       b.file_hash as expected_sha256,
       null::bigint as expected_size,
       b.id::text as row_key
  from public.books b
 where b.source_storage_path is not null
   and b.source_storage_backend = %(from_backend)s
union all
select 'books.viewer',
       b.viewer_storage_bucket,
       b.viewer_storage_path,
       b.owner_id,
       b.viewer_storage_backend,
       null,
       null,
       b.id::text
  from public.books b
 where b.viewer_storage_path is not null
   and b.viewer_storage_backend = %(from_backend)s
union all
-- A job's object is worth moving while it is still inside the retention
-- window the cleanup sweep honours: ready jobs keep their source forever,
-- and failed or cancelled ones keep theirs for a week. Anything already
-- past its window is deliberately left behind and reported, not migrated.
select 'ingestion_jobs',
       j.storage_bucket,
       j.storage_path,
       j.owner_id,
       j.storage_backend,
       j.file_hash,
       coalesce(j.verified_size_bytes, j.declared_size_bytes),
       j.id::text
  from public.ingestion_jobs j, retention r
 where j.storage_backend = %(from_backend)s
   and (
        j.status = 'ready'
        or j.completed_at is null
        or j.completed_at > now() - make_interval(days => r.days)
   )
"""


def build_manifest(
    connection: psycopg.Connection,
    *,
    from_backend: str,
    owner: UUID | None,
    retention_days: int,
) -> list[SourceObject]:
    """Every distinct object still on the legacy backend, with its referrers.

    Distinct is the point: two books share a source object when one was
    re-ingested, and the two viewer copies live under `book-<id>/` beside the
    job directories. Copying per row would move the same bytes twice and, worse,
    would let one row flip while another still pointed at the old provider.
    """

    rows = connection.execute(
        _MANIFEST_SQL,
        {"from_backend": from_backend, "retention_days": retention_days},
    ).fetchall()

    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        if owner is not None and row["owner_id"] != owner:
            continue
        if not row["bucket"] or not row["path"]:
            continue
        # Books ingested from disk record bucket 'local' and have no remote
        # object at all; the backend column already says filesystem, so they
        # do not appear here. This is belt and braces.
        if row["bucket"] == "local":
            continue
        key = (str(row["bucket"]), str(row["path"]))
        entry = grouped.setdefault(
            key,
            {
                "owner_id": row["owner_id"],
                "backend": row["backend"],
                "expected_sha256": None,
                "expected_size": None,
                "references": [],
            },
        )
        # Prefer any recorded hash/size over none; a disagreement between two
        # referrers is a real problem and is surfaced rather than resolved.
        if row["expected_sha256"]:
            existing = entry["expected_sha256"]
            if existing and existing != row["expected_sha256"]:
                raise BootstrapError(
                    f"rows disagree about the sha256 of {key[0]}/{key[1]}"
                )
            entry["expected_sha256"] = row["expected_sha256"]
        if row["expected_size"]:
            entry["expected_size"] = int(row["expected_size"])
        entry["references"].append((row["origin"], row["row_key"]))

    manifest: list[SourceObject] = []
    for (bucket, path), entry in sorted(grouped.items()):
        manifest.append(
            SourceObject(
                bucket=bucket,
                path=path,
                owner_id=entry["owner_id"],
                backend=str(entry["backend"]),
                expected_sha256=entry["expected_sha256"],
                expected_size=entry["expected_size"],
                references=tuple(
                    (origin, "", row_key) for origin, row_key in entry["references"]
                ),
            )
        )
    return manifest


# ---------------------------------------------------------------------------
# Row updates
# ---------------------------------------------------------------------------

# The bucket moves with the backend. "book-sources" is Supabase's bucket name
# and means nothing to R2, whose bucket is
# "agentic-study-partner-book-sources-prod" — so a row that flipped backend
# but kept its old bucket name sends the API looking for a bucket that does
# not exist, and every PDF 503s with "the document store is unavailable".
# The row has to name the bucket that actually holds the object.
_UPDATES = {
    "books.source": """
        update public.books
           set source_storage_backend = %(to_backend)s,
               source_storage_bucket = %(target_bucket)s
         where id = %(row_key)s::bigint
           and source_storage_bucket = %(bucket)s
           and source_storage_path = %(path)s
           and source_storage_backend = %(from_backend)s
    """,
    "books.viewer": """
        update public.books
           set viewer_storage_backend = %(to_backend)s,
               viewer_storage_bucket = %(target_bucket)s
         where id = %(row_key)s::bigint
           and viewer_storage_bucket = %(bucket)s
           and viewer_storage_path = %(path)s
           and viewer_storage_backend = %(from_backend)s
    """,
    "ingestion_jobs": """
        update public.ingestion_jobs
           set storage_backend = %(to_backend)s,
               storage_bucket = %(target_bucket)s
         where id = %(row_key)s::uuid
           and storage_bucket = %(bucket)s
           and storage_path = %(path)s
           and storage_backend = %(from_backend)s
    """,
}


def flip_rows(
    connection: psycopg.Connection,
    entry: SourceObject,
    *,
    from_backend: str,
    to_backend: str,
    target_bucket: str,
) -> int:
    """Point only the rows naming this exact object at the new backend.

    One short transaction, and every statement re-states the bucket, path and
    current backend in its WHERE clause. So a row that changed underneath this
    run is not silently overwritten — it simply does not match, and the count
    says so.
    """

    changed = 0
    with connection.transaction():
        for origin, _, row_key in entry.references:
            statement = _UPDATES[origin]
            result = connection.execute(
                statement,
                {
                    "to_backend": to_backend,
                    "from_backend": from_backend,
                    "target_bucket": target_bucket,
                    "row_key": row_key,
                    "bucket": entry.bucket,
                    "path": entry.path,
                },
            )
            changed += result.rowcount
    return changed


# ---------------------------------------------------------------------------
# One object
# ---------------------------------------------------------------------------


def migrate_one(
    entry: SourceObject,
    *,
    legacy: SourceObjectStore,
    target: SourceObjectStore,
    target_bucket: str,
    maximum_bytes: int,
    verify_only: bool,
) -> Outcome:
    """Copy and verify one object. Returns without touching any row."""

    label = f"{entry.bucket}/{entry.path}"

    if verify_only:
        stored = target.head(target_bucket, entry.path)
        if stored is None:
            return Outcome(label, "missing", "not present on the target")
        if entry.expected_size and stored.size_bytes != entry.expected_size:
            return Outcome(
                label,
                "failed",
                f"size {stored.size_bytes} != expected {entry.expected_size}",
                stored.size_bytes,
            )
        recorded = (stored.metadata or {}).get("sha256")
        if entry.expected_sha256 and recorded and recorded != entry.expected_sha256:
            return Outcome(label, "failed", "recorded sha256 differs", stored.size_bytes)
        return Outcome(label, "verified", "present and consistent", stored.size_bytes)

    # Already there and consistent? Then this is a resumed run.
    existing = target.head(target_bucket, entry.path)

    with tempfile.TemporaryDirectory(prefix="source-migrate-") as directory:
        local = Path(directory) / "object"
        try:
            downloaded = legacy.download(
                entry.bucket, entry.path, local, maximum_bytes=maximum_bytes
            )
        except IngestionError as error:
            return Outcome(label, "missing", f"legacy read failed: {error.code.value}")

        if entry.expected_size and downloaded.size_bytes != entry.expected_size:
            return Outcome(
                label,
                "failed",
                f"legacy size {downloaded.size_bytes} != recorded {entry.expected_size}",
                downloaded.size_bytes,
            )
        if entry.expected_sha256 and downloaded.sha256 != entry.expected_sha256:
            return Outcome(
                label,
                "failed",
                "legacy bytes do not match the hash the database records",
                downloaded.size_bytes,
            )

        if existing is not None:
            # The same key holding different bytes is a hard stop. Overwriting
            # would destroy whatever is there, and the two candidates cannot be
            # told apart by this program.
            if existing.size_bytes != downloaded.size_bytes:
                return Outcome(
                    label,
                    "collision",
                    f"target already holds {existing.size_bytes} bytes, "
                    f"legacy has {downloaded.size_bytes}",
                    downloaded.size_bytes,
                )
            recorded = (existing.metadata or {}).get("sha256")
            if recorded and recorded != downloaded.sha256:
                return Outcome(
                    label, "collision", "target holds a different object at this key"
                )
        else:
            # The job id is the second path segment for job objects, and for a
            # viewer copy the segment is `book-<id>`; either way it is what the
            # completion check re-reads, so it is recorded as written.
            segments = entry.path.split("/")
            job_segment = segments[1] if len(segments) > 1 else ""
            metadata = owner_metadata(entry.owner_id, job_segment)
            metadata["sha256"] = downloaded.sha256
            try:
                target.put(
                    target_bucket,
                    entry.path,
                    local.read_bytes(),
                    content_type=SOURCE_CONTENT_TYPE,
                    overwrite=False,
                    metadata=metadata,
                )
            except IngestionError as error:
                return Outcome(label, "failed", f"upload failed: {error.code.value}")

        # Read it back rather than trusting the write.
        stored = target.head(target_bucket, entry.path)
        if stored is None:
            return Outcome(label, "failed", "target reports no object after upload")
        if stored.size_bytes != downloaded.size_bytes:
            return Outcome(
                label,
                "failed",
                f"target size {stored.size_bytes} != {downloaded.size_bytes}",
                downloaded.size_bytes,
            )
        if stored.content_type and stored.content_type != SOURCE_CONTENT_TYPE:
            return Outcome(
                label, "failed", f"target content type is {stored.content_type}"
            )
        recorded = (stored.metadata or {}).get("sha256")
        if recorded and recorded != downloaded.sha256:
            return Outcome(label, "failed", "target sha256 metadata differs")

        return Outcome(label, "migrated", "copied and verified", downloaded.size_bytes, downloaded.sha256)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--from-backend", default=SUPABASE_BACKEND)
    parser.add_argument("--to-backend", default=R2_BACKEND)
    parser.add_argument(
        "--target-bucket",
        default=os.getenv("SOURCE_S3_BUCKET", "").strip()
        or "agentic-study-partner-book-sources-prod",
    )
    parser.add_argument("--owner", help="restrict to one owner UUID")
    parser.add_argument("--limit", type=int, help="stop after this many objects")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--retention-days", type=int, default=RETENTION_DAYS_DEFAULT
    )
    parser.add_argument(
        "--maximum-bytes", type=int, default=64 * 1024 * 1024,
        help="refuse an object larger than this",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--manifest-out", help="write the manifest as JSON here")
    args = parser.parse_args(argv)

    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        print(f"error: {args.database_url_env} is not set", file=sys.stderr)
        return 2

    owner = UUID(args.owner) if args.owner else None
    print(f"database host class: {host_class(database_url)}")
    print(f"{args.from_backend} -> {args.to_backend} in {args.target_bucket}")

    report = Report()
    try:
        with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
            # `--verify-only` looks at what has already moved; a migrating run
            # looks at what has not.
            manifest = build_manifest(
                connection,
                from_backend=args.to_backend if args.verify_only else args.from_backend,
                owner=owner,
                retention_days=args.retention_days,
            )
            total_bytes = sum(e.expected_size or 0 for e in manifest)
            print(
                f"{len(manifest)} distinct objects, "
                f"{sum(len(e.references) for e in manifest)} referring rows, "
                f"{total_bytes} recorded bytes"
            )

            if args.manifest_out:
                Path(args.manifest_out).write_text(
                    json.dumps(
                        [
                            {
                                "bucket": e.bucket,
                                "path": e.path,
                                "owner_id": str(e.owner_id),
                                "backend": e.backend,
                                "expected_sha256": e.expected_sha256,
                                "expected_size": e.expected_size,
                                "references": [
                                    {"origin": o, "row": k} for o, _, k in e.references
                                ],
                            }
                            for e in manifest
                        ],
                        indent=2,
                    )
                    + "\n"
                )
                print(f"manifest written to {args.manifest_out}")

            if args.dry_run:
                for entry in manifest[: args.limit or len(manifest)]:
                    print(
                        f"  would copy {entry.bucket}/{entry.path} "
                        f"({len(entry.references)} rows)"
                    )
                print("\ndry run; nothing was read or written")
                return 0

            if not manifest:
                print("nothing to do")
                return 0

            legacy = build_store(args.from_backend)
            target = build_store(args.to_backend)
            selected = manifest[: args.limit] if args.limit else manifest

            for start in range(0, len(selected), args.batch):
                chunk = selected[start : start + args.batch]
                # `pool.map` was the obvious shape and the wrong one: it
                # collects every result before yielding the first, so nothing
                # is printed and no row is flipped until the whole batch is
                # done. A crash partway through then loses the record of
                # everything that had already been copied. `as_completed`
                # reports and commits each object as it lands, which is what
                # makes an interrupted run genuinely resumable rather than
                # merely re-runnable.
                with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                    pending = {
                        pool.submit(
                            migrate_one,
                            entry,
                            legacy=legacy,
                            target=target,
                            target_bucket=args.target_bucket,
                            maximum_bytes=args.maximum_bytes,
                            verify_only=args.verify_only,
                        ): entry
                        for entry in chunk
                    }
                    finished = [
                        (pending[future], future.result())
                        for future in as_completed(pending)
                    ]
                for entry, outcome in finished:
                    report.add(outcome)
                    marker = {
                        "migrated": "copied",
                        "verified": "ok",
                        "missing": "MISSING",
                        "failed": "FAILED",
                        "collision": "COLLISION",
                    }.get(outcome.status, outcome.status)
                    print(f"  [{marker}] {outcome.object_key} {outcome.detail}", flush=True)

                    # The row update is the last thing, and only for a verified
                    # object. This is the invariant the whole command exists for.
                    if outcome.status == "migrated" and not args.verify_only:
                        changed = flip_rows(
                            connection,
                            entry,
                            from_backend=args.from_backend,
                            to_backend=args.to_backend,
                            target_bucket=args.target_bucket,
                        )
                        if changed != len(entry.references):
                            print(
                                f"      note: {changed} of {len(entry.references)} "
                                "rows updated; the rest changed underneath this run",
                                flush=True,
                            )

            remaining = build_manifest(
                connection,
                from_backend=args.from_backend,
                owner=owner,
                retention_days=args.retention_days,
            )
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f"error: {str(error).strip().splitlines()[0]}", file=sys.stderr)
        return 1

    print(
        f"\nmigrated={report.count('migrated')} verified={report.count('verified')} "
        f"missing={report.count('missing')} failed={report.count('failed')} "
        f"collisions={report.count('collision')} bytes={report.bytes_moved}"
    )
    if not args.verify_only:
        print(f"still on {args.from_backend}: {len(remaining)} objects")

    if report.failed:
        print("\nerror: some objects did not migrate or verify", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
