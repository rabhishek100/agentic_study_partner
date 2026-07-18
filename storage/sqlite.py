"""Store and restore ParsedBook objects using SQLite."""

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from parsing.models import ImageBlock, ParsedBook, Section, TableBlock, TextBlock


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
TABLE_MARKER = re.compile(r"^\[TABLE (\d+)]$")
IMAGE_MARKER = re.compile(r"^\[IMAGE (\d+)]$")


class BookAlreadyExistsError(RuntimeError):
    """The PDF hash is already present and replacement was not requested."""


class InvalidBookError(ValueError):
    """The ParsedBook cannot be stored without losing information."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a configured SQLite connection."""

    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Create the canonical tables and indexes if they do not exist."""

    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def _marker(block: TextBlock) -> tuple[str, int] | None:
    """Return the payload type and index represented by a placeholder."""

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
    """Check the invariants needed for a lossless relational mapping."""

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
    connection: sqlite3.Connection,
    book: ParsedBook,
    *,
    title: str,
    author: str | None,
    file_hash: str,
    page_count: int,
    parser_version: str,
    metadata: Mapping[str, Any] | None = None,
    replace: bool = False,
) -> int:
    """Atomically insert a ParsedBook and return its database ID."""

    file_hash = file_hash.casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", file_hash):
        raise ValueError("file_hash must be a hexadecimal SHA-256 digest")
    if not title.strip():
        raise ValueError("title cannot be empty")
    _validate(book, page_count)

    source_filename = book.source.replace("\\", "/").rsplit("/", 1)[-1]
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)

    if connection.in_transaction:
        raise RuntimeError(
            "ingest_book requires a connection with no active transaction"
        )

    with connection:
        existing = connection.execute(
            "SELECT id FROM books WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
        if existing and not replace:
            raise BookAlreadyExistsError(
                f"this PDF is already stored as book {existing['id']}"
            )
        if existing:
            connection.execute("DELETE FROM books WHERE id = ?", (existing["id"],))

        cursor = connection.execute(
            """
            INSERT INTO books (
                title, author, source_path, source_filename, file_hash,
                page_count, parser_version, parsed_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                author.strip() if author and author.strip() else None,
                book.source,
                source_filename,
                file_hash,
                page_count,
                parser_version,
                datetime.now(timezone.utc).isoformat(),
                metadata_json,
            ),
        )
        book_id = int(cursor.lastrowid)
        parent_by_level: dict[int, int] = {}

        for toc_index, section in enumerate(book.sections):
            parent_id = parent_by_level.get(section.level - 1)
            cursor = connection.execute(
                """
                INSERT INTO nodes (
                    book_id, parent_id, toc_index, toc_level, node_type,
                    title, path_text, path_json, start_page, end_page,
                    direct_text, direct_char_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    parent_id,
                    toc_index,
                    section.level,
                    _node_type(section),
                    section.title,
                    section.label,
                    json.dumps(section.path, ensure_ascii=False),
                    section.start_page,
                    section.end_page,
                    section.full_text,
                    len(section.full_text),
                ),
            )
            node_id = int(cursor.lastrowid)
            parent_by_level[section.level] = node_id
            parent_by_level = {
                level: parent
                for level, parent in parent_by_level.items()
                if level <= section.level
            }

            for block_index, block in enumerate(section.texts):
                marker = _marker(block)
                block_type = marker[0] if marker else "text"
                cursor = connection.execute(
                    """
                    INSERT INTO content_blocks (
                        node_id, block_index, block_type, category,
                        page_number, text_content, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, '{}')
                    """,
                    (
                        node_id,
                        block_index,
                        block_type,
                        block.category,
                        block.page,
                        block.text,
                    ),
                )
                block_id = int(cursor.lastrowid)

                if marker and marker[0] == "table":
                    table = section.tables[marker[1]]
                    connection.execute(
                        """
                        INSERT INTO table_blocks (block_id, html_content, flat_text)
                        VALUES (?, ?, ?)
                        """,
                        (block_id, table.html, table.text),
                    )
                elif marker:
                    image = section.images[marker[1]]
                    connection.execute(
                        """
                        INSERT INTO image_blocks (block_id, mime_type, base64_content)
                        VALUES (?, ?, ?)
                        """,
                        (block_id, image.mime, image.base64),
                    )

    return book_id


def restore_book(connection: sqlite3.Connection, book_id: int) -> ParsedBook:
    """Reconstruct the original ParsedBook from canonical rows."""

    book_row = connection.execute(
        "SELECT source_path FROM books WHERE id = ?",
        (book_id,),
    ).fetchone()
    if book_row is None:
        raise KeyError(f"book {book_id} does not exist")

    node_rows = connection.execute(
        "SELECT * FROM nodes WHERE book_id = ? ORDER BY toc_index",
        (book_id,),
    ).fetchall()
    if not node_rows:
        raise InvalidBookError(f"book {book_id} has no nodes")

    sections: list[Section] = []
    toc: list[tuple[int, str, int]] = []

    for node in node_rows:
        rows = connection.execute(
            """
            SELECT
                content_blocks.*,
                table_blocks.html_content,
                table_blocks.flat_text,
                image_blocks.mime_type,
                image_blocks.base64_content
            FROM content_blocks
            LEFT JOIN table_blocks ON table_blocks.block_id = content_blocks.id
            LEFT JOIN image_blocks ON image_blocks.block_id = content_blocks.id
            WHERE content_blocks.node_id = ?
            ORDER BY content_blocks.block_index
            """,
            (node["id"],),
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
            section = Section(
                path=json.loads(node["path_json"]),
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
