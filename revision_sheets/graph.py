"""Revision-sheet graph topology, shared by generation and documentation."""

from collections.abc import Callable, Mapping
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .contracts import Sheet
from .review import Inventory, Review


class State(TypedDict, total=False):
    sheet: Sheet
    pdf: bytes
    html: str
    summary_figure_ids: list[int]
    layout_profile: str
    inventory: Inventory
    review: Review
    reviews: list[dict]
    quality_repairs: int
    advisories: list
    outstanding_findings: list
    quality_patch: bool
    feedback: str
    content_repairs: int
    fit_repairs: int
    resolved_inventory_references: int
    next: str


# Only destinations each node can actually return. The runtime uses these same
# maps, so LangGraph can visualize repairs without inventing extra branches.
ROUTES = {
    "inventory": {"compose": "compose"},
    "compose": {"compose": "compose", "validate": "validate"},
    "validate": {"compose": "compose", "render": "render"},
    "render": {"compose": "compose", "judge": "judge"},
    "judge": {"compose": "compose", "done": END},
}


def route_next(state: State) -> str:
    return state["next"]


def build_revision_graph(nodes: Mapping[str, Callable]):
    """Compile the workflow with source-bound nodes supplied by the caller.

    Building the topology does not inspect figures, call models, or render PDFs.
    Generation binds its closures; documentation binds nodes that refuse to run.
    """
    if set(nodes) != set(ROUTES):
        raise ValueError(f"Revision graph requires nodes: {', '.join(ROUTES)}")
    graph = StateGraph(State)
    for name in ROUTES:
        graph.add_node(name, nodes[name])
    graph.add_edge(START, "inventory")
    for name, destinations in ROUTES.items():
        graph.add_conditional_edges(name, route_next, destinations)
    return graph.compile()
