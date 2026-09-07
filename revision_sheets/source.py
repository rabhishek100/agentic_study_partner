"""Canonical source snapshots; fingerprints do not depend on retrieval builds."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from parsing.models import NON_CONTENT_CATEGORIES
from storage.postgres import ready_book
from study.content import load_scope_content
from study.scope import resolve_book, resolve_node, ScopeResolutionError

from .contracts import RevisionError, ScopeRequest

SOURCE_VERSION = "revision-source-v1"


@dataclass
class Source:
    request: ScopeRequest
    title: str
    scope_title: str
    fingerprint: str
    text: str
    references: dict
    units: dict[str, set[str]]
    figures: list[dict]


def load_source(connection, *, owner_id, request: ScopeRequest) -> Source:
    book = ready_book(connection, request.book_id, owner_id=owner_id)
    if not book:
        raise RevisionError("source_unavailable", "Choose a ready source from your library.")
    try:
        if request.scope_kind == "paper":
            scope = resolve_book(connection, owner_id=owner_id, book_id=request.book_id)
            if scope.document_type != "paper":
                raise RevisionError("invalid_scope", "Choose one chapter for a book.")
        else:
            scope = resolve_node(connection, request.chapter_node_id, owner_id=owner_id)
            root = next(n for n in scope.nodes if n.id == request.chapter_node_id)
            if scope.book_id != request.book_id or scope.document_type == "paper" or root.node_type != "chapter":
                raise RevisionError("invalid_scope", "Choose a chapter belonging to this book.")
    except ScopeResolutionError as error:
        raise RevisionError("invalid_scope", "This source scope is no longer available.") from error
    bundle = load_scope_content(connection, scope, owner_id=owner_id)
    figures = connection.execute(
        """select i.block_id, i.owner_id, i.mime_type, i.storage_key,
                  i.content_hash, c.caption, c.skipped_reason,
                  b.node_id, b.page_number as page
           from image_blocks i join content_blocks b on b.id=i.block_id and b.owner_id=i.owner_id
           left join image_captions c on c.block_id=i.block_id and c.owner_id=i.owner_id
           where i.owner_id=%s and b.book_id=%s and b.node_id=any(%s)
           order by b.node_id, b.block_index""",
        (owner_id, request.book_id, list(scope.node_ids)),
    ).fetchall()
    canonical = {"policy": SOURCE_VERSION, "scope": asdict(scope),
                 "content": [asdict(n) for n in bundle.nodes],
                 "images": [{k: f[k] for k in ("block_id", "content_hash")} for f in figures]}
    fingerprint = sha256(json.dumps(canonical, sort_keys=True, default=str).encode()).hexdigest()
    refs, units, parts = {}, {}, []
    for ordinal, group in enumerate(bundle.nodes, 1):
        for block in group.blocks:
            if block.category in NON_CONTENT_CATEGORIES:
                continue
            value = (block.readable_text or "").strip()
            if block.block_type == "image":
                figure = next((f for f in figures if f["block_id"] == block.id), None)
                if figure and figure["skipped_reason"] not in (None, "failed"):
                    continue
                value = f"Figure block {block.id}. Contents unknown until image inspection."
                if figure and figure["caption"]:
                    value += " Derived discovery description (verify against image): " + figure["caption"]
            if not value:
                continue
            marker = f"[N{block.node_id}:P{block.page_number}]"
            unit = f"N{block.node_id}:P{block.page_number}"
            units.setdefault(unit, set()).add(marker)
            refs[marker] = {"node_id": block.node_id, "page": block.page_number,
                            "book_id": request.book_id, "path": group.node.path_text,
                            "section_number": str(ordinal), "section_title": group.node.title}
            parts.append(f"Source unit {unit} | {group.node.path_text}\n{marker}\n{value}")
    if not parts:
        raise RevisionError("insufficient_evidence", "This scope has no readable source evidence.")
    return Source(request, scope.book_title, scope.display_path, fingerprint,
                  "\n\n".join(parts), refs, units, figures)
