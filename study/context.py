"""Format one complete canonical scope as non-overlapping LLM context."""

from dataclasses import dataclass

import tiktoken

from parsing.models import NON_CONTENT_CATEGORIES
from .content import EvidenceBundle

DEFAULT_ENCODING = "cl100k_base"
SKIPPED_CATEGORIES = NON_CONTENT_CATEGORIES


@dataclass(frozen=True)
class ScopeContext:
    """Complete formatted evidence plus auditable inclusion metadata."""

    text: str
    token_count: int
    included_block_count: int
    skipped_block_count: int
    table_count: int
    image_count: int
    expected_node_ids: frozenset[int]
    allowed_citations: frozenset[tuple[int, int]]


def build_scope_context(
    evidence: EvidenceBundle,
    *,
    encoding_name: str = DEFAULT_ENCODING,
) -> ScopeContext:
    """Format all meaningful blocks without chunk overlap or truncation."""

    sections: list[str] = []
    included = 0
    skipped = 0
    tables = 0
    images = 0
    expected_nodes: set[int] = set()
    allowed_citations: set[tuple[int, int]] = set()

    for node_content in evidence.nodes:
        node = node_content.node
        node_lines: list[str] = []
        for block in node_content.blocks:
            if block.category in SKIPPED_CATEGORIES:
                skipped += 1
                continue
            if block.block_type == "image":
                value = (
                    "<image payload omitted from text-only summarization; "
                    "use only adjacent caption text>"
                )
                images += 1
            else:
                value = (block.readable_text or "").strip()
                if block.block_type == "table":
                    tables += 1
                    value = f"Table:\n{value}" if value else ""
            if not value:
                skipped += 1
                continue

            # Use the exact marker the answer must emit. Block indexes remain
            # canonical provenance in storage, but exposing a different
            # citation shape in evidence made models translate markers instead
            # of simply copying them and increased citation omissions.
            marker = f"[N{block.node_id}:P{block.page_number}]"
            node_lines.append(f"{marker}\n{value}")
            included += 1
            expected_nodes.add(block.node_id)
            allowed_citations.add((block.node_id, block.page_number))

        if node_lines:
            sections.append(
                f"## Node {node.id}: {node.path_text}\n\n"
                + "\n\n".join(node_lines)
            )

    text = "\n\n".join(sections)
    encoding = tiktoken.get_encoding(encoding_name)
    return ScopeContext(
        text=text,
        token_count=len(encoding.encode(text)),
        included_block_count=included,
        skipped_block_count=skipped,
        table_count=tables,
        image_count=images,
        expected_node_ids=frozenset(expected_nodes),
        allowed_citations=frozenset(allowed_citations),
    )
