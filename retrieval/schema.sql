PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS chunk_builds (
    id                  INTEGER PRIMARY KEY,
    source_book_id      INTEGER NOT NULL,
    source_file_hash    TEXT NOT NULL CHECK (length(source_file_hash) = 64),
    parser_version      TEXT NOT NULL,
    chunker_version     TEXT NOT NULL,
    config_json         TEXT NOT NULL,
    config_hash         TEXT NOT NULL CHECK (length(config_hash) = 64),
    built_at            TEXT NOT NULL,
    chunk_count         INTEGER NOT NULL CHECK (chunk_count >= 0),
    UNIQUE (
        source_file_hash,
        parser_version,
        chunker_version,
        config_hash
    )
);

CREATE TABLE IF NOT EXISTS chunks (
    id                  TEXT PRIMARY KEY CHECK (length(id) = 64),
    build_id            INTEGER NOT NULL,
    source_book_id      INTEGER NOT NULL,
    source_node_id      INTEGER NOT NULL,
    toc_index           INTEGER NOT NULL CHECK (toc_index >= 0),
    chunk_index         INTEGER NOT NULL CHECK (chunk_index >= 0),
    section_title       TEXT NOT NULL,
    path_text           TEXT NOT NULL,
    start_page          INTEGER NOT NULL CHECK (start_page > 0),
    end_page            INTEGER NOT NULL CHECK (end_page >= start_page),
    text                TEXT NOT NULL CHECK (length(text) > 0),
    content_types_json  TEXT NOT NULL,
    char_count          INTEGER NOT NULL CHECK (char_count = length(text)),
    token_count         INTEGER NOT NULL CHECK (token_count > 0),
    content_hash        TEXT NOT NULL CHECK (length(content_hash) = 64),
    FOREIGN KEY (build_id) REFERENCES chunk_builds(id) ON DELETE CASCADE,
    UNIQUE (build_id, toc_index, chunk_index)
);

CREATE TABLE IF NOT EXISTS chunk_sources (
    chunk_id            TEXT NOT NULL,
    source_order        INTEGER NOT NULL CHECK (source_order >= 0),
    source_block_id     INTEGER NOT NULL,
    block_index         INTEGER NOT NULL CHECK (block_index >= 0),
    page_number         INTEGER NOT NULL CHECK (page_number > 0),
    block_type          TEXT NOT NULL
        CHECK (block_type IN ('text', 'table', 'image')),
    category            TEXT NOT NULL,
    start_offset        INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset          INTEGER NOT NULL CHECK (end_offset >= start_offset),
    PRIMARY KEY (chunk_id, source_order),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_book_node
    ON chunks (source_book_id, source_node_id);

CREATE INDEX IF NOT EXISTS idx_chunks_build_order
    ON chunks (build_id, toc_index, chunk_index);

CREATE INDEX IF NOT EXISTS idx_chunk_sources_block
    ON chunk_sources (source_block_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    section_title,
    path_text,
    text,
    content = 'chunks',
    content_rowid = 'rowid',
    tokenize = 'porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_fts_insert
AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, section_title, path_text, text)
    VALUES (new.rowid, new.section_title, new.path_text, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_delete
AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(
        chunks_fts, rowid, section_title, path_text, text
    )
    VALUES (
        'delete', old.rowid, old.section_title, old.path_text, old.text
    );
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_update
AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(
        chunks_fts, rowid, section_title, path_text, text
    )
    VALUES (
        'delete', old.rowid, old.section_title, old.path_text, old.text
    );
    INSERT INTO chunks_fts(rowid, section_title, path_text, text)
    VALUES (new.rowid, new.section_title, new.path_text, new.text);
END;
