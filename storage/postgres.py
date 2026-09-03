"""Lossless canonical PDF persistence in Postgres.

``ParsedBook`` remains the parser's historical name, but the canonical model
also stores papers.  A paper uses the same lossless hierarchy and content
blocks as a book while assigning paper-native structural roles to its outline.
"""

from base64 import b64decode, b64encode
from hashlib import sha256
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock
from parsing.outline_roles import (
    CHAPTER,
    NESTED_SECTION,
    SECTION,
    SUBSECTION,
    chapter_level,
    chapter_number,
    outline_roles,
)

from video.media_store import MediaStore

from .book_images import configured_book_image_store, load_figure, store_figure
from .database import parse_owner_id

TABLE_MARKER = re.compile(r"^\[TABLE (\d+)]$")
IMAGE_MARKER = re.compile(r"^\[IMAGE (\d+)]$")


def canonical_text(value: str | None) -> str | None:
    """Remove the one Unicode code point PostgreSQL text cannot represent.

    PDF text layers occasionally contain an embedded NUL as a broken glyph.
    It carries no readable content, and allowing it to abort an otherwise
    lossless canonical import makes parser fallback unusable for that source.
    """

    return None if value is None else value.replace("\x00", "")


def _postgres_text(value: str | None) -> str | None:
    """Backward-compatible binding helper for canonical text values."""

    return canonical_text(value)


def _postgres_json(value: Any) -> Any:
    """Apply the same NUL rule recursively to JSON-bound provenance."""

    if isinstance(value, str):
        return _postgres_text(value)
    if isinstance(value, Mapping):
        return {
            str(_postgres_text(str(key))): _postgres_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_postgres_json(item) for item in value]
    return value


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


def _validate_chapters(sections: list[Section], node_types: list[str]) -> None:
    """Refuse an outline whose chapter numbering contradicts itself.

    Only meaningful when a chapter level was actually detected: there the run
    is consecutive by construction, so this guards against a future change to
    the classifier rather than against the books. Two nodes both answering to
    "Chapter 5", or a jump from 3 to 7, means a chapter reference resolves to
    the wrong pages silently, which is worse than a failed ingestion.

    On the fallback path every top-level entry is a chapter regardless of its
    title, which claims nothing about numbering and must not be checked as if
    it did.
    """

    detected = chapter_level(
        [section.level for section in sections],
        [section.title for section in sections],
        [section.start_page for section in sections],
    )
    if detected is None:
        return

    numbered = [
        number
        for section, node_type in zip(sections, node_types, strict=True)
        if node_type == CHAPTER
        and (number := chapter_number(section.title)) is not None
    ]
    if not numbered:
        return
    duplicates = {n for n in numbered if numbered.count(n) > 1}
    if duplicates:
        raise InvalidBookError(f"chapter numbers are not unique: {sorted(duplicates)}")
    if sorted(numbered) != list(range(min(numbered), min(numbered) + len(numbered))):
        raise InvalidBookError(
            f"chapter numbers are not consecutive: {sorted(numbered)}"
        )


def _paper_node_types(sections: list[Section]) -> list[str]:
    """Name paper headings without inventing chapters.

    Scientific papers are normally organized as sections and subsections,
    including when their top-level headings form a numbered 1..N run.  That
    numbering is precisely the signal used to find chapters in books, so paper
    roles must be selected from the declared document type rather than guessed
    from the outline text.
    """

    return [
        SECTION
        if section.level == 1
        else SUBSECTION
        if section.level == 2
        else NESTED_SECTION
        for section in sections
    ]


