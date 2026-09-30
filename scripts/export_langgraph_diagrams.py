"""Export documentation from compiled LangGraph workflows without running them."""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH_SPECS = {
    "study-turn": ("study.graph", "study_turn_graph"),
    "lecture-turn": ("video.conversation", "video_turn_graph"),
    "course-turn": ("video.course_conversation", "course_turn_graph"),
    "interview-answer": ("interviews.graph", "answer_graph"),
    "revision-sheet": ("revision_sheets.graph", "build_revision_graph"),
}


def documentation_only_node(state):
    raise RuntimeError("Documentation exports must not execute revision-sheet nodes")


def compiled_graphs():
    graphs = {}
    for name, (module_name, attribute) in GRAPH_SPECS.items():
        module = import_module(module_name)
        graph = getattr(module, attribute)
        if name == "revision-sheet":
            # The runtime supplies source/model-bound closures to this same
            # builder. Only topology is needed here; nothing is invoked.
            graph = graph({node: documentation_only_node for node in module.ROUTES})
        graphs[name] = graph
    return graphs


def render_outputs(root: Path = ROOT) -> dict[Path, str]:
    document_path = root / "docs/langgraph.md"
    document = document_path.read_text()
    outputs = {}
    for name, graph in compiled_graphs().items():
        mermaid = graph.get_graph().draw_mermaid()
        outputs[root / f"docs/graphs/{name}.mmd"] = mermaid
        begin = f"<!-- BEGIN GENERATED: {name} -->"
        end = f"<!-- END GENERATED: {name} -->"
        if document.count(begin) != 1 or document.count(end) != 1:
            raise ValueError(f"Missing or duplicate graph markers for {name}")
        before, _, remainder = document.partition(begin)
        _, found, after = remainder.partition(end)
        if not found:
            raise ValueError(f"Missing end marker for {name}")
        document = f"{before}{begin}\n```mermaid\n{mermaid}```\n{end}{after}"
    outputs[document_path] = document
    return outputs


def sync_outputs(root: Path = ROOT, *, check: bool = False) -> list[Path]:
    stale = []
    for path, content in render_outputs(root).items():
        if path.exists() and path.read_text() == content:
            continue
        stale.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if diagrams need regeneration")
    args = parser.parse_args()
    stale = sync_outputs(check=args.check)
    for path in stale:
        print(f"{'Stale' if args.check else 'Updated'}: {path.relative_to(ROOT)}")
    if args.check and stale:
        print("Run: uv run python -m scripts.export_langgraph_diagrams")
        return 1
    if not stale:
        print("All five LangGraph diagrams are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
