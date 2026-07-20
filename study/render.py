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


def format_chapter_list(scope: ResolvedScope) -> str:
    """Render the chapters in one resolved book."""

    nodes = list(scope.nodes)
    chapters = [node for node in nodes if node.node_type == "chapter"]
    lines = [
        f"# {scope.book_title}",
        "",
        f"PDF pages: {scope.start_page}–{scope.end_page}",
        "",
        "Chapters:",
    ]
    for chapter in chapters:
        chapter_position = nodes.index(chapter)
        chapter_end_page = chapter.end_page
        for descendant in nodes[chapter_position + 1 :]:
            if descendant.level <= chapter.level:
                break
            chapter_end_page = max(chapter_end_page, descendant.end_page)
        pages = (
            str(chapter.start_page)
            if chapter.start_page == chapter_end_page
            else f"{chapter.start_page}–{chapter_end_page}"
        )
        lines.append(
            f"- {chapter.title} "
            f"[node {chapter.id}, PDF pp. {pages}]"
        )
    return "\n".join(lines)
