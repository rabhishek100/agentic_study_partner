"""Bounded model output; source identity and provenance are server-owned."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "revision-v3"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScopeRequest(Contract):
    scope_kind: Literal["chapter", "paper"]
    book_id: int = Field(gt=0)
    chapter_node_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def matching_scope(self):
        if (self.scope_kind == "chapter") != (self.chapter_node_id is not None):
            raise ValueError("Select one chapter for a book, or the entire paper.")
        return self

    @property
    def key(self) -> str:
        if self.scope_kind == "paper":
            return f"paper:{self.book_id}"
        return f"book:{self.book_id}:chapter:{self.chapter_node_id}"


class Item(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    heading: str = Field(max_length=70)
    text: str = Field(min_length=1, max_length=650)
    citations: list[str] = Field(min_length=1, max_length=12)


class DiagramNode(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=55)
    citations: list[str] = Field(min_length=1, max_length=8)


class DiagramEdge(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    source: str
    target: str
    label: str = Field(min_length=1, max_length=85)
    citations: list[str] = Field(min_length=1, max_length=8)


class Diagram(Contract):
    description: str = Field(min_length=1, max_length=240)
    description_citations: list[str] = Field(min_length=1, max_length=8)
    nodes: list[DiagramNode] = Field(min_length=2, max_length=8)
    edges: list[DiagramEdge] = Field(min_length=1, max_length=10)
    source_figure_ids: list[int] = Field(max_length=12)


class Concept(Contract):
    id: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=120)
    citations: list[str] = Field(min_length=1, max_length=12)
    item_ids: list[str] = Field(min_length=1, max_length=20)


class Disposition(Contract):
    source_unit: str
    item_ids: list[str] = Field(max_length=30)
    reason: str = Field(max_length=240)


class Sheet(Contract):
    template_kind: Literal["chapter", "paper"]
    title: str = Field(min_length=1, max_length=100)
    central_idea: Item
    diagram: Diagram
    essential_notes: list[Item] = Field(min_length=1, max_length=12)
    comparison_rows: list[Item] = Field(min_length=1, max_length=4)
    equation: Item | None
    recall_cues: list[Item] = Field(min_length=1, max_length=3)
    essential_concepts: list[Concept] = Field(min_length=1, max_length=60)
    source_dispositions: list[Disposition] = Field(min_length=1, max_length=400)
    compression_notes: list[str] = Field(max_length=20)

    def items(self) -> list[Item | DiagramNode | DiagramEdge]:
        return [self.central_idea, Item(id="diagram_description", heading="", text=self.diagram.description,
                citations=self.diagram.description_citations), *self.diagram.nodes, *self.diagram.edges,
                *self.essential_notes, *self.comparison_rows,
                *([self.equation] if self.equation else []), *self.recall_cues]


class RevisionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
