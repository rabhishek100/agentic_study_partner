"""Lossless canonical ParsedBook persistence in Postgres."""

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import re
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from .database import parse_owner_id


TABLE_MARKER = re.compile(r"^\[TABLE (\d+)]$")
IMAGE_MARKER = re.compile(r"^\[IMAGE (\d+)]$")


class BookAlreadyExistsError(RuntimeError):
    """The owner already has this PDF and replacement was not requested."""


class InvalidBookError(ValueError):
    """The ParsedBook cannot be stored without losing information."""


def _marker(block: TextBlock) -> tuple[str, int] | None:
    if block.category == "TablePlaceholder":
        match = TABLE_MARKER.fullmatch(block.text)
        if match:
            return "table", int(match.group(1))
    elif block.category == "ImagePlaceholder":
        match = IMAGE_MARKER.fullmatch(block.text)
        if match:
            return "image", int(match.group(1))
    else:
        return None
    raise InvalidBookError(f"invalid payload placeholder: {block.text!r}")


def _validate(book: ParsedBook, page_count: int) -> None:
    if not book.sections or len(book.toc) != len(book.sections):
        raise InvalidBookError(
            "TOC and sections must be non-empty and have equal length"
        )
    if max(section.end_page for section in book.sections) > page_count:
        raise InvalidBookError("a section extends beyond the source PDF")

    active_path: list[str] = []
    for index, (toc_entry, section) in enumerate(
        zip(book.toc, book.sections, strict=True)
    ):
        expected = (section.level, section.title, section.start_page)
        if toc_entry != expected:
            raise InvalidBookError(f"TOC entry {index} does not match its section")
        if section.level < 1 or len(section.path) != section.level:
            raise InvalidBookError(f"section {index} has an invalid hierarchy level")
        if section.path[:-1] != active_path[: section.level - 1]:
            raise InvalidBookError(f"section {index} has inconsistent ancestors")
        if not 1 <= section.start_page <= section.end_page <= page_count:
            raise InvalidBookError(f"section {index} has an invalid page range")

        table_indexes: list[int] = []
        image_indexes: list[int] = []
        for block in section.texts:
            if not 1 <= block.page <= page_count:
                raise InvalidBookError(
                    f"section {index} contains an invalid block page"
                )
            marker = _marker(block)
            if marker is None:
                continue
            kind, payload_index = marker
            payloads = section.tables if kind == "table" else section.images
            if (
                payload_index >= len(payloads)
                or payloads[payload_index].page != block.page
            ):
                raise InvalidBookError(
                    f"section {index} has an invalid {kind} placeholder"
                )
            (table_indexes if kind == "table" else image_indexes).append(payload_index)

        if table_indexes != list(range(len(section.tables))):
            raise InvalidBookError(f"section {index} has unmatched table payloads")
        if image_indexes != list(range(len(section.images))):
            raise InvalidBookError(f"section {index} has unmatched image payloads")
        active_path[section.level - 1 :] = [section.title]


def _node_type(section: Section) -> str:
    if section.level == 1:
        title = section.title.casefold()
        if title.startswith("chapter"):
            return "chapter"
        if title.startswith("appendix"):
            return "appendix"
        return "other"
    if section.level == 2:
        return "section"
    if section.level == 3:
        return "subsection"
    return "nested_section"


