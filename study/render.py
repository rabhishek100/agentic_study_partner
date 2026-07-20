"""Reader-facing rendering for deterministic hierarchy operations."""

from .scope import ResolvedScope


def format_outline(scope: ResolvedScope) -> str:
    """Render a resolved scope's descendants in canonical TOC order."""

    lines = [
        f"# {scope.display_path}",
        "",
        f"Book: {scope.book_title}",
        f"PDF pages: {scope.start_page}–{scope.end_page}",
        "",
        "Sections:",
    ]
    root_level = scope.nodes[0].level
    for node in scope.nodes[1:]:
        indent = "  " * max(0, node.level - root_level - 1)
        pages = (
            str(node.start_page)
            if node.start_page == node.end_page
            else f"{node.start_page}–{node.end_page}"
        )
        lines.append(
            f"{indent}- {node.title} [node {node.id}, PDF pp. {pages}]"
        )
    return "\n".join(lines)
