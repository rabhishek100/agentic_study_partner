"""Graph exports must expose real branches and remain reproducible offline."""

import ast
from collections import Counter
from pathlib import Path
import tempfile
import unittest

from revision_sheets.graph import build_revision_graph
from scripts.export_langgraph_diagrams import (
    GRAPH_SPECS, ROOT, compiled_graphs, render_outputs, sync_outputs,
)


class GraphDocumentationTests(unittest.TestCase):
    def test_all_graph_definition_modules_are_registered(self):
        definitions = set()
        for package in ("study", "video", "interviews", "revision_sheets"):
            for path in (ROOT / package).rglob("*.py"):
                tree = ast.parse(path.read_text())
                if any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "StateGraph"
                    for node in ast.walk(tree)
                ):
                    definitions.add(".".join(path.relative_to(ROOT).with_suffix("").parts))
        self.assertEqual(definitions, {module for module, _ in GRAPH_SPECS.values()})

    def test_exported_nodes_are_reachable_and_can_finish(self):
        # Missing conditional route metadata previously made three visualizations
        # omit their branches and leave most registered nodes disconnected.
        for name, compiled in compiled_graphs().items():
            with self.subTest(graph=name):
                graph = compiled.get_graph()
                edges = {(edge.source, edge.target) for edge in graph.edges}

                def reachable(start, *, reverse=False):
                    reached = {start}
                    pending = [start]
                    while pending:
                        current = pending.pop()
                        for source, target in edges:
                            if reverse:
                                source, target = target, source
                            if source == current and target not in reached:
                                reached.add(target)
                                pending.append(target)
                    return reached

                self.assertEqual(set(graph.nodes), reachable("__start__"))
                self.assertEqual(set(graph.nodes), reachable("__end__", reverse=True))

    def test_check_detects_stale_raw_and_embedded_diagrams_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide = root / "docs/langgraph.md"
            guide.parent.mkdir()
            guide.write_text((ROOT / "docs/langgraph.md").read_text())
            sync_outputs(root)
            self.assertEqual(sync_outputs(root, check=True), [])
            raw = root / "docs/graphs/lecture-turn.mmd"
            raw.write_text("stale graph\n")
            guide.write_text(guide.read_text().replace("```mermaid", "```text", 1))
            self.assertEqual(set(sync_outputs(root, check=True)), {raw, guide})
            self.assertEqual(raw.read_text(), "stale graph\n")
            self.assertIn("```text", guide.read_text())

    def test_committed_diagrams_match_langgraph_output(self):
        for path, expected in render_outputs().items():
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertEqual(path.read_text(), expected)

    def test_revision_builder_runs_bound_nodes_through_repairs_to_completion(self):
        calls = Counter()

        def inventory(state):
            calls["inventory"] += 1
            return {"next": "compose"}

        def compose(state):
            calls["compose"] += 1
            if calls["compose"] == 1:
                return {"next": "compose", "content_repairs": 1}
            return {"next": "validate"}

        def validate(state):
            calls["validate"] += 1
            if calls["validate"] == 1:
                return {"next": "compose", "content_repairs": 2}
            return {"next": "render"}

        def render(state):
            calls["render"] += 1
            if calls["render"] == 1:
                return {"next": "compose", "fit_repairs": 1}
            return {"next": "judge"}

        def judge(state):
            calls["judge"] += 1
            if calls["judge"] == 1:
                return {"next": "compose", "quality_repairs": 1}
            return {"next": "done", "pdf": b"completed fixture"}

        result = build_revision_graph({
            "inventory": inventory, "compose": compose, "validate": validate,
            "render": render, "judge": judge,
        }).invoke({})
        self.assertEqual(result["pdf"], b"completed fixture")
        self.assertEqual(result["content_repairs"], 2)
        self.assertEqual(result["fit_repairs"], 1)
        self.assertEqual(result["quality_repairs"], 1)
        self.assertEqual(calls["inventory"], 1)
        self.assertEqual(calls["compose"], 5)


if __name__ == "__main__":
    unittest.main()