def ingest_book(
    connection: Connection,
    book: ParsedBook,
    *,
    owner_id: str | UUID,
    title: str,
    author: str | None,
    file_hash: str,
    page_count: int,
    parser_version: str,
    metadata: Mapping[str, Any] | None = None,
    source_storage_bucket: str | None = None,
    source_storage_path: str | None = None,
    ingestion_job_id: str | UUID | None = None,
    ready: bool = True,
    replace: bool = False,
) -> int:
    """Atomically insert a ParsedBook for one caller-supplied owner.

    Manual imports mark the book ready immediately. The ingestion worker passes
    ``ready=False`` so the book stays invisible to study and retrieval until
    chunks, embeddings, and verification have all succeeded.
    """

    owner = parse_owner_id(owner_id)
    file_hash = file_hash.casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", file_hash):
        raise ValueError("file_hash must be a hexadecimal SHA-256 digest")
    if not title.strip():
        raise ValueError("title cannot be empty")
    if (source_storage_bucket is None) != (source_storage_path is None):
        raise ValueError("source storage bucket and path must be supplied together")
    _validate(book, page_count)

    source_filename = book.source.replace("\\", "/").rsplit("/", 1)[-1]
    job_id = UUID(str(ingestion_job_id)) if ingestion_job_id else None
    now = datetime.now(timezone.utc)
    with connection.transaction():
        existing = connection.execute(
            "select id from books where owner_id = %s and file_hash = %s",
            (owner, file_hash),
        ).fetchone()
        if existing and not replace:
            raise BookAlreadyExistsError(
                f"this PDF is already stored as book {existing['id']}"
            )
        if existing:
            connection.execute(
                "delete from books where owner_id = %s and id = %s",
                (owner, existing["id"]),
            )

        book_id = int(
            connection.execute(
                """
                insert into books (
                    owner_id, title, author, source_path, source_filename,
                    source_storage_bucket, source_storage_path, file_hash,
                    page_count, parser_version, parsed_at, metadata_json,
                    ingestion_job_id, status, ready_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                returning id
                """,
                (
                    owner,
                    title.strip(),
                    author.strip() if author and author.strip() else None,
                    book.source,
                    source_filename,
                    source_storage_bucket,
                    source_storage_path,
                    file_hash,
                    page_count,
                    parser_version,
                    now,
                    Jsonb(dict(metadata or {})),
                    job_id,
                    "ready" if ready else "processing",
                    now if ready else None,
                ),
            ).fetchone()["id"]
        )
        parent_by_level: dict[int, int] = {}

        for toc_index, section in enumerate(book.sections):
            parent_id = parent_by_level.get(section.level - 1)
            node_id = int(
                connection.execute(
                    """
                    insert into nodes (
                        owner_id, book_id, parent_id, toc_index, toc_level,
                        node_type, title, path_text, path_json, start_page,
                        end_page, direct_text
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        owner,
                        book_id,
                        parent_id,
                        toc_index,
                        section.level,
                        _node_type(section),
                        section.title,
                        section.label,
                        Jsonb(section.path),
                        section.start_page,
                        section.end_page,
                        section.full_text,
                    ),
                ).fetchone()["id"]
            )
            parent_by_level[section.level] = node_id
            parent_by_level = {
                level: parent
                for level, parent in parent_by_level.items()
                if level <= section.level
            }

            for block_index, block in enumerate(section.texts):
                marker = _marker(block)
                block_type = marker[0] if marker else "text"
                block_id = int(
                    connection.execute(
                        """
                        insert into content_blocks (
                            owner_id, book_id, node_id, block_index, block_type,
                            category, page_number, text_content, metadata_json
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
                        returning id
                        """,
                        (
                            owner,
                            book_id,
                            node_id,
                            block_index,
                            block_type,
                            block.category,
                            block.page,
                            block.text,
                        ),
                    ).fetchone()["id"]
                )
                if marker and marker[0] == "table":
                    table = section.tables[marker[1]]
                    connection.execute(
                        """
                        insert into table_blocks (
                            block_id, owner_id, book_id, html_content, flat_text
                        ) values (%s, %s, %s, %s, %s)
                        """,
                        (block_id, owner, book_id, table.html, table.text),
                    )
                elif marker:
                    image = section.images[marker[1]]
                    connection.execute(
                        """
                        insert into image_blocks (
                            block_id, owner_id, book_id, mime_type, base64_content
                        ) values (%s, %s, %s, %s, %s)
                        """,
                        (block_id, owner, book_id, image.mime, image.base64),
                    )
    return book_id


def mark_book_ready(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
) -> None:
    """Publish a verified book in one short transaction.

    Only the ingestion worker calls this, and only after every readiness check
    has passed. Until then the book exists but no study or retrieval entry
    point will select it.
    """

    owner = parse_owner_id(owner_id)
    updated = connection.execute(
        """
        update books
        set status = 'ready', ready_at = now()
        where id = %s and owner_id = %s and status <> 'ready'
        """,
        (book_id, owner),
    ).rowcount
    if not updated:
        # Either the book is already published or it does not belong to this
        # owner; both mean this call must not silently invent a ready book.
        row = connection.execute(
            "select status from books where id = %s and owner_id = %s",
            (book_id, owner),
        ).fetchone()
        if row is None:
            raise KeyError(f"book {book_id} does not exist")


def ready_book_by_hash(
    connection: Connection,
    *,
    owner_id: str | UUID,
    file_hash: str,
) -> dict[str, Any] | None:
    """Find this owner's existing ready book for a source hash.

    Duplicate detection is owner-scoped on purpose: one user must not be able
    to learn that another user uploaded the same file.
    """

    return connection.execute(
        """
        select id, title, ready_at from books
        where owner_id = %s and file_hash = %s and status = 'ready'
        """,
        (parse_owner_id(owner_id), file_hash.casefold()),
    ).fetchone()


def book_for_job(
    connection: Connection,
    *,
    owner_id: str | UUID,
    ingestion_job_id: str | UUID,
) -> dict[str, Any] | None:
    """Find the book a previous attempt of this job already committed.

    Canonical ingestion is one transaction, so a book row existing means the
    whole import succeeded. A resumed attempt reuses it instead of importing
    the same content twice.
    """

    return connection.execute(
        """
        select id, status, file_hash, parser_version, page_count
        from books
        where owner_id = %s and ingestion_job_id = %s
        """,
        (parse_owner_id(owner_id), UUID(str(ingestion_job_id))),
    ).fetchone()


def delete_book(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
) -> bool:
    """Remove a book and every row that cascades from it."""

    return bool(
        connection.execute(
            "delete from books where id = %s and owner_id = %s",
            (book_id, parse_owner_id(owner_id)),
        ).rowcount
    )


def canonical_counts(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
) -> dict[str, int]:
    """Count the canonical rows stored for one book."""

    owner = parse_owner_id(owner_id)
    return dict(
        connection.execute(
            """
            select
                (select count(*) from nodes
                 where book_id = %s and owner_id = %s) as nodes,
                (select count(*) from content_blocks
                 where book_id = %s and owner_id = %s) as blocks,
                (select count(*) from table_blocks
                 where book_id = %s and owner_id = %s) as tables,
                (select count(*) from image_blocks
                 where book_id = %s and owner_id = %s) as images
            """,
            (book_id, owner) * 4,
        ).fetchone()
    )


def list_books(
    connection: Connection,
    *,
    owner_id: str | UUID,
    ready_only: bool = True,
) -> list[dict[str, Any]]:
    """List one owner's books, newest first.

    Study and retrieval entry points use the default: a book that is still
    being processed must never be selectable.
    """

    owner = parse_owner_id(owner_id)
    predicate = "and status = 'ready'" if ready_only else ""
    return connection.execute(
        f"""
        select
            id, title, author, source_filename, page_count, status,
            ready_at, parsed_at, ingestion_job_id
        from books
        where owner_id = %s {predicate}
        order by coalesce(ready_at, parsed_at) desc, id desc
        """,
        (owner,),
    ).fetchall()


def ready_book(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    """Return one ready owner-scoped book, or None.

    None covers "does not exist", "belongs to someone else", and "not ready
    yet" on purpose: callers turn all three into the same 404 so book IDs
    cannot be enumerated.
    """

    owner = parse_owner_id(owner_id)
    return connection.execute(
        """
        select id, title, author, page_count, status, ready_at
        from books
        where id = %s and owner_id = %s and status = 'ready'
        """,
        (book_id, owner),
    ).fetchone()


def restore_book(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
) -> ParsedBook:
    """Reconstruct the original ParsedBook from owner-scoped rows."""

    owner = parse_owner_id(owner_id)
    book_row = connection.execute(
        "select source_path from books where id = %s and owner_id = %s",
        (book_id, owner),
    ).fetchone()
    if book_row is None:
        raise KeyError(f"book {book_id} does not exist")
    node_rows = connection.execute(
        """
        select * from nodes
        where book_id = %s and owner_id = %s
        order by toc_index
        """,
        (book_id, owner),
    ).fetchall()
    if not node_rows:
        raise InvalidBookError(f"book {book_id} has no nodes")

    sections: list[Section] = []
    toc: list[tuple[int, str, int]] = []
    for node in node_rows:
        rows = connection.execute(
            """
            select
                content_blocks.*,
                table_blocks.html_content,
                table_blocks.flat_text,
                image_blocks.mime_type,
                image_blocks.base64_content
            from content_blocks
            left join table_blocks
              on table_blocks.block_id = content_blocks.id
             and table_blocks.owner_id = content_blocks.owner_id
            left join image_blocks
              on image_blocks.block_id = content_blocks.id
             and image_blocks.owner_id = content_blocks.owner_id
            where content_blocks.node_id = %s
              and content_blocks.owner_id = %s
            order by content_blocks.block_index
            """,
            (node["id"], owner),
        ).fetchall()

        texts: list[TextBlock] = []
        tables: dict[int, TableBlock] = {}
        images: dict[int, ImageBlock] = {}
        for row in rows:
            block = TextBlock(
                text=row["text_content"],
                category=row["category"],
                page=row["page_number"],
            )
            texts.append(block)
            marker = _marker(block)
            if row["block_type"] == "table":
                if marker is None or marker[0] != "table" or row["flat_text"] is None:
                    raise InvalidBookError(f"table block {row['id']} is inconsistent")
                tables[marker[1]] = TableBlock(
                    html=row["html_content"],
                    text=row["flat_text"],
                    page=row["page_number"],
                )
            elif row["block_type"] == "image":
                if (
                    marker is None
                    or marker[0] != "image"
                    or row["mime_type"] is None
                    or row["base64_content"] is None
                ):
                    raise InvalidBookError(f"image block {row['id']} is inconsistent")
                images[marker[1]] = ImageBlock(
                    base64=row["base64_content"],
                    mime=row["mime_type"],
                    page=row["page_number"],
                )
            elif row["block_type"] != "text":
                raise InvalidBookError(f"content block {row['id']} is inconsistent")

        try:
            path = node["path_json"]
            if isinstance(path, str):
                path = json.loads(path)
            section = Section(
                path=path,
                level=node["toc_level"],
                start_page=node["start_page"],
                end_page=node["end_page"],
                texts=texts,
                tables=[tables[index] for index in range(len(tables))],
                images=[images[index] for index in range(len(images))],
            )
        except KeyError as error:
            raise InvalidBookError(
                f"node {node['id']} has non-contiguous payload indexes"
            ) from error
        if section.full_text != node["direct_text"]:
            raise InvalidBookError(f"node {node['id']} has inconsistent direct text")
        sections.append(section)
        toc.append((section.level, section.title, section.start_page))

    return ParsedBook(source=book_row["source_path"], toc=toc, sections=sections)
