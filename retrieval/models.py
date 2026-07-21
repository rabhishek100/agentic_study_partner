"""Data contracts for rebuildable retrieval chunks."""

from dataclasses import dataclass, field


DEFAULT_EXCLUDED_TITLES = (
    "Cover",
    "Copyright",
    "Table of Contents",
    "Index",
    "About the Author",
    "Colophon",
)


@dataclass(frozen=True)
class ChunkingConfig:
    """Deterministic parameters used to construct chunks."""

    target_tokens: int = 600
    max_tokens: int = 800
    overlap_tokens: int = 80
    encoding_name: str = "cl100k_base"
    excluded_titles: tuple[str, ...] = DEFAULT_EXCLUDED_TITLES

    def __post_init__(self) -> None:
        if self.target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if self.max_tokens < self.target_tokens:
            raise ValueError("max_tokens must be at least target_tokens")
        if not 0 <= self.overlap_tokens < self.target_tokens:
            raise ValueError("overlap_tokens must be between 0 and target_tokens")
        if not self.encoding_name.strip():
            raise ValueError("encoding_name cannot be empty")


@dataclass(frozen=True)
class ChunkSource:
    """The canonical block, or block fragment, represented in a chunk."""

    source_block_id: int
    block_index: int
    page_number: int
    block_type: str
    category: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class Chunk:
    """One searchable text unit with exact canonical provenance."""

    id: str
    source_book_id: int
    source_file_hash: str
    source_node_id: int
    toc_index: int
    chunk_index: int
    section_title: str
    path_text: str
    start_page: int
    end_page: int
    text: str
    content_types: tuple[str, ...]
    char_count: int
    token_count: int
    content_hash: str
    sources: tuple[ChunkSource, ...] = field(default_factory=tuple)
