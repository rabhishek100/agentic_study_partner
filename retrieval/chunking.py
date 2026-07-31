"""Construct citation-aware chunks from canonical Postgres rows."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID

import tiktoken

from parsing.models import NON_CONTENT_CATEGORIES
from storage.database import parse_owner_id
from .models import Chunk, ChunkSource, ChunkingConfig


# v2 makes figure captions searchable text. A figure without a caption is
# still a zero-length source, exactly as before.
CHUNKER_VERSION = "ordered-blocks-v2"
SKIPPED_CATEGORIES = NON_CONTENT_CATEGORIES
WORD_WITH_SPACE = re.compile(r"\S+\s*")
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass(frozen=True)
class _Piece:
    text: str
    source: ChunkSource
    token_count: int


def config_json(config: ChunkingConfig) -> str:
    """Return the canonical JSON representation used for provenance."""

    return json.dumps(asdict(config), ensure_ascii=False, sort_keys=True)


def config_hash(config: ChunkingConfig) -> str:
    """Hash the effective chunking configuration."""

    return sha256(config_json(config).encode("utf-8")).hexdigest()


def _trimmed_span(text: str) -> tuple[str, int, int]:
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return text[start:end], start, end


def _split_text(
    text: str,
    *,
    max_tokens: int,
    encoding,
) -> list[tuple[str, int, int, int]]:
    """Split oversized text at sentence, then word, boundaries."""

    token_count = len(encoding.encode(text))
    if token_count <= max_tokens:
        return [(text, 0, len(text), token_count)]

    sentence_ranges: list[tuple[int, int]] = []
    start = 0
    for boundary in SENTENCE_BOUNDARY.finditer(text):
        sentence_ranges.append((start, boundary.end()))
        start = boundary.end()
    if start < len(text):
        sentence_ranges.append((start, len(text)))

    pieces: list[tuple[str, int, int, int]] = []
    start = sentence_ranges[0][0]
    end = start

    for sentence_start, sentence_end in sentence_ranges:
        candidate_end = sentence_end
        candidate = text[start:candidate_end].rstrip()
        candidate_tokens = len(encoding.encode(candidate))
        if end > start and candidate_tokens > max_tokens:
            value = text[start:end].rstrip()
            pieces.append(
                (value, start, start + len(value), len(encoding.encode(value)))
            )
            start = sentence_start
        end = sentence_end

    value = text[start:end].rstrip()
    if value:
        pieces.append((value, start, start + len(value), len(encoding.encode(value))))

    final: list[tuple[str, int, int, int]] = []
    for value, start, end, piece_tokens in pieces:
        if piece_tokens <= max_tokens:
            final.append((value, start, end, piece_tokens))
            continue
        final.extend(
            _split_words(
                text,
                start=start,
                end=end,
                max_tokens=max_tokens,
                encoding=encoding,
            )
        )
    return final


def _split_words(
    text: str,
    *,
    start: int,
    end: int,
    max_tokens: int,
    encoding,
) -> list[tuple[str, int, int, int]]:
    """Split one oversized sentence without losing source offsets."""

    spans = list(WORD_WITH_SPACE.finditer(text, start, end))
    if not spans:
        raise ValueError("cannot split an oversized block without word boundaries")

    pieces: list[tuple[str, int, int, int]] = []
    piece_start = spans[0].start()
    piece_end = piece_start
    for span in spans:
        candidate = text[piece_start : span.end()].rstrip()
        candidate_tokens = len(encoding.encode(candidate))
        if piece_end > piece_start and candidate_tokens > max_tokens:
            value = text[piece_start:piece_end].rstrip()
            value_tokens = len(encoding.encode(value))
            if value_tokens > max_tokens:
                raise ValueError("a single unbroken token span exceeds max_tokens")
            pieces.append(
                (
                    value,
                    piece_start,
                    piece_start + len(value),
                    value_tokens,
                )
            )
            piece_start = span.start()
        piece_end = span.end()

    value = text[piece_start:piece_end].rstrip()
    if value:
        token_count = len(encoding.encode(value))
        if token_count > max_tokens:
            raise ValueError("a single unbroken token span exceeds max_tokens")
        pieces.append(
            (
                value,
                piece_start,
                piece_start + len(value),
                token_count,
            )
        )
    return pieces


def _read_units(
    connection: Any,
    node_id: int,
    *,
    owner_id: UUID,
    config: ChunkingConfig,
) -> list[_Piece | ChunkSource]:
    rows = connection.execute(
        """
        SELECT
            content_blocks.id,
            content_blocks.block_index,
            content_blocks.block_type,
            content_blocks.category,
            content_blocks.page_number,
            content_blocks.text_content,
            table_blocks.flat_text,
            image_captions.caption
        FROM content_blocks
        LEFT JOIN table_blocks ON table_blocks.block_id = content_blocks.id
        LEFT JOIN image_captions
               ON image_captions.block_id = content_blocks.id
              AND image_captions.owner_id = content_blocks.owner_id
        WHERE content_blocks.node_id = %s
          AND content_blocks.owner_id = %s
        ORDER BY content_blocks.block_index
        """,
        (node_id, owner_id),
    ).fetchall()
    encoding = tiktoken.get_encoding(config.encoding_name)
    units: list[_Piece | ChunkSource] = []

    for row in rows:
        block_type = row["block_type"]
        category = row["category"]
        if category in SKIPPED_CATEGORIES:
            continue
        if block_type == "image":
            # A captioned figure carries searchable text, so it can be
            # retrieved on what it shows rather than only being displayed
            # when its page happens to be cited. An uncaptioned one — or a
            # figure the captioner judged decorative — stays a zero-length
            # provenance marker, exactly as before.
            caption = (row["caption"] or "").strip()
            if not caption:
                units.append(
                    ChunkSource(
                        source_block_id=row["id"],
                        block_index=row["block_index"],
                        page_number=row["page_number"],
                        block_type=block_type,
                        category=category,
                        start_offset=0,
                        end_offset=0,
                    )
                )
                continue
            caption_text = f"Figure: {caption}"
            units.append(
                _Piece(
                    text=caption_text,
                    token_count=len(encoding.encode(caption_text)),
                    source=ChunkSource(
                        source_block_id=row["id"],
                        block_index=row["block_index"],
                        page_number=row["page_number"],
                        block_type=block_type,
                        category=category,
                        start_offset=0,
                        # The caption is derived text, not a span of the
                        # canonical block, so the offsets describe the caption
                        # itself rather than pointing into bytes that have no
                        # text to point at.
                        end_offset=len(caption_text),
                    ),
                )
            )
            continue

        raw_text = row["flat_text"] if block_type == "table" else row["text_content"]
        if raw_text is None:
            raise ValueError(f"canonical block {row['id']} has no searchable text")
        text, trim_start, trim_end = _trimmed_span(raw_text)
        if not text:
            continue

        for value, start, end, token_count in _split_text(
            text,
            max_tokens=config.max_tokens,
            encoding=encoding,
        ):
            units.append(
                _Piece(
                    text=value,
                    token_count=token_count,
                    source=ChunkSource(
                        source_block_id=row["id"],
                        block_index=row["block_index"],
                        page_number=row["page_number"],
                        block_type=block_type,
                        category=category,
                        start_offset=trim_start + start,
                        end_offset=min(trim_start + end, trim_end),
                    ),
                )
            )

    return units


def _chunk_text(pieces: list[_Piece], encoding) -> tuple[str, int]:
    text = "\n\n".join(piece.text for piece in pieces).strip()
    return text, len(encoding.encode(text))


def _overlap(pieces: list[_Piece], budget: int) -> list[_Piece]:
    if budget <= 0:
        return []

    selected: list[_Piece] = []
    used = 0
    for piece in reversed(pieces):
        if piece.source.block_type == "table":
            break
        if used + piece.token_count > budget:
            break
        selected.append(piece)
        used += piece.token_count
    return list(reversed(selected))


def _make_chunk(
    *,
    book: Any,
    node: Any,
    chunk_index: int,
    pieces: list[_Piece],
    extra_sources: list[ChunkSource],
    config: ChunkingConfig,
    encoding,
) -> Chunk:
    text, token_count = _chunk_text(pieces, encoding)
    ordered_sources = sorted(
        [*(piece.source for piece in pieces), *extra_sources],
        key=lambda source: (
            source.block_index,
            source.start_offset,
            source.end_offset,
        ),
    )
    pages = [source.page_number for source in ordered_sources]
    content_types = tuple(sorted({source.block_type for source in ordered_sources}))
    content_digest = sha256(text.encode("utf-8")).hexdigest()
    identity = "|".join(
        (
            book["file_hash"],
            str(book["id"]),
            book["parser_version"],
            CHUNKER_VERSION,
            config_hash(config),
            str(node["toc_index"]),
            str(chunk_index),
            content_digest,
        )
    )

    return Chunk(
        id=sha256(identity.encode("utf-8")).hexdigest(),
        source_book_id=book["id"],
        source_file_hash=book["file_hash"],
        source_node_id=node["id"],
        toc_index=node["toc_index"],
        chunk_index=chunk_index,
        section_title=node["title"],
        path_text=node["path_text"],
        start_page=min(pages),
        end_page=max(pages),
        text=text,
        content_types=content_types,
        char_count=len(text),
        token_count=token_count,
        content_hash=content_digest,
        sources=tuple(ordered_sources),
    )


def _build_node_chunks(
    connection: Any,
    *,
    book: Any,
    node: Any,
    owner_id: UUID,
    config: ChunkingConfig,
) -> list[Chunk]:
    encoding = tiktoken.get_encoding(config.encoding_name)
    units = _read_units(
        connection,
        node["id"],
        owner_id=owner_id,
        config=config,
    )
    chunks: list[Chunk] = []
    pieces: list[_Piece] = []
    extra_sources: list[ChunkSource] = []
    pending_assets: list[ChunkSource] = []

    def emit() -> None:
        nonlocal pieces, extra_sources
        if not pieces:
            return
        chunks.append(
            _make_chunk(
                book=book,
                node=node,
                chunk_index=len(chunks),
                pieces=pieces,
                extra_sources=extra_sources,
                config=config,
                encoding=encoding,
            )
        )
        pieces = _overlap(pieces, config.overlap_tokens)
        extra_sources = []

    for unit in units:
        if isinstance(unit, ChunkSource):
            pending_assets.append(unit)
            continue

        candidate_pieces = [*pieces, unit]
        _, candidate_tokens = _chunk_text(candidate_pieces, encoding)
        at_target = (
            bool(pieces) and _chunk_text(pieces, encoding)[1] >= config.target_tokens
        )
        table_fits = (
            unit.source.block_type == "table" and candidate_tokens <= config.max_tokens
        )
        if pieces and (
            candidate_tokens > config.max_tokens or (at_target and not table_fits)
        ):
            emit()
            candidate_pieces = [*pieces, unit]
            _, candidate_tokens = _chunk_text(candidate_pieces, encoding)
            if candidate_tokens > config.max_tokens:
                pieces = []

        if pending_assets:
            extra_sources.extend(pending_assets)
            pending_assets.clear()
        pieces.append(unit)

    if pending_assets and pieces:
        extra_sources.extend(pending_assets)
        pending_assets.clear()
    emit()
    return chunks


def build_book_chunks(
    connection: Any,
    book_id: int,
    *,
    owner_id: str | UUID,
    config: ChunkingConfig | None = None,
) -> tuple[Any, list[Chunk]]:
    """Build all searchable chunks for one canonical book."""

    config = config or ChunkingConfig()
    owner = parse_owner_id(owner_id)
    book = connection.execute(
        "select * from books where id = %s and owner_id = %s",
        (book_id, owner),
    ).fetchone()
    if book is None:
        raise KeyError(f"book {book_id} does not exist")

    excluded = {title.casefold() for title in config.excluded_titles}
    nodes = connection.execute(
        """
        select * from nodes
        where book_id = %s and owner_id = %s
        order by toc_index
        """,
        (book_id, owner),
    ).fetchall()
    chunks: list[Chunk] = []
    for node in nodes:
        if node["title"].casefold() in excluded:
            continue
        chunks.extend(
            _build_node_chunks(
                connection,
                book=book,
                node=node,
                owner_id=owner,
                config=config,
            )
        )
    return book, chunks
