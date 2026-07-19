"""Inspect deterministic book, chapter, and section scopes."""

import argparse
from pathlib import Path

from storage.sqlite import connect_readonly
from study.content import EvidenceBundle, load_scope_content
from study.scope import (
    ResolvedScope,
    ScopeResolutionError,
    resolve_book,
    resolve_chapter,
    resolve_section,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a canonical study scope and inspect its complete hierarchy."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/books.sqlite3"),
        help="Canonical SQLite database (default: data/books.sqlite3)",
    )
    subparsers = parser.add_subparsers(dest="scope_kind", required=True)

    book = subparsers.add_parser("book", help="Inspect a complete book")
    book.add_argument("reference", nargs="?", help="Exact or partial book title")
    book.add_argument("--book-id", type=int)
    book.add_argument("--show-content", action="store_true")

    chapter = subparsers.add_parser("chapter", help="Inspect a chapter")
    chapter.add_argument("reference", help="Chapter number or title")
    chapter.add_argument("--book-id", type=int)
    chapter.add_argument("--show-content", action="store_true")

    section = subparsers.add_parser("section", help="Inspect a section")
    section.add_argument("reference", help="Section title or hierarchy path")
    section.add_argument("--chapter", help="Optional containing chapter")
    section.add_argument("--book-id", type=int)
    section.add_argument("--show-content", action="store_true")

    return parser


def _pages(start_page: int, end_page: int) -> str:
    return (
        str(start_page)
        if start_page == end_page
        else f"{start_page}–{end_page}"
    )


def format_inspection(
    scope: ResolvedScope,
    evidence: EvidenceBundle,
    *,
    show_content: bool = False,
) -> str:
    """Format a stable reader-facing scope diagnostic."""

    lines = [
        f"Book: {scope.book_title} (ID {scope.book_id})",
        f"Scope: {scope.kind} — {scope.display_path}",
        f"PDF pages: {_pages(scope.start_page, scope.end_page)}",
        f"TOC nodes: {len(scope.nodes)}",
        (
            f"Content blocks: {evidence.block_count} "
            f"({evidence.table_count} tables, {evidence.image_count} images)"
        ),
        f"Readable characters: {evidence.readable_char_count}",
        "",
        "Hierarchy:",
    ]
    base_level = scope.nodes[0].level if scope.root_node_id is not None else 1
    for node_content in evidence.nodes:
        node = node_content.node
        indent = "  " * max(0, node.level - base_level)
        lines.append(
            f"{indent}- {node.title} "
            f"[node {node.id}, PDF pp. {_pages(node.start_page, node.end_page)}, "
            f"{len(node_content.blocks)} blocks]"
        )
        if not show_content:
            continue
        for block in node_content.blocks:
            readable = " ".join((block.readable_text or "").split())
            if readable:
                preview = readable[:240] + ("…" if len(readable) > 240 else "")
            elif block.block_type == "image":
                preview = (
                    f"<image: {block.image_mime_type or 'unknown MIME type'}>"
                )
            else:
                preview = "<no readable text>"
            lines.append(
                f"{indent}  · block {block.block_index} "
                f"{block.block_type} p. {block.page_number}: {preview}"
            )
    return "\n".join(lines)


def _resolve(args, connection) -> ResolvedScope:
    if args.scope_kind == "book":
        return resolve_book(
            connection,
            args.reference,
            book_id=args.book_id,
        )
    if args.scope_kind == "chapter":
        return resolve_chapter(
            connection,
            args.reference,
            book_id=args.book_id,
        )
    return resolve_section(
        connection,
        args.reference,
        book_id=args.book_id,
        chapter=args.chapter,
    )


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()
    try:
        with connect_readonly(args.database) as connection:
            scope = _resolve(args, connection)
            evidence = load_scope_content(connection, scope)
    except ScopeResolutionError as error:
        parser.error(str(error))
    print(
        format_inspection(
            scope,
            evidence,
            show_content=args.show_content,
        )
    )


if __name__ == "__main__":
    main()
