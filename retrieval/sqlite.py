"""Persist chunks and search them with SQLite FTS5/BM25."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from .chunking import CHUNKER_VERSION, build_book_chunks, config_hash, config_json
from .models import ChunkingConfig


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
QUERY_TOKEN = re.compile(r"\w+(?:['’]\w+)?", re.UNICODE)


@dataclass(frozen=True)
class BuildSummary:
    build_id: int
    book_id: int
    title: str
    chunk_count: int
    source_node_count: int
    config_hash: str


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    source_node_id: int
    toc_index: int
    chunk_index: int
    section_title: str
    path_text: str
    start_page: int
    end_page: int
    text: str
    content_types: tuple[str, ...]
    score: float


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a configured derived-retrieval database."""

    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def connect_source(path: str | Path) -> sqlite3.Connection:
    """Open canonical storage in read-only mode."""

    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Create derived chunk and FTS5 tables."""

    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def rebuild(
    source: sqlite3.Connection,
    destination: sqlite3.Connection,
    book_id: int,
    *,
    config: ChunkingConfig | None = None,
) -> BuildSummary:
    """Atomically replace the derived build for one source/configuration."""

    config = config or ChunkingConfig()
    book, chunks = build_book_chunks(source, book_id, config=config)
    serialized_config = config_json(config)
    configuration_hash = config_hash(config)
    source_node_count = len({chunk.source_node_id for chunk in chunks})

    if destination.in_transaction:
        raise RuntimeError("rebuild requires a destination with no active transaction")

    with destination:
        existing = destination.execute(
            """
            SELECT id
            FROM chunk_builds
            WHERE source_file_hash = ?
              AND parser_version = ?
              AND chunker_version = ?
              AND config_hash = ?
            """,
            (
                book["file_hash"],
                book["parser_version"],
                CHUNKER_VERSION,
                configuration_hash,
            ),
        ).fetchone()
        if existing:
            destination.execute(
                "DELETE FROM chunk_builds WHERE id = ?",
                (existing["id"],),
            )

        cursor = destination.execute(
            """
            INSERT INTO chunk_builds (
                source_book_id, source_file_hash, parser_version,
                chunker_version, config_json, config_hash, built_at,
                chunk_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book["id"],
                book["file_hash"],
                book["parser_version"],
                CHUNKER_VERSION,
                serialized_config,
                configuration_hash,
                datetime.now(timezone.utc).isoformat(),
                len(chunks),
            ),
        )
        build_id = int(cursor.lastrowid)

        for chunk in chunks:
            destination.execute(
                """
                INSERT INTO chunks (
                    id, build_id, source_book_id, source_node_id, toc_index,
                    chunk_index, section_title, path_text, start_page,
                    end_page, text, content_types_json, char_count,
                    token_count, content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.id,
                    build_id,
                    chunk.source_book_id,
                    chunk.source_node_id,
                    chunk.toc_index,
                    chunk.chunk_index,
                    chunk.section_title,
                    chunk.path_text,
                    chunk.start_page,
                    chunk.end_page,
                    chunk.text,
                    json.dumps(chunk.content_types),
                    chunk.char_count,
                    chunk.token_count,
                    chunk.content_hash,
                ),
            )
            destination.executemany(
                """
                INSERT INTO chunk_sources (
                    chunk_id, source_order, source_block_id, block_index,
                    page_number, block_type, category, start_offset,
                    end_offset
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.id,
                        source_order,
                        source_ref.source_block_id,
                        source_ref.block_index,
                        source_ref.page_number,
                        source_ref.block_type,
                        source_ref.category,
                        source_ref.start_offset,
                        source_ref.end_offset,
                    )
                    for source_order, source_ref in enumerate(chunk.sources)
                ],
            )

    return BuildSummary(
        build_id=build_id,
        book_id=book["id"],
        title=book["title"],
        chunk_count=len(chunks),
        source_node_count=source_node_count,
        config_hash=configuration_hash,
    )


def _fts_query(query: str) -> str:
    terms = list(dict.fromkeys(QUERY_TOKEN.findall(query.casefold())))
    if not terms:
        raise ValueError("query must contain at least one searchable term")
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def search(
    connection: sqlite3.Connection,
    query: str,
    *,
    book_id: int | None = None,
    limit: int = 5,
    unique_nodes: bool = False,
) -> list[SearchResult]:
    """Return BM25-ranked chunks, optionally collapsed to unique nodes."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    fetch_limit = 10_000 if unique_nodes else limit
    parameters: list[object] = [_fts_query(query)]
    book_filter = ""
    if book_id is not None:
        book_filter = "AND chunks.source_book_id = ?"
        parameters.append(book_id)
    parameters.append(fetch_limit)

    rows = connection.execute(
        f"""
        SELECT
            chunks.*,
            bm25(chunks_fts, 5.0, 2.0, 1.0) AS score
        FROM chunks_fts
        JOIN chunks ON chunks.rowid = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
          {book_filter}
        ORDER BY score, chunks.toc_index, chunks.chunk_index
        LIMIT ?
        """,
        parameters,
    ).fetchall()

    results: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for row in rows:
        if unique_nodes and row["source_node_id"] in seen_nodes:
            continue
        seen_nodes.add(row["source_node_id"])
        results.append(
            SearchResult(
                chunk_id=row["id"],
                source_node_id=row["source_node_id"],
                toc_index=row["toc_index"],
                chunk_index=row["chunk_index"],
                section_title=row["section_title"],
                path_text=row["path_text"],
                start_page=row["start_page"],
                end_page=row["end_page"],
                text=row["text"],
                content_types=tuple(json.loads(row["content_types_json"])),
                score=row["score"],
            )
        )
        if len(results) == limit:
            break
    return results