def _node_types(
    sections: list[Section],
    *,
    document_type: str = "book",
) -> list[str]:
    """Name every heading's structural role for its declared document type.

    Depth alone named the roles until a book with Parts arrived: its chapters
    sit at level 2, so a depth rule typed Part I as a chapter and Chapter 5 as
    a section, and nothing typed `section` can answer to a chapter number. See
    `parsing.outline_roles` for why neither depth nor title works alone.
    """

    if document_type == "paper":
        return _paper_node_types(sections)

    return outline_roles(
        [section.level for section in sections],
        [section.title for section in sections],
        # Pages are what distinguish a book's chapter sequence from a numbered
        # list inside one of its pages. See `parsing.outline_roles`.
        [section.start_page for section in sections],
    )


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
    document_type: str = "book",
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
    if document_type not in {"book", "paper"}:
        raise ValueError("document_type must be 'book' or 'paper'")
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
                    ingestion_job_id, document_type, status, ready_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                returning id
                """,
                (
                    owner,
                    _postgres_text(title.strip()),
                    _postgres_text(author.strip())
                    if author and author.strip()
                    else None,
                    _postgres_text(book.source),
                    _postgres_text(source_filename),
                    _postgres_text(source_storage_bucket),
                    _postgres_text(source_storage_path),
                    file_hash,
                    page_count,
                    parser_version,
                    now,
                    Jsonb(_postgres_json(dict(metadata or {}))),
                    job_id,
                    document_type,
                    "ready" if ready else "processing",
                    now if ready else None,
                ),
            ).fetchone()["id"]
        )
        parent_by_level: dict[int, int] = {}
        # Blocks and their payloads are collected across every node, then
        # written in batches once the node ids are known.
        pending_blocks: list[tuple] = []
        pending_payloads: list[tuple | None] = []
        node_types = _node_types(book.sections, document_type=document_type)
        if document_type == "book":
            _validate_chapters(book.sections, node_types)

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
                        node_types[toc_index],
                        _postgres_text(section.title),
                        _postgres_text(section.label),
                        Jsonb(_postgres_json(section.path)),
                        section.start_page,
                        section.end_page,
                        _postgres_text(section.full_text),
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
                pending_blocks.append(
                    (
                        owner,
                        book_id,
                        node_id,
                        block_index,
                        marker[0] if marker else "text",
                        _postgres_text(block.category),
                        block.page,
                        _postgres_text(block.text),
                    )
                )
                pending_payloads.append(
                    None if marker is None else (marker[0], section, marker[1])
                )

        _insert_payloads(
            connection,
            owner=owner,
            book_id=book_id,
            blocks=pending_blocks,
            payloads=pending_payloads,
        )
    return book_id


# One statement per this many rows. Large enough that a book costs a handful
# of round trips instead of thousands, small enough to keep any single
# statement and its parameter list manageable.
INSERT_BATCH_SIZE = 500


def _insert_payloads(
    connection: Connection,
    *,
    owner: UUID,
    book_id: int,
    blocks: list[tuple],
    payloads: list[tuple | None],
    image_store: MediaStore | None = None,
) -> None:
    """Insert every content block and its payload in batched statements.

    Row-by-row inserts cost one network round trip each, which is invisible
    against a local database and dominant against a remote one: a 2,562-block
    book spent over five minutes here when the database was a continent away.
    Multi-row inserts return their ids in value order, which is what lets the
    payload rows be matched back to their blocks.
    """

    block_ids: list[int] = []
    for start in range(0, len(blocks), INSERT_BATCH_SIZE):
        batch = blocks[start : start + INSERT_BATCH_SIZE]
        values = ", ".join(
            ["(%s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)"] * len(batch)
        )
        rows = connection.execute(
            f"""
            insert into content_blocks (
                owner_id, book_id, node_id, block_index, block_type,
                category, page_number, text_content, metadata_json
            ) values {values}
            returning id
            """,
            [field for row in batch for field in row],
        ).fetchall()
        block_ids.extend(int(row["id"]) for row in rows)

    if len(block_ids) != len(blocks):
        raise InvalidBookError(
            f"stored {len(block_ids)} content blocks for {len(blocks)} parsed blocks"
        )

    image_store = image_store or configured_book_image_store()
    tables: list[tuple] = []
    images: list[tuple] = []
    for block_id, payload in zip(block_ids, payloads, strict=True):
        if payload is None:
            continue
        kind, section, index = payload
        if kind == "table":
            table = section.tables[index]
            tables.append(
                (
                    block_id,
                    owner,
                    book_id,
                    _postgres_text(table.html),
                    _postgres_text(table.text),
                )
            )
        else:
            image = section.images[index]
            # Figures go to object storage and the row names them. Held inline
            # as base64, they made this table 52% of the database.
            payload = b64decode(image.base64, validate=True)
            key, content_hash, size_bytes = store_figure(
                image_store, owner_id=owner, payload=payload, mime_type=image.mime
            )
            images.append(
                (
                    block_id,
                    owner,
                    book_id,
                    image.mime,
                    image_store.backend,
                    key,
                    content_hash,
                    size_bytes,
                    # Recorded now so figure captions keep the identity they
                    # were keyed on once base64_content is dropped.
                    sha256(image.base64.encode("ascii")).hexdigest(),
                )
            )

    for rows_to_insert, table_name, columns in (
        (
            tables,
            "table_blocks",
            "block_id, owner_id, book_id, html_content, flat_text",
        ),
        (
            images,
            "image_blocks",
            "block_id, owner_id, book_id, mime_type, storage_backend, "
            "storage_key, content_hash, size_bytes, base64_hash",
        ),
    ):
        for start in range(0, len(rows_to_insert), INSERT_BATCH_SIZE):
            batch = rows_to_insert[start : start + INSERT_BATCH_SIZE]
            placeholders = "(" + ", ".join(["%s"] * len(batch[0])) + ")"
            values = ", ".join([placeholders] * len(batch))
            connection.execute(
                f"insert into {table_name} ({columns}) values {values}",
                [field for row in batch for field in row],
            )


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
        set status = 'ready', ready_at = now(),
            cards_automation_eligible_at = coalesce(
                cards_automation_eligible_at, now()
            )
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


def rename_book(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
    title: str,
) -> dict[str, Any] | None:
    """Give one book or paper the name its reader chose.

    Returns the updated row, or `None` when the id belongs to nobody or to
    somebody else — the owner predicate is in the statement itself rather than
    in a check before it, so a mistake cannot rename another account's book.

    The title is the reader's, and nothing derives over it afterwards: the
    backfill only replaces names that still look machine-generated, and a name
    typed here does not.
    """

    clean = " ".join(title.split())
    if not clean:
        raise ValueError("title cannot be blank")
    return connection.execute(
        """
        update books set title = %s
        where id = %s and owner_id = %s
        returning id, title, author, document_type
        """,
        (clean, book_id, parse_owner_id(owner_id)),
    ).fetchone()


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
    document_type: str | None = None,
) -> list[dict[str, Any]]:
    """List one owner's books/papers, newest first.

    Study and retrieval entry points use the default: a book that is still
    being processed must never be selectable.
    """

    owner = parse_owner_id(owner_id)
    predicates = []
    params: list[Any] = [owner]
    if ready_only:
        predicates.append("status = 'ready'")
    if document_type:
        predicates.append("document_type = %s")
        params.append(document_type)

    where_clause = " and ".join([""] + predicates) if predicates else ""
    return connection.execute(
        f"""
        select
            id, title, author, source_filename, page_count, status,
            ready_at, parsed_at, ingestion_job_id, document_type
        from books
        where owner_id = %s {where_clause}
        order by coalesce(ready_at, parsed_at) desc, id desc
        """,
        params,
    ).fetchall()


def ready_book(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
) -> dict[str, Any] | None:
    """Return one ready owner-scoped book or paper, or None.

    None covers "does not exist", "belongs to someone else", and "not ready
    yet" on purpose: callers turn all three into the same 404 so book IDs
    cannot be enumerated.
    """

    owner = parse_owner_id(owner_id)
    return connection.execute(
        """
        select id, title, author, page_count, status, ready_at, document_type
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
                image_blocks.base64_content,
                image_blocks.storage_key,
                image_blocks.content_hash as image_content_hash
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
                    or (row["base64_content"] is None and row["storage_key"] is None)
                ):
                    raise InvalidBookError(f"image block {row['id']} is inconsistent")
                # ImageBlock is the parser's shape and carries base64, so a
                # stored figure is re-encoded here rather than kept that way
                # at rest. This path rebuilds a parsed book from canonical
                # rows and is not on any request.
                images[marker[1]] = ImageBlock(
                    base64=b64encode(load_figure(row)).decode("ascii"),
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
