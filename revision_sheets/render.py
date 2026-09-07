"""Measured A4 output. No font shrinking, clipped text, or model-authored HTML."""

from __future__ import annotations

from html import escape
import math
import re

import pymupdf as fitz

from .contracts import Item, RevisionError, Sheet

LAYOUT_VERSION = "a4-v3"
INK = (0.10, 0.19, 0.17)
JADE = (0.17, 0.37, 0.31)
WASH = (0.93, 0.96, 0.94)
CSS = """
body { font-family: sans-serif; font-size: 10pt; line-height: 1.25; color: #19302b; margin: 0; padding: 0; }
p { margin: 0 0 5pt; } b { font-weight: bold; }
.cite { font-size: 8pt; color: #3d6559; }
h1 { font-size: 21pt; line-height: 1.1; margin: 3pt 0 7pt; }
h2 { font-size: 11pt; margin: 0 0 7pt; color: #2b5e4f; }
"""


def citations(markers: list[str], references: dict) -> str:
    locations: dict[str, set[int]] = {}
    for marker in dict.fromkeys(markers):
        ref = references[marker]
        locations.setdefault(ref['section_number'], set()).add(ref['page'])
    return "[" + "; ".join(f"{section} p.{','.join(map(str, sorted(pages)))}" for section, pages in locations.items()) + "]"


def item_html(item: Item, refs: dict) -> str:
    heading = f"<b>{escape(item.heading)}.</b> " if item.heading else ""
    return (f"<p>{heading}{escape(item.text)} "
            f"<span class='cite'>{escape(citations(item.citations, refs))}</span></p>")


def diagram_layout(sheet: Sheet) -> dict:
    """Stable compact topology; numbered edges match the explicit relationship key."""
    nodes = sheet.diagram.nodes
    # Source order is part of the model's diagram contract. Two rows reserve
    # space for relationships without compressing eight labels into one line.
    cols = min(4, len(nodes))
    result = []
    for i, node in enumerate(nodes):
        row, col = divmod(i, cols)
        if row % 2:
            col = cols - 1 - col
        result.append({"id": node.id, "x": col * 130 + 5, "y": row * 75 + 10,
                       "width": 110, "height": 45})
    lookup = {n["id"]: n for n in result}
    ports: dict[tuple[str, str], list[tuple[str, str]]] = {}
    sides = {}
    for edge in sheet.diagram.edges:
        a, b = lookup[edge.source], lookup[edge.target]
        adjacent = a["y"] == b["y"] and abs(a["x"] - b["x"]) == 130
        source_side = ("right" if b["x"] > a["x"] else "left") if adjacent else ("bottom" if a["y"] < 50 else "top")
        target_side = ("left" if b["x"] > a["x"] else "right") if adjacent else ("bottom" if b["y"] < 50 else "top")
        for node_id, side, end in ((edge.source, source_side, "source"), (edge.target, target_side, "target")):
            ports.setdefault((node_id, side), []).append((edge.id, end))
            sides[(edge.id, end)] = (node_id, side)

    def port(edge, end):
        node_id, side = sides[(edge.id, end)]
        node, occupants = lookup[node_id], ports[(node_id, side)]
        fraction = (occupants.index((edge.id, end)) + 1) / (len(occupants) + 1)
        if side in ("top", "bottom"):
            return [node["x"] + 110 * fraction, node["y"] + (45 if side == "bottom" else 0)]
        return [node["x"] + (110 if side == "right" else 0), node["y"] + 45 * fraction]

    edges = []
    for i, edge in enumerate(sheet.diagram.edges):
        a, b = lookup[edge.source], lookup[edge.target]
        start, end = port(edge, "source"), port(edge, "target")
        if a["y"] == b["y"] and abs(a["x"] - b["x"]) == 130:
            points = [start, end]
        else:
            lane = 62 + i * 1.7
            points = [start, [start[0], lane], [end[0], lane], end]
        if edge.source == edge.target:
            points = [[a["x"] + 110, a["y"] + 22], [a["x"] + 118, a["y"] + 22],
                      [a["x"] + 118, a["y"] + 55], [a["x"] + 55, a["y"] + 55],
                      [a["x"] + 55, a["y"] + 45]]
        edges.append({"id": edge.id, "number": i + 1, "points": points})
    # Number labels may share a narrow routing corridor, but never a circle.
    # Choose a clear position on the edge itself, preserving its identity.
    labels = []
    for edge in edges:
        candidates = []
        for a, b in zip(edge["points"], edge["points"][1:]):
            length = math.dist(a, b)
            for fraction in (0.5, 0.25, 0.75, 0.15, 0.85):
                if min(fraction, 1-fraction) * length < 7:
                    continue
                point = [a[j] + (b[j]-a[j])*fraction for j in (0, 1)]
                if any(n["x"]-7 < point[0] < n["x"]+117 and n["y"]-7 < point[1] < n["y"]+52 for n in result):
                    continue
                candidates.append(point)
        clear = [p for p in candidates if all(math.dist(p, other) >= 16 for other in labels)]
        if not clear:
            raise RevisionError("page_overflow", "Diagram relationships are too crowded. Simplify the graph while preserving critical branches.")
        edge["label"] = clear[0]
        labels.append(clear[0])
    return {"width": 520, "height": 140 if len(nodes) > 4 else 85,
            "nodes": result, "edges": edges}


