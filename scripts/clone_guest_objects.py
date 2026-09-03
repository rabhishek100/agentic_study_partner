"""Copy a guest's objects into its own prefix, server-side.

`clone_guest_library.py` rewrote the guest's rows to name keys under its own
owner prefix. Those objects do not exist yet; this creates them.

Server-side `CopyObject` rather than download-and-upload: the bytes never
leave Cloudflare, so 44 MB of figures is a few seconds of API calls rather
than a round trip through this laptop. It is also exact by construction —
there is no re-encode to get wrong.

Why copy at all, when the figures are content-addressed and identical? Because
the retention sweeps are owner-scoped: `_referenced_keys(connection, owner_id)`
builds what one owner references and deletes the rest of that owner's prefix.
A guest row pointing into the primary owner's prefix would be invisible to
that accounting, and the day the primary owner removed a book, the sweep would
reclaim an object the guest still names. The duplication is what keeps each
owner's storage self-contained.

    uv run python -m scripts.clone_guest_objects --guest-email guest-1@... --dry-run
    uv run python -m scripts.clone_guest_objects --guest-email guest-1@...
    uv run python -m scripts.clone_guest_objects --guest-email guest-1@... --verify-only
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys
from uuid import UUID

import boto3
import psycopg
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from psycopg.rows import dict_row

from scripts.bootstrap_postgres import BootstrapError, host_class
from scripts.clone_guest_library import derived_owner, resolve_owner


@dataclass(frozen=True)
class ObjectCopy:
    bucket: str
    source_key: str
    target_key: str
    expected_bytes: int | None
    label: str


def _client(prefix: str):
    """An S3 client for one bucket family, falling back to video's endpoint.

    Same inheritance the application uses: only the bucket must be restated,
    because all three live in one account behind one endpoint.
    """

    def setting(name: str, fallback: str = "") -> str:
        return (
            os.getenv(f"{prefix}_{name}", "").strip()
            or os.getenv(f"VIDEO_S3_{name}", fallback).strip()
        )

    endpoint = setting("ENDPOINT")
    if not endpoint:
        raise BootstrapError(f"{prefix}_ENDPOINT or VIDEO_S3_ENDPOINT is required")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=setting("REGION", "auto") or "auto",
        aws_access_key_id=setting("ACCESS_KEY_ID"),
        aws_secret_access_key=setting("SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def plan_copies(
    connection: psycopg.Connection, guest: UUID, source_owner: UUID
) -> list[ObjectCopy]:
    """Every object the guest's rows name, and where it comes from."""

    figures_bucket = os.getenv("BOOK_IMAGE_S3_BUCKET", "").strip()
    sources_bucket = os.getenv("SOURCE_S3_BUCKET", "").strip() or os.getenv(
        "INGESTION_SOURCE_BUCKET", ""
    ).strip()
    if not figures_bucket or not sources_bucket:
        raise BootstrapError(
            "BOOK_IMAGE_S3_BUCKET and SOURCE_S3_BUCKET must both be configured"
        )

    copies: list[ObjectCopy] = []

    figures = connection.execute(
        """
        select distinct storage_key, size_bytes
        from public.image_blocks where owner_id = %s and storage_key is not null
        """,
        (guest,),
    ).fetchall()
    for row in figures:
        key = str(row["storage_key"])
        copies.append(
            ObjectCopy(
                bucket=figures_bucket,
                source_key=key.replace(str(guest), str(source_owner), 1),
                target_key=key,
                expected_bytes=int(row["size_bytes"]) if row["size_bytes"] else None,
                label="figure",
            )
        )

    # A book's source and viewer copies are separate objects; both, when set,
    # were rewritten to the guest prefix and both need to exist.
    pdfs = connection.execute(
        """
        select source_storage_path as key from public.books
         where owner_id = %s and source_storage_path is not null
        union
        select viewer_storage_path from public.books
         where owner_id = %s and viewer_storage_path is not null
        """,
        (guest, guest),
    ).fetchall()
    for row in pdfs:
        key = str(row["key"])
        copies.append(
            ObjectCopy(
                bucket=sources_bucket,
                # The job id in the middle segment changed too, so the source
                # key cannot be recovered by swapping the owner alone. The
                # guest's own job row still records where it came from.
                source_key="",
                target_key=key,
                expected_bytes=None,
                label="pdf",
            )
        )
    return copies


