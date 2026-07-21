PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS books (
    id                  INTEGER PRIMARY KEY,
    title               TEXT NOT NULL,
    author              TEXT,
    source_path         TEXT NOT NULL,
    source_filename     TEXT NOT NULL,
    file_hash           TEXT NOT NULL UNIQUE CHECK (length(file_hash) = 64),
    page_count          INTEGER CHECK (page_count IS NULL OR page_count > 0),
    parser_version      TEXT NOT NULL,
    parsed_at           TEXT NOT NULL,
    metadata_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS nodes (
    id                  INTEGER PRIMARY KEY,
    book_id             INTEGER NOT NULL,
    parent_id           INTEGER,
    toc_index           INTEGER NOT NULL CHECK (toc_index >= 0),
    toc_level           INTEGER NOT NULL CHECK (toc_level > 0),
    node_type           TEXT NOT NULL,
    title               TEXT NOT NULL,
    path_text           TEXT NOT NULL,
    path_json           TEXT NOT NULL,
    start_page          INTEGER NOT NULL CHECK (start_page > 0),
    end_page            INTEGER NOT NULL CHECK (end_page >= start_page),
    direct_text         TEXT NOT NULL DEFAULT '',
    direct_char_count   INTEGER NOT NULL DEFAULT 0
        CHECK (direct_char_count = length(direct_text)),
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES nodes(id) ON DELETE CASCADE,
    UNIQUE (book_id, toc_index)
);

CREATE TABLE IF NOT EXISTS content_blocks (
    id                  INTEGER PRIMARY KEY,
    node_id             INTEGER NOT NULL,
    block_index         INTEGER NOT NULL CHECK (block_index >= 0),
    block_type          TEXT NOT NULL CHECK (block_type IN ('text', 'table', 'image')),
    category            TEXT NOT NULL,
    page_number         INTEGER NOT NULL CHECK (page_number > 0),
    text_content        TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    UNIQUE (node_id, block_index)
);

CREATE TABLE IF NOT EXISTS table_blocks (
    block_id            INTEGER PRIMARY KEY,
    html_content        TEXT,
    flat_text           TEXT NOT NULL,
    FOREIGN KEY (block_id) REFERENCES content_blocks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS image_blocks (
    block_id            INTEGER PRIMARY KEY,
    mime_type           TEXT NOT NULL,
    base64_content      TEXT NOT NULL,
    FOREIGN KEY (block_id) REFERENCES content_blocks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nodes_book_parent
    ON nodes (book_id, parent_id);

CREATE INDEX IF NOT EXISTS idx_nodes_book_pages
    ON nodes (book_id, start_page, end_page);

CREATE INDEX IF NOT EXISTS idx_content_blocks_node
    ON content_blocks (node_id, block_index);