def _box(page, rect, html: str, *, name: str, css: str = CSS) -> float:
    spare, scale = page.insert_htmlbox(rect, html, css=css, scale_low=1)
    if spare < 0 or scale < 0.999:
        raise RevisionError("page_overflow", f"{name} does not fit at readable A4 size. Shorten this region.")
    return rect.height - spare


def render_pdf(sheet: Sheet, *, source_title: str, scope_title: str,
               references: dict) -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page(width=595.276, height=841.89)
        left, right, bottom = 34.02, 561.25, 807.87
        header = ("<p class='cite'>REVISION SHEET · " + escape(source_title) + "</p>"
                  + f"<h1>{escape(sheet.title)}</h1>" + item_html(sheet.central_idea, references))
        h = _box(page, fitz.Rect(left, 34, right, 157), header, name="Central idea")
        y = 34 + h + 10
        page.draw_line((left, y), (right, y), color=JADE, width=0.8)
        y += 9
        h = _box(page, fitz.Rect(left, y, right, y + 65),
                 f"<p><b>Overview · simplified.</b> {escape(sheet.diagram.description)} "
                 f"<span class='cite'>{escape(citations(sheet.diagram.description_citations, references))}</span></p>", name="Diagram description")
        y += h + 3
        layout = diagram_layout(sheet)
        ox, oy = left + 2, y
        for edge in layout["edges"]:
            pts = [fitz.Point(ox + p[0], oy + p[1]) for p in edge["points"]]
            page.draw_polyline(pts, color=JADE, width=0.65)
            a, b = pts[-2], pts[-1]
            angle = math.atan2(b.y - a.y, b.x - a.x)
            for delta in (-0.55, 0.55):
                page.draw_line(b, (b.x - 5 * math.cos(angle + delta), b.y - 5 * math.sin(angle + delta)), color=JADE, width=0.8)
            # Number the relationship itself so its key need not repeat long
            # component labels. This buys room without deleting source ideas.
            center = fitz.Point(ox + edge["label"][0], oy + edge["label"][1])
            page.draw_circle(center, 7, color=JADE, fill=(1, 1, 1), width=0.5)
            page.insert_text((center.x - 2.8, center.y + 3.4), str(edge["number"]), fontsize=10, color=INK)
        for placed, node in zip(layout["nodes"], sheet.diagram.nodes):
            rect = fitz.Rect(ox + placed["x"], oy + placed["y"],
                             ox + placed["x"] + 110, oy + placed["y"] + 45)
            page.draw_rect(rect, color=JADE, fill=WASH, width=0.6)
            _box(page, rect + (4, 3, -4, -3),
                 f"<b>{escape(node.label)}</b><br><span class='cite'>{escape(citations(node.citations, references))}</span>",
                 name=f"Diagram node {node.id}")
        y += layout["height"]
        relationships = "".join(
            f"<p><b>{i}.</b> {escape(e.label)} "
            f"<span class='cite'>{escape(citations(e.citations, references))}</span></p>"
            for i, e in enumerate(sheet.diagram.edges, 1)
        )
        h = _box(page, fitz.Rect(left, y, right, min(y + 145, bottom)), relationships,
                 name="Diagram relationships", css=CSS + "p { margin: 0 0 2pt; }")
        y += h + 10
        if y > 565:
            raise RevisionError("page_overflow", "Overview leaves too little space for essential notes. Compress diagram labels and relationships.")
        gutter, mid = 18, (left + right) / 2
        side_title = "Results & limitations" if sheet.template_kind == "paper" else "Trade-offs & failure modes"
        groups = [("Essential concepts", [*sheet.essential_notes, *([sheet.equation] if sheet.equation else [])]),
                  (side_title, sheet.comparison_rows), ("Recall", sheet.recall_cues)]
        blocks, headings = [], []
        for heading, items in groups:
            for index, item in enumerate(items):
                blocks.append((f"<h2>{heading}</h2>" if index == 0 else "") + item_html(item, references))
                headings.append(heading if index else "")
        sections = {r['section_number']: r['section_title'] for r in references.values()}
        used = {references[m]['section_number'] for item in sheet.items() for m in item.citations}
        key = "; ".join(f"{k}: {sections[k]}" for k in sorted(used, key=int))
        footer_height = min(100, 31 + 10 * (len(key) // 125))
        footer_y = bottom - footer_height
        columns = [fitz.Rect(left, y, mid - gutter / 2, footer_y - 8),
                   fitz.Rect(mid + gutter / 2, y, right, footer_y - 8)]
        best = None
        # Balance whole paragraphs in source reading order. The longest region
        # can use both columns; headings never become stranded at the bottom.
        for split in range(1, len(blocks)):
            candidates = ["".join(blocks[:split]),
                          (f"<h2>{headings[split]} · continued</h2>" if headings[split] else "") + "".join(blocks[split:])]
            with fitz.open() as measure:
                scratch = measure.new_page(width=page.rect.width, height=page.rect.height)
                measured = [scratch.insert_htmlbox(rect, html, css=CSS, scale_low=1)
                            for rect, html in zip(columns, candidates)]
            if all(spare >= 0 and scale >= 0.999 for spare, scale in measured):
                imbalance = abs(measured[0][0] - measured[1][0])
                if best is None or imbalance < best[0]:
                    best = (imbalance, candidates)
        if best is None:
            raise RevisionError("page_overflow", "Essential concepts and trade-offs do not fit in the two A4 columns. Shorten notes while preserving essential meaning.")
        for rect, html in zip(columns, best[1]):
            _box(page, rect, html, name="Revision notes")
        page.draw_line((left, footer_y - 2), (right, footer_y - 2), color=JADE, width=0.5)
        _box(page, fitz.Rect(left, footer_y, right, bottom),
             f"<span class='cite'>{escape(scope_title)} · PDF page citations. {escape(key)}</span>", name="Source key")
        text = page.get_text()
        if "\ufffd" in text or "\x00" in text:
            raise RevisionError("unsupported_glyph", "The PDF contains an unsupported character.")
        # HTML escaping prevents model text from becoming markup. Verify every
        # prose field survives extraction (ligatures/whitespace normalized).
        import unicodedata
        normalize = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))
        extracted = normalize(text)
        for item in sheet.items():
            value = getattr(item, "text", getattr(item, "label", ""))
            if normalize(value) not in extracted:
                raise RevisionError("text_preservation", f"PDF did not preserve item {item.id}.")
        doc.set_metadata({"title": sheet.title, "subject": source_title,
                          "creator": f"Mugensei revision sheets {LAYOUT_VERSION}"})
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()