def resolve_pdf_sources(
    connection: psycopg.Connection, guest: UUID, source_owner: UUID
) -> dict[str, str]:
    """Map each guest PDF key back to the primary owner's key.

    Both sides are joined through the book's `file_hash`, which the clone did
    not change and which is unique per owner. That is sturdier than trying to
    invert the id derivation in SQL.
    """

    rows = connection.execute(
        """
        select g.source_storage_path as guest_key, s.source_storage_path as source_key
          from public.books g
          join public.books s
            on s.owner_id = %(source)s and s.file_hash = g.file_hash
         where g.owner_id = %(guest)s and g.source_storage_path is not null
        """,
        {"guest": guest, "source": source_owner},
    ).fetchall()
    return {str(r["guest_key"]): str(r["source_key"]) for r in rows}


def head(client, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    except BotoCoreError:
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--guest-email", required=True)
    parser.add_argument(
        "--guest-owner",
        help="use this owner id instead of deriving one from the email; see "
             "clone_guest_library for why a renamed guest needs it",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)

    url = os.getenv(args.database_url_env, "").strip()
    if not url:
        print(f"error: {args.database_url_env} is not set", file=sys.stderr)
        return 2
    print(f"database host class: {host_class(url)}")

    guest = UUID(args.guest_owner) if args.guest_owner else derived_owner(args.guest_email)
    clients = {"figure": _client("BOOK_IMAGE_S3"), "pdf": _client("SOURCE_S3")}

    copied = verified = missing = failed = skipped = 0
    total_bytes = 0
    try:
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as connection:
            source_owner = resolve_owner(connection)
            if source_owner == guest:
                raise BootstrapError("the guest resolves to the primary owner")
            copies = plan_copies(connection, guest, source_owner)
            pdf_sources = resolve_pdf_sources(connection, guest, source_owner)

        resolved: list[ObjectCopy] = []
        for entry in copies:
            if entry.label == "pdf":
                origin = pdf_sources.get(entry.target_key)
                if not origin:
                    print(f"  [MISSING] no source for {entry.target_key}", flush=True)
                    missing += 1
                    continue
                entry = ObjectCopy(
                    bucket=entry.bucket,
                    source_key=origin,
                    target_key=entry.target_key,
                    expected_bytes=entry.expected_bytes,
                    label=entry.label,
                )
            resolved.append(entry)

        print(f"{len(resolved)} objects to place")
        if args.dry_run:
            for entry in resolved[:5]:
                print(f"  would copy {entry.label} {entry.source_key} -> {entry.target_key}")
            print("\ndry run; nothing was read or written")
            return 0

        for entry in resolved:
            client = clients[entry.label]
            existing = head(client, entry.bucket, entry.target_key)
            if existing is not None:
                size = int(existing.get("ContentLength") or 0)
                if entry.expected_bytes and size != entry.expected_bytes:
                    print(
                        f"  [FAILED] {entry.target_key} is {size}, expected "
                        f"{entry.expected_bytes}",
                        flush=True,
                    )
                    failed += 1
                    continue
                verified += 1
                total_bytes += size
                continue

            if args.verify_only:
                print(f"  [MISSING] {entry.target_key}", flush=True)
                missing += 1
                continue

            origin = head(client, entry.bucket, entry.source_key)
            if origin is None:
                print(f"  [MISSING] source gone: {entry.source_key}", flush=True)
                missing += 1
                continue
            try:
                client.copy_object(
                    Bucket=entry.bucket,
                    Key=entry.target_key,
                    CopySource={"Bucket": entry.bucket, "Key": entry.source_key},
                    MetadataDirective="COPY",
                )
            except (ClientError, BotoCoreError) as error:
                print(f"  [FAILED] {entry.target_key}: {type(error).__name__}", flush=True)
                failed += 1
                continue

            placed = head(client, entry.bucket, entry.target_key)
            if placed is None:
                print(f"  [FAILED] {entry.target_key} absent after copy", flush=True)
                failed += 1
                continue
            size = int(placed.get("ContentLength") or 0)
            if size != int(origin.get("ContentLength") or 0):
                print(f"  [FAILED] {entry.target_key} size differs after copy", flush=True)
                failed += 1
                continue
            copied += 1
            total_bytes += size

    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as error:
        print(f"error: object store failed: {type(error).__name__}", file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f"error: {str(error).strip().splitlines()[0]}", file=sys.stderr)
        return 1

    print(
        f"\ncopied={copied} already_present={verified} missing={missing} "
        f"failed={failed} skipped={skipped} bytes={total_bytes}"
    )
    if missing or failed:
        print("\nerror: the guest's library is incomplete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
