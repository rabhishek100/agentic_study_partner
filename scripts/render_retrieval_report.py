"""Render the canonical retrieval artifact as a self-contained HTML report."""

import argparse
from html import escape
import json
from pathlib import Path

from markdown_it import MarkdownIt


MARKDOWN = MarkdownIt("commonmark", {"html": False})


def _format_value(value, format_name: str | None = None) -> str:
    if value is None:
        return "—"
    if format_name == "percent" and isinstance(value, (int, float)):
        return f"{value:.1%}"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _render_metric_strip(block: dict, manifest: dict, datasets: dict) -> str:
    cards_by_id = {card["id"]: card for card in manifest.get("cards", [])}
    cards = []
    for card_id in block.get("cardIds", []):
        card = cards_by_id[card_id]
        rows = datasets.get(card["dataset"], [])
        row = rows[0] if rows else {}
        metrics = "".join(
            "<div class=\"metric\">"
            f"<span>{escape(metric['label'])}</span>"
            "<strong>"
            f"{escape(_format_value(row.get(metric['field']), metric.get('format')))}"
            "</strong>"
            "</div>"
            for metric in card.get("metrics", [])
        )
        cards.append(
            "<article class=\"metric-card\">"
            f"<p>{escape(card.get('description', ''))}</p>{metrics}"
            "</article>"
        )
    return f"<section class=\"metric-grid\">{''.join(cards)}</section>"


def _render_chart(block: dict, manifest: dict, datasets: dict) -> str:
    chart = next(
        item for item in manifest.get("charts", []) if item["id"] == block["chartId"]
    )
    rows = datasets.get(chart["dataset"], [])
    encodings = chart["encodings"]
    x_field = encodings["x"]["field"]
    y_field = encodings["y"]["field"]
    color_field = encodings.get("color", {}).get("field")
    values = [
        float(row[y_field])
        for row in rows
        if isinstance(row.get(y_field), (int, float))
    ]
    scale = max(values, default=1.0) or 1.0
    bars = []
    for row in rows:
        value = row.get(y_field)
        if not isinstance(value, (int, float)):
            continue
        label = str(row.get(x_field, ""))
        if color_field:
            label = f"{label} · {row.get(color_field, '')}"
        width = max(0.0, min(100.0, float(value) / scale * 100))
        bars.append(
            "<div class=\"bar-row\">"
            f"<span class=\"bar-label\">{escape(label)}</span>"
            "<span class=\"bar-track\">"
            f"<span class=\"bar-fill\" style=\"width:{width:.2f}%\"></span>"
            "</span>"
            "<strong>"
            f"{escape(_format_value(value, encodings['y'].get('format')))}"
            "</strong>"
            "</div>"
        )
    subtitle = chart.get("subtitle")
    return (
        "<section class=\"panel chart\">"
        f"<h2>{escape(chart['title'])}</h2>"
        f"{f'<p>{escape(subtitle)}</p>' if subtitle else ''}"
        f"{''.join(bars)}</section>"
    )


def _render_table(block: dict, manifest: dict, datasets: dict) -> str:
    table = next(
        item for item in manifest.get("tables", []) if item["id"] == block["tableId"]
    )
    rows = datasets.get(table["dataset"], [])
    columns = table.get("columns", [])
    header = "".join(f"<th>{escape(column['label'])}</th>" for column in columns)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(_format_value(row.get(column['field'])))}</td>"
            for column in columns
        )
        + "</tr>"
        for row in rows
    )
    return (
        "<details class=\"panel table-panel\">"
        f"<summary>{escape(table['title'])}</summary>"
        f"<p>{escape(table.get('subtitle', ''))}</p>"
        "<div class=\"table-scroll\"><table>"
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody>"
        "</table></div></details>"
    )


