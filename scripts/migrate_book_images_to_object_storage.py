"""Move book figures out of Postgres and into object storage.

`image_blocks` was 52% of this database — 456 MB of base64 JPEG for 4,526
figures — in a table whose TOAST outweighed every other table combined. This
writes each figure to the configured store, records where it went, and leaves
`base64_content` exactly as it found it.

Leaving the column is the point. Dropping it is what reclaims the space, and
it is a separate, later change, so that between the two there is no moment
when a figure exists in only one place and no way to check it arrived.

Every row is verified after writing: the object is read back and its digest
compared against the bytes that went in. A row that fails is left untouched
and reported, so a rerun retries exactly the rows that need it.

    python -m scripts.migrate_book_images_to_object_storage --dry-run
    python -m scripts.migrate_book_images_to_object_storage
    python -m scripts.migrate_book_images_to_object_storage --owner <uuid>
"""

from __future__ import annotations

import argparse
from base64 import b64decode, b64encode
import hashlib
import logging
import sys

from dotenv import load_dotenv

from storage.book_images import configured_book_image_store, digest, store_figure
from storage.database import connection, resolve_database_url
from video.media_store import MediaStoreError


logger = logging.getLogger("study_partner.scripts.migrate_book_images")

SELECT_PENDING = """
    select block_id, owner_id, book_id, mime_type, base64_content
    from image_blocks
    where storage_key is null and base64_content is not null
      and (%(owner)s::uuid is null or owner_id = %(owner)s::uuid)
    order by block_id
    limit %(batch)s
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--owner", help="Migrate one owner's figures only")
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument(
        "--limit", type=int, default=0, help="Stop after this many figures (0 = all)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would move without writing anything",
    )
    return parser


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = build_parser().parse_args()
    if arguments.batch <= 0:
        raise ValueError("--batch must be positive")

    store = configured_book_image_store()
    logger.info("store backend: %s", getattr(store, "backend", "filesystem"))

    moved = failed = skipped = 0
    total_bytes = 0
    url = arguments.database_url or resolve_database_url()

    if arguments.dry_run:
        # Counted in one query rather than paged: a dry run should describe
        # the whole job, not the first batch of it.
        with connection(url) as database:
            row = database.execute(
                """
                select count(*) as pending,
                       coalesce(sum(octet_length(decode(base64_content,'base64'))), 0)
                           as bytes
                from image_blocks
                where storage_key is null and base64_content is not null
                  and (%(owner)s::uuid is null or owner_id = %(owner)s::uuid)
                """,
                {"owner": arguments.owner},
            ).fetchone()
        logger.info(
            "would move %s figures, %.1f MB of image bytes "
            "(%.1f MB of base64 leaves the database)",
            row["pending"],
            row["bytes"] / 1e6,
            row["bytes"] * 4 / 3 / 1e6,
        )
        return 0

    while True:
        with connection(url) as database:
            rows = database.execute(
                SELECT_PENDING,
                {"owner": arguments.owner, "batch": arguments.batch},
            ).fetchall()
            if not rows:
                break

            for row in rows:
                try:
                    payload = b64decode(row["base64_content"], validate=True)
                except Exception:
                    logger.warning("figure %s is not valid base64", row["block_id"])
                    failed += 1
                    continue

                # Recorded as the captions were keyed, so existing captions
                # and boilerplate detection survive the column being dropped.
                base64_hash = hashlib.sha256(
                    b64encode(payload)
                ).hexdigest()

                if arguments.dry_run:
                    skipped += 1
                    total_bytes += len(payload)
                    continue

                try:
                    key, content_hash, size = store_figure(
                        store,
                        owner_id=row["owner_id"],
                        payload=payload,
                        mime_type=row["mime_type"],
                    )
                    # Read back before the row is allowed to name it.
                    stored = store.open_path(
                        owner_id=row["owner_id"], storage_key=key
                    ).read_bytes()
                    if digest(stored) != content_hash:
                        raise MediaStoreError("stored object does not match its source")
                except MediaStoreError as error:
                    logger.warning(
                        "figure %s could not be stored: %s", row["block_id"], error
                    )
                    failed += 1
                    continue

                database.execute(
                    """
                    update image_blocks
                    set storage_backend = %s, storage_key = %s,
                        content_hash = %s, size_bytes = %s, base64_hash = %s
                    where block_id = %s and owner_id = %s
                    """,
                    (
                        getattr(store, "backend", "filesystem"),
                        key,
                        content_hash,
                        size,
                        base64_hash,
                        row["block_id"],
                        row["owner_id"],
                    ),
                )
                moved += 1
                total_bytes += size

        done = moved + skipped + failed
        logger.info("%s figures processed (%.1f MB)", done, total_bytes / 1e6)
        if arguments.dry_run:
            break
        if arguments.limit and moved >= arguments.limit:
            break

    logger.info(
        "moved=%s failed=%s would-move=%s bytes=%.1f MB",
        moved,
        failed,
        skipped,
        total_bytes / 1e6,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
