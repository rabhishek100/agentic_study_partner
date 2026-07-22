"""Build a searchable, self-contained review page for conversation gold data."""

import argparse
from html import escape
import json
from pathlib import Path

from scripts.validate_multiturn_gold import validate
from storage.database import connection as database_connection, resolve_owner_id


def _evidence(items, nodes):
    if not items:
        return "<p>None</p>"
    rows = "".join(
        "<tr>"
        f"<td>N{item['node_id']}</td>"
        f"<td>{escape(nodes[item['node_id']]['path_text'])}</td>"
        f"<td>{escape(', '.join(map(str, item.get('pages', []))))}</td>"
        f"<td>{escape(item.get('role', 'near miss'))}</td>"
        "</tr>"
        for item in items
    )
    return (
        "<table><thead><tr><th>Node</th><th>Hierarchy</th>"
        "<th>PDF pages</th><th>Role</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def build_html(gold, validation, connection):
    nodes = {
        row["id"]: dict(row)
        for row in connection.execute(
            "select * from nodes where owner_id = %s", (resolve_owner_id(),)
        )
    }
    cards = []
    routes = set()
    for conversation in gold["conversations"]:
        turns = []
        for turn in conversation["turns"]:
            routes.add(turn["expected_route"])
            search = escape(
                " ".join(
                    [
                        turn["turn_id"],
                        turn["user"],
                        turn["reference_answer"],
                    ]
                ).casefold()
            )
            turns.append(
                f"""<article id="{escape(turn["turn_id"])}" class="turn"
 data-route="{escape(turn["expected_route"])}" data-search="{search}">
<h3>{escape(turn["turn_id"])}: {escape(turn["user"])}</h3>
<dl><dt>Route</dt><dd>{escape(turn["expected_route"])}</dd>
<dt>History</dt><dd>{escape(turn["history_dependency"])}</dd>
<dt>Standalone query</dt><dd>{escape(turn.get("expected_standalone_query") or "—")}</dd>
<dt>Scope</dt><dd>{escape(str(turn.get("expected_scope") or "global"))}</dd></dl>
<h4>Reference answer</h4><p>{escape(turn["reference_answer"])}</p>
<h4>Expected evidence</h4>{_evidence(turn.get("expected_evidence", []), nodes)}
<details><summary>Near misses</summary>
{_evidence(turn.get("near_miss_evidence", []), nodes)}</details>
</article>"""
            )
        cards.append(
            f'<section id="{escape(conversation["id"])}">'
            f"<h2>{escape(conversation['title'])}</h2>{''.join(turns)}</section>"
        )
    options = "".join(
        f'<option value="{escape(route)}">{escape(route)}</option>'
        for route in sorted(routes)
    )
    status = "valid" if validation["valid"] else "invalid"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Conversation gold set</title><style>
body{{font:15px/1.5 system-ui;max-width:1100px;margin:auto;padding:28px;background:#f4f6f8}}
.turn{{background:white;border:1px solid #d9e0e6;border-radius:9px;padding:18px;margin:14px 0}}
.controls{{display:flex;gap:10px;position:sticky;top:0;background:#f4f6f8;padding:10px 0}}
input{{flex:1}}input,select{{padding:9px}}dl{{display:grid;grid-template-columns:130px 1fr}}
dd{{margin:0}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #ddd;padding:6px}}
</style></head><body><h1>Conversation gold set</h1>
<p><strong>Model-adjudicated, not human-verified.</strong>
 Canonical validation: {status}. {validation["turn_count"]} turns.</p>
<div class="controls"><input id="search" placeholder="Search">
<select id="route"><option value="">All routes</option>{options}</select></div>
{"".join(cards)}
<script>const s=document.querySelector('#search'),r=document.querySelector('#route');
function f(){{const q=s.value.toLowerCase();document.querySelectorAll('.turn').forEach(x=>
x.hidden=(r.value&&x.dataset.route!==r.value)||(q&&!x.dataset.search.includes(q)));}}
s.oninput=f;r.onchange=f;</script></body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gold-set", type=Path, default=Path("evaluation/multiturn_gold.json")
    )
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL")
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/multiturn_gold.html")
    )
    args = parser.parse_args()
    gold = json.loads(args.gold_set.read_text(encoding="utf-8"))
    with database_connection(args.database_url, readonly=True) as connection:
        validation = validate(gold, connection, allow_pending=False)
        if not validation["valid"]:
            raise SystemExit("\n".join(validation["errors"]))
        html = build_html(gold, validation, connection)
    args.output.write_text(html, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