def render(artifact: dict) -> str:
    manifest = artifact["manifest"]
    datasets = artifact["snapshot"]["datasets"]
    rendered_blocks = []
    for block in manifest["blocks"]:
        block_type = block["type"]
        if block_type == "markdown":
            rendered_blocks.append(
                f"<section class=\"panel markdown\">{MARKDOWN.render(block['body'])}</section>"
            )
        elif block_type == "metric-strip":
            rendered_blocks.append(_render_metric_strip(block, manifest, datasets))
        elif block_type == "chart":
            rendered_blocks.append(_render_chart(block, manifest, datasets))
        elif block_type == "table":
            rendered_blocks.append(_render_table(block, manifest, datasets))
        else:
            raise ValueError(f"unsupported report block type: {block_type}")

    sources = "".join(
        "<li>"
        f"<strong>{escape(source.get('label', source.get('id', 'Source')))}</strong>"
        f"<p>{escape(source.get('path', ''))}</p>"
        f"<p>{escape(source.get('query', {}).get('description', ''))}</p>"
        "</li>"
        for source in manifest.get("sources", [])
    )
    generated_at = manifest.get("generatedAt") or artifact["snapshot"].get(
        "generatedAt", ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(manifest["title"])}</title>
<style>
:root{{--ink:#172033;--muted:#5d687a;--line:#d9e0e8;--panel:#fff;--bg:#f4f6f9;--accent:#4f67d9}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 system-ui,sans-serif}} main{{max-width:1180px;margin:auto;padding:28px}}
.toolbar{{position:sticky;top:0;z-index:2;background:rgba(244,246,249,.96);
padding:10px 0}} input{{width:100%;padding:11px 13px;border:1px solid var(--line);
border-radius:8px;font:inherit}} .panel,.metric-card{{background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:20px;margin:14px 0}}
.markdown:first-child{{padding-bottom:8px}} h1{{margin:0 0 8px;font-size:2rem}}
h2{{margin:0 0 10px;font-size:1.3rem}} p{{color:var(--muted)}} code{{color:#344bb6}}
.metric-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
gap:12px;margin:14px 0}} .metric-card{{margin:0}} .metric{{display:flex;
justify-content:space-between;gap:12px;margin-top:8px}} .metric span{{color:var(--muted)}}
.metric strong{{font-size:1.1rem}} .bar-row{{display:grid;
grid-template-columns:minmax(180px,1.3fr) minmax(180px,3fr) 70px;gap:12px;
align-items:center;margin:9px 0}} .bar-label{{color:var(--muted)}}
.bar-track{{height:13px;background:#e8ecf4;border-radius:999px;overflow:hidden}}
.bar-fill{{display:block;height:100%;background:var(--accent);border-radius:999px}}
details summary{{cursor:pointer;font-weight:700;font-size:1.05rem}}
.table-scroll{{overflow:auto;max-height:620px}} table{{border-collapse:collapse;width:100%;
font-size:13px}} th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left;
vertical-align:top}} th{{position:sticky;top:0;background:#edf1f7}} td:last-child{{min-width:320px}}
.sources{{margin-top:24px}} .meta{{color:var(--muted);font-size:13px}}
[hidden]{{display:none!important}} @media(max-width:700px){{main{{padding:16px}}
.bar-row{{grid-template-columns:1fr 64px}}.bar-label{{grid-column:1/-1}}}}
</style>
</head>
<body>
<main>
<div class="toolbar"><input id="search" type="search"
 placeholder="Filter report sections and question audits…" aria-label="Filter report"></div>
<div id="report">{''.join(rendered_blocks)}</div>
<section class="panel sources"><h2>Sources</h2><ol>{sources}</ol>
<p class="meta">Generated {escape(str(generated_at))} from the canonical artifact.</p></section>
</main>
<script>
const search=document.querySelector('#search');
search.addEventListener('input',()=>{{
 const query=search.value.trim().toLowerCase();
 document.querySelectorAll('#report > section, #report > details').forEach(section=>{{
  section.hidden=Boolean(query)&&!section.textContent.toLowerCase().includes(query);
 }});
}});
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact",
        type=Path,
        default=Path("evaluation/retrieval_comparison_artifact.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/retrieval_comparison.html"),
    )
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    args.output.write_text(render(artifact), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
