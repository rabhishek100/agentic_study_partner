"""Load complete canonical content for an already resolved scope."""

from dataclasses import dataclass
import sqlite3

from .scope import ResolvedScope, ScopeNode


@dataclass(frozen=True)
class ContentBlock:
    """One ordered canonical block without loading image binary payloads."""

    id: int
    node_id: int
    block_index: int
    block_type: str
    category: str
    page_number: int
    text_content: str | None
    table_text: str | None
    table_html: str | None
    image_mime_type: str | None
    has_image_payload: bool

    @property
    def readable_text(self) -> str | None:
        if self.block_type == "table":
            return self.table_text
        if self.block_type == "image":
            return None
        return self.text_content


@dataclass(frozen=True)
class NodeContent:
    """A TOC node and all blocks directly owned by it."""

    node: ScopeNode
    blocks: tuple[ContentBlock, ...]


@dataclass(frozen=True)
class EvidenceBundle:
    """Complete ordered canonical evidence for one resolved scope."""

    scope: ResolvedScope
    nodes: tuple[NodeContent, ...]

    @property
    def block_count(self) -> int:
        return sum(len(node.blocks) for node in self.nodes)

    @property
    def table_count(self) -> int:
        return sum(
            block.block_type == "table"
            for node in self.nodes
            for block in node.blocks
        )

    @property
    def image_count(self) -> int:
        return sum(
            block.block_type == "image"
            for node in self.nodes
            for block in node.blocks
        )

    @property
    def readable_char_count(self) -> int:
        return sum(
            len(block.readable_text or "")
            for node in self.nodes
            for block in node.blocks
        )


def load_scope_content(
    connection: sqlite3.Connection,
    scope: ResolvedScope,
) -> EvidenceBundle:
    """Load every block in scope order, flattening tables for readable text."""

    by_node: dict[int, list[ContentBlock]] = {
        node.id: [] for node in scope.nodes
    }
    if not by_node:
        return EvidenceBundle(scope=scope, nodes=())

    placeholders = ", ".join("?" for _ in by_node)
    rows = connection.execute(
        f"""
        SELECT
            content_blocks.id,
            content_blocks.node_id,
            content_blocks.block_index,
            content_blocks.block_type,
            content_blocks.category,
            content_blocks.page_number,
            content_blocks.text_content,
            table_blocks.flat_text,
            table_blocks.html_content,
            image_blocks.mime_type,
            image_blocks.block_id AS image_payload_id
        FROM content_blocks
        JOIN nodes ON nodes.id = content_blocks.node_id
        LEFT JOIN table_blocks ON table_blocks.block_id = content_blocks.id
        LEFT JOIN image_blocks ON image_blocks.block_id = content_blocks.id
        WHERE content_blocks.node_id IN ({placeholders})
        ORDER BY nodes.toc_index, content_blocks.block_index
        """,
        tuple(by_node),
    ).fetchall()
    for row in rows:
        by_node[row["node_id"]].append(
            ContentBlock(
                id=row["id"],
                node_id=row["node_id"],
                block_index=row["block_index"],
                block_type=row["block_type"],
                category=row["category"],
                page_number=row["page_number"],
                text_content=row["text_content"],
                table_text=row["flat_text"],
                table_html=row["html_content"],
                image_mime_type=row["mime_type"],
                has_image_payload=row["image_payload_id"] is not None,
            )
        )

    return EvidenceBundle(
        scope=scope,
        nodes=tuple(
            NodeContent(node=node, blocks=tuple(by_node[node.id]))
            for node in scope.nodes
        ),
    )
