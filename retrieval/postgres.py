"""Persist chunks and search them with Postgres full-text search."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import log
import re
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id
from .chunking import CHUNKER_VERSION, build_book_chunks, config_hash, config_json
from .models import ChunkingConfig, book_scope


QUERY_TOKEN = re.compile(r"\w+(?:['’]\w+)?", re.UNICODE)
TSVECTOR_ENTRY = re.compile(r"'((?:[^']|'')*)':([^ ]+)")


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
    source_book_id: int
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
    retrieval_method: str = "bm25"


def rebuild(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
    config: ChunkingConfig | None = None,
) -> BuildSummary:
    """Atomically replace one owner/book/configuration chunk build."""

    owner = parse_owner_id(owner_id)
    config = config or ChunkingConfig()
    book, chunks = build_book_chunks(
        connection,
        book_id,
        owner_id=owner,
        config=config,
    )
    connection.commit()
    serialized_config = config_json(config)
    configuration_hash = config_hash(config)
    source_node_count = len({chunk.source_node_id for chunk in chunks})

    with connection.transaction():
        connection.execute("select pg_advisory_xact_lock(%s)", (book_id,))
        # Every prior build for this book goes, not just one matching this
        # chunker version and config. Retrieval does not filter by build, so a
        # superseded build's chunks stay searchable alongside the new ones —
        # which is exactly what happened when the chunker was bumped to v2 for
        # figure captions: every book ended up with both, and every query could
        # match the same passage twice, once without its caption.
        connection.execute(
            """
            delete from chunk_builds
            where owner_id = %s and source_book_id = %s
            """,
            (owner, book_id),
        )

        build_id = int(
            connection.execute(
                """
                insert into chunk_builds (
                    owner_id, source_book_id, source_file_hash, parser_version,
                    chunker_version, config_json, config_hash, built_at,
                    chunk_count
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    owner,
                    book_id,
                    book["file_hash"],
                    book["parser_version"],
                    CHUNKER_VERSION,
                    Jsonb(json.loads(serialized_config)),
                    configuration_hash,
                    datetime.now(timezone.utc),
                    len(chunks),
                ),
            ).fetchone()["id"]
        )

        for chunk in chunks:
            connection.execute(
                """
                insert into chunks (
                    id, owner_id, build_id, source_book_id, source_node_id,
                    toc_index, chunk_index, section_title, path_text,
                    start_page, end_page, text, content_types, token_count,
                    content_hash
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    chunk.id,
                    owner,
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
                    list(chunk.content_types),
                    chunk.token_count,
                    chunk.content_hash,
                ),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    insert into chunk_sources (
                        chunk_id, owner_id, source_book_id, source_order,
                        source_block_id, block_index, page_number, block_type,
                        category, start_offset, end_offset
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    [
                        (
                            chunk.id,
                            owner,
                            book_id,
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
        book_id=book_id,
        title=book["title"],
        chunk_count=len(chunks),
        source_node_count=source_node_count,
        config_hash=configuration_hash,
    )


def _fts_query(query: str) -> str:
    terms = list(dict.fromkeys(QUERY_TOKEN.findall(query.casefold())))
    if not terms:
        raise ValueError("query must contain at least one searchable term")
    return " OR ".join(f'"{term}"' for term in terms)


def _term_frequencies(vector_text: str) -> tuple[dict[str, float], int]:
    """Decode Postgres positions into FTS5-compatible weighted frequencies."""

    frequencies: dict[str, float] = {}
    length = 0
    weights = {"A": 5.0, "B": 2.0, "C": 0.0, "D": 1.0}
    for match in TSVECTOR_ENTRY.finditer(vector_text):
        lexeme = match.group(1).replace("''", "'")
        frequency = 0.0
        for position in match.group(2).split(","):
            length += 1
            weight = position[-1] if position[-1].isalpha() else "D"
            frequency += weights[weight]
        frequencies[lexeme] = frequency
    return frequencies, length


def _bm25_scores(
    corpus: list[Any],
    query_terms: tuple[str, ...],
) -> dict[str, float]:
    """Calculate deterministic BM25 over Postgres-normalized lexemes."""

    documents = {}
    document_frequencies = {term: 0 for term in query_terms}
    total_length = 0
    for row in corpus:
        frequencies, length = _term_frequencies(row["search_vector_text"])
        documents[row["id"]] = (frequencies, length)
        total_length += length
        for term in query_terms:
            document_frequencies[term] += term in frequencies

    document_count = len(documents)
    average_length = total_length / document_count
    k1 = 1.2
    b = 0.75
    scores = {}
    for chunk_id, (frequencies, length) in documents.items():
        score = 0.0
        length_normalization = k1 * (1 - b + b * length / average_length)
        for term in query_terms:
            frequency = frequencies.get(term, 0.0)
            if not frequency:
                continue
            document_frequency = document_frequencies[term]
            inverse_document_frequency = max(
                log(
                    (document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                ),
                1e-6,
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1) / (frequency + length_normalization)
            )
        scores[chunk_id] = score
    return scores


def search(
    connection: Connection,
    query: str,
    *,
    owner_id: str | UUID,
    book_id: int | None = None,
    book_ids: Sequence[int] | None = None,
    limit: int = 5,
    unique_nodes: bool = False,
) -> list[SearchResult]:
    """Return weighted full-text results, optionally collapsed by node."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    owner = parse_owner_id(owner_id)
    scope = book_scope(book_id, book_ids)
    search_query = _fts_query(query)
    predicate = (
        "owner_id = %s and (%s::bigint[] is null or source_book_id = any(%s))"
    )
    corpus = connection.execute(
        f"""
        select id, search_vector::text as search_vector_text
        from chunks where {predicate}
        """,
        (owner, scope, scope),
    ).fetchall()
    if not corpus:
        return []
    term_row = connection.execute(
        "select tsvector_to_array(to_tsvector('english', %s)) as terms",
        (query,),
    ).fetchone()
    query_terms = tuple(term_row["terms"])
    if not query_terms:
        return []
    scores = _bm25_scores(corpus, query_terms)
    rows = connection.execute(
        f"""
        select * from chunks
        where {predicate}
          and search_vector @@ websearch_to_tsquery('english', %s)
        """,
        (owner, scope, scope, search_query),
    ).fetchall()
    rows.sort(
        key=lambda row: (
            -scores[row["id"]],
            row["toc_index"],
            row["chunk_index"],
        )
    )

    results: list[SearchResult] = []
    seen_nodes: set[int] = set()
    for row in rows:
        if unique_nodes and row["source_node_id"] in seen_nodes:
            continue
        seen_nodes.add(row["source_node_id"])
        results.append(
            search_result_from_row(
                row,
                score=scores[row["id"]],
                retrieval_method="bm25",
            )
        )
        if len(results) == limit:
            break
    return results


def chunks_by_id(
    connection: Connection,
    chunk_ids: list[str],
    *,
    owner_id: str | UUID,
) -> dict[str, Any]:
    if not chunk_ids:
        return {}
    owner = parse_owner_id(owner_id)
    rows = connection.execute(
        "select * from chunks where owner_id = %s and id = any(%s)",
        (owner, chunk_ids),
    ).fetchall()
    return {row["id"]: row for row in rows}


def search_result_from_row(
    row: Any,
    *,
    score: float,
    retrieval_method: str,
) -> SearchResult:
    return SearchResult(
        chunk_id=row["id"],
        source_book_id=row["source_book_id"],
        source_node_id=row["source_node_id"],
        toc_index=row["toc_index"],
        chunk_index=row["chunk_index"],
        section_title=row["section_title"],
        path_text=row["path_text"],
        start_page=row["start_page"],
        end_page=row["end_page"],
        text=row["text"],
        content_types=tuple(row["content_types"]),
        score=score,
        retrieval_method=retrieval_method,
    )
