"""Backfill canonical SQLite books into Postgres and audit losslessness."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from storage.database import connection as database_connection, resolve_owner_id
from storage.postgres import ingest_book, restore_book
from storage.sqlite import (
    connect_readonly as connect_legacy,
    restore_book as restore_legacy,
)


TABLES = ("nodes", "content_blocks", "table_blocks", "image_blocks")


def _upload_pdf(source: Path, *, owner_id: str, file_hash: str) -> tuple[str, str]:
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for upload"
        )
    bucket = "book-sources"
    object_path = f"{owner_id}/{file_hash}/{source.name}"
    response = httpx.post(
        f"{supabase_url}/storage/v1/object/{bucket}/{quote(object_path)}",
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Content-Type": "application/pdf",
            "x-upsert": "true",
        },
        content=source.read_bytes(),
        timeout=120,
    )
    response.raise_for_status()
    return bucket, object_path


def _legacy_counts(connection, book_id: int) -> dict[str, int]:
    counts = {
        "nodes": connection.execute(
            "select count(*) from nodes where book_id = ?", (book_id,)
        ).fetchone()[0],
        "content_blocks": connection.execute(
            """
            select count(*) from content_blocks
            join nodes on nodes.id = content_blocks.node_id
            where nodes.book_id = ?
            """,
            (book_id,),
        ).fetchone()[0],
        "table_blocks": connection.execute(
            """
            select count(*) from table_blocks
            join content_blocks on content_blocks.id = table_blocks.block_id
            join nodes on nodes.id = content_blocks.node_id
            where nodes.book_id = ?
            """,
            (book_id,),
        ).fetchone()[0],
        "image_blocks": connection.execute(
            """
            select count(*) from image_blocks
            join content_blocks on content_blocks.id = image_blocks.block_id
            join nodes on nodes.id = content_blocks.node_id
            where nodes.book_id = ?
            """,
            (book_id,),
        ).fetchone()[0],
    }
    counts["image_payload_chars"] = connection.execute(
        """
        select coalesce(sum(length(image_blocks.base64_content)), 0)
        from image_blocks
        join content_blocks on content_blocks.id = image_blocks.block_id
        join nodes on nodes.id = content_blocks.node_id
        where nodes.book_id = ?
        """,
        (book_id,),
    ).fetchone()[0]
    return counts


def _postgres_counts(connection, book_id: int) -> dict[str, int]:
    owner = resolve_owner_id()
    counts = {
        "nodes": connection.execute(
            "select count(*) as count from nodes where book_id = %s and owner_id = %s",
            (book_id, owner),
        ).fetchone()["count"],
        "content_blocks": connection.execute(
            "select count(*) as count from content_blocks where book_id = %s and owner_id = %s",
            (book_id, owner),
        ).fetchone()["count"],
        "table_blocks": connection.execute(
            "select count(*) as count from table_blocks where book_id = %s and owner_id = %s",
            (book_id, owner),
        ).fetchone()["count"],
        "image_blocks": connection.execute(
            "select count(*) as count from image_blocks where book_id = %s and owner_id = %s",
            (book_id, owner),
        ).fetchone()["count"],
    }
    counts["image_payload_chars"] = connection.execute(
        """
        select coalesce(sum(length(base64_content)), 0) as count
        from image_blocks where book_id = %s and owner_id = %s
        """,
        (book_id, owner),
    ).fetchone()["count"]
    return counts


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def migrate(args) -> dict:
    owner = resolve_owner_id()
    legacy = connect_legacy(args.sqlite)
    reports = []
    try:
        predicate = "where id = ?" if args.book_id is not None else ""
        params = (args.book_id,) if args.book_id is not None else ()
        books = legacy.execute(
            f"select * from books {predicate} order by id", params
        ).fetchall()
        if not books:
            raise ValueError("no SQLite books matched")

        with database_connection(args.database_url) as postgres:
            for row in books:
                parsed = restore_legacy(legacy, row["id"])
                source = args.source_pdf or Path(row["source_path"])
                if source.is_file() and _file_hash(source) != row["file_hash"]:
                    raise ValueError(f"source PDF hash mismatch for book {row['id']}")
                bucket = object_path = None
                if args.upload_source:
                    if not source.is_file():
                        raise FileNotFoundError(f"source PDF not found: {source}")
                    bucket, object_path = _upload_pdf(
                        source,
                        owner_id=str(owner),
                        file_hash=row["file_hash"],
                    )
                existing = postgres.execute(
                    """
                    select id from books
                    where owner_id = %s and file_hash = %s
                    """,
                    (owner, row["file_hash"]),
                ).fetchone()
                if existing and not args.replace:
                    destination_id = int(existing["id"])
                    if object_path:
                        postgres.execute(
                            """
                            update books
                            set source_storage_bucket = %s,
                                source_storage_path = %s
                            where id = %s and owner_id = %s
                            """,
                            (bucket, object_path, destination_id, owner),
                        )
                else:
                    destination_id = ingest_book(
                        postgres,
                        parsed,
                        owner_id=owner,
                        title=row["title"],
                        author=row["author"],
                        file_hash=row["file_hash"],
                        page_count=row["page_count"],
                        parser_version=row["parser_version"],
                        metadata=json.loads(row["metadata_json"]),
                        source_storage_bucket=bucket,
                        source_storage_path=object_path,
                        replace=args.replace,
                    )
                restored = restore_book(postgres, destination_id, owner_id=owner)
                source_counts = _legacy_counts(legacy, row["id"])
                destination_counts = _postgres_counts(postgres, destination_id)
                reports.append(
                    {
                        "source_book_id": row["id"],
                        "destination_book_id": destination_id,
                        "file_hash": row["file_hash"],
                        "parsed_book_equal": restored == parsed,
                        "source_counts": source_counts,
                        "destination_counts": destination_counts,
                        "counts_equal": source_counts == destination_counts,
                        "source_uploaded": object_path is not None,
                    }
                )
    finally:
        legacy.close()

    valid = all(
        report["parsed_book_equal"] and report["counts_equal"] for report in reports
    )
    return {"valid": valid, "owner_id": str(owner), "books": reports}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, default=Path("data/books.sqlite3"))
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL"),
        help="Hosted Postgres URL; defaults to MIGRATION_DATABASE_URL",
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--source-pdf", type=Path)
    parser.add_argument("--upload-source", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error(
            "--database-url or MIGRATION_DATABASE_URL is required; "
            "the backfill will not fall back to DATABASE_URL"
        )
    report = migrate(args)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
