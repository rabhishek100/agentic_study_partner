"""Build a self-contained HTML review view for the multi-turn gold set."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sqlite3

from scripts.validate_multiturn_gold import validate


CITATION = re.compile(r"\[N(\d+):P(\d+)]")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the offline multi-turn gold-set review page."
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/multiturn_gold.json"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/books.sqlite3"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/multiturn_gold.html"),
    )
    return parser


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _reference_answer(value: str) -> str:
    escaped = _e(value).replace("\n", "<br>")
    return CITATION.sub(
        lambda match: (
            f'<span class="citation">N{match.group(1)} · '
            f'PDF {match.group(2)}</span>'
        ),
        escaped,
    )


def _nodes(connection: sqlite3.Connection) -> dict[int, dict]:
    return {
        row["id"]: dict(row)
        for row in connection.execute(
            """
            SELECT id, title, path_text, node_type, start_page, end_page
            FROM nodes
            ORDER BY toc_index
            """
        )
    }


def _evidence_table(evidence: list[dict], nodes: dict[int, dict]) -> str:
    if not evidence:
        return '<p class="empty">None</p>'
    rows = []
    for item in evidence:
        node = nodes[item["node_id"]]
        role = item.get("role", "near miss")
        reason = item.get("reason_not_sufficient", "")
        rows.append(
            "<tr>"
            f"<td><code>N{item['node_id']}</code></td>"
            f"<td>{_e(node['path_text'])}</td>"
            f"<td>{_e(', '.join(map(str, item['pages'])))}</td>"
            f"<td>{_e(role)}</td>"
            f"<td>{_e(reason)}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Node</th><th>Canonical hierarchy</th><th>PDF pages</th>"
        "<th>Role</th><th>Judgment</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _turn_html(turn: dict, nodes: dict[int, dict], ordinal: int) -> str:
    outcome = (
        "answer"
        if turn["answerable"]
        else "clarify"
        if turn["expected_route"] == "clarify"
        else "abstain"
    )
    scope = turn["expected_scope"]
    scope_text = (
        "Book-wide / no hard scope"
        if scope is None
        else f"{scope['kind'].title()} · N{scope['node_id']} · "
        f"{nodes[scope['node_id']]['path_text']}"
    )
    outline = turn.get("expected_outline_node_ids")
    outline_html = ""
    if outline:
        outline_html = (
            "<dt>Expected outline order</dt><dd>"
            + " → ".join(
                f"N{node_id} {_e(nodes[node_id]['title'])}"
                for node_id in outline
            )
            + "</dd>"
        )
    standalone = turn["expected_standalone_query"] or "No rewrite/search query"
    dependencies = ", ".join(turn.get("depends_on_turn_ids", [])) or "None"
    clarification_update = turn.get(
        "pending_clarification_update",
        "none",
    )
    return f"""
<section class="turn" id="{_e(turn['turn_id'])}"
  data-route="{_e(turn['expected_route'])}"
  data-dependency="{_e(turn['history_dependency'])}"
  data-outcome="{outcome}">
  <div class="turn-head">
    <span class="turn-number">Turn {ordinal}</span>
    <span class="badge route">{_e(turn['expected_route'])}</span>
    <span class="badge dependency">{_e(turn['history_dependency'])}</span>
    <span class="badge outcome {outcome}">{outcome}</span>
    <a class="anchor" href="#{_e(turn['turn_id'])}">#{_e(turn['turn_id'])}</a>
  </div>
  <div class="message user-message">
    <div class="message-label">User</div>
    <div>{_e(turn['user'])}</div>
  </div>
  <div class="message answer-message">
    <div class="message-label">Reference response</div>
    <div>{_reference_answer(turn['reference_answer'])}</div>
  </div>
  <details>
    <summary>Expected routing, state, and evidence</summary>
    <dl class="facts">
      <dt>Standalone meaning</dt><dd>{_e(standalone)}</dd>
      <dt>Scope behavior</dt><dd>{_e(turn['scope_behavior'])}</dd>
      <dt>Resolved scope</dt><dd>{_e(scope_text)}</dd>
      <dt>Depends on</dt><dd>{_e(dependencies)}</dd>
      <dt>Active-scope update</dt><dd>{_e(turn['state_update'])}</dd>
      <dt>Clarification update</dt><dd>{_e(clarification_update)}</dd>
      <dt>Answerable</dt><dd>{str(turn['answerable']).lower()}</dd>
      {outline_html}
    </dl>
    <h4>Expected evidence</h4>
    {_evidence_table(turn['expected_evidence'], nodes)}
    <h4>Near-miss evidence</h4>
    {_evidence_table(turn['near_miss_evidence'], nodes)}
  </details>
</section>
"""


def build_html(
    gold: dict,
    validation: dict,
    connection: sqlite3.Connection,
) -> str:
    nodes = _nodes(connection)
    route_counts = Counter(
        turn["expected_route"]
        for conversation in gold["conversations"]
        for turn in conversation["turns"]
    )
    category_counts = Counter(
        conversation["category"] for conversation in gold["conversations"]
    )
    reviewer_rows = "".join(
        "<tr>"
        f"<td>{_e(reviewer['reviewer_id'])}</td>"
        f"<td>{_e(reviewer['role'])}</td>"
        f"<td>{_e(reviewer['decision'])}</td>"
        f"<td>{_e(reviewer['summary'])}</td>"
        "</tr>"
        for reviewer in gold["review"]["reviewers"]
    )
    route_rows = "".join(
        f"<tr><td>{_e(route)}</td><td>{count}</td></tr>"
        for route, count in sorted(route_counts.items())
    )
    category_rows = "".join(
        f"<tr><td>{_e(category)}</td><td>{count}</td></tr>"
        for category, count in sorted(category_counts.items())
    )
    conversations = []
    for conversation in gold["conversations"]:
        searchable = " ".join(
            [
                conversation["id"],
                conversation["title"],
                conversation["category"],
                *conversation["tags"],
                *[
                    turn["user"] + " " + turn["reference_answer"]
                    for turn in conversation["turns"]
                ],
            ]
        ).casefold()
        turns = "".join(
            _turn_html(turn, nodes, ordinal)
            for ordinal, turn in enumerate(conversation["turns"], start=1)
        )
        conversations.append(
            f"""
<article class="conversation" id="{_e(conversation['id'])}"
  data-search="{_e(searchable)}">
  <header class="conversation-head">
    <div>
      <div class="eyebrow">{_e(conversation['category'])}</div>
      <h2>{_e(conversation['title'])}</h2>
      <div class="tags">{''.join(f'<span>{_e(tag)}</span>' for tag in conversation['tags'])}</div>
    </div>
    <div class="conversation-meta">
      <strong>{len(conversation['turns'])}</strong> turns
      <span class="review accepted">{_e(conversation['review_status'])}</span>
      <a href="#{_e(conversation['id'])}">#{_e(conversation['id'])}</a>
    </div>
  </header>
  {turns}
</article>
"""
        )

    raw_json = _e(json.dumps(gold, ensure_ascii=False, indent=2))
    generated_at = datetime.now(timezone.utc).isoformat()
    source_hash = gold["book"]["source_file_sha256"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22><text y=%2214%22>✓</text></svg>">
<title>Multi-turn Gold Set Review</title>
<style>
:root {{
  --bg: #f4f3ee; --paper: #fffdf8; --ink: #20231f; --muted: #667067;
  --line: #d8d8cf; --green: #286246; --green-soft: #e6f1e9;
  --blue: #2d5976; --blue-soft: #e7f0f6; --amber: #8a5a12;
  --amber-soft: #fff0cf; --red: #8c3a32; --red-soft: #f8e8e5;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: var(--ink); background: var(--bg);
  font: 15px/1.55 Inter, ui-sans-serif, system-ui, sans-serif; }}
a {{ color: var(--blue); }}
.shell {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; }}
.hero {{ color: #f7f8f4; background: #17251d; padding: 56px 0 42px; }}
.hero h1 {{ max-width: 820px; margin: 8px 0 14px; font-size: clamp(34px, 6vw, 64px);
  line-height: .98; letter-spacing: -.045em; }}
.hero p {{ max-width: 780px; color: #c9d6cc; font-size: 17px; }}
.eyebrow {{ color: #8fb89c; font-size: 12px; font-weight: 800;
  letter-spacing: .12em; text-transform: uppercase; }}
.warning {{ margin-top: 26px; padding: 13px 16px; color: #fff2cb;
  border: 1px solid #826f35; background: #3a321c; border-radius: 10px; }}
.metrics {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px;
  margin-top: -20px; position: relative; }}
.metric {{ padding: 18px; background: var(--paper); border: 1px solid var(--line);
  border-radius: 12px; box-shadow: 0 5px 22px #17251d12; }}
.metric strong {{ display: block; font-size: 28px; }}
.metric span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
.section {{ margin: 32px 0; padding: 26px; background: var(--paper);
  border: 1px solid var(--line); border-radius: 14px; }}
.section h2 {{ margin-top: 0; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }}
.provenance {{ display: grid; grid-template-columns: 180px 1fr; gap: 8px 18px; }}
.provenance dt {{ color: var(--muted); }}
.provenance dd {{ margin: 0; overflow-wrap: anywhere; }}
.filters {{ position: sticky; top: 0; z-index: 5; padding: 14px 0;
  background: color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter: blur(12px); border-bottom: 1px solid var(--line); }}
.filter-row {{ display: grid; grid-template-columns: 2fr repeat(3, 1fr); gap: 10px; }}
input, select {{ width: 100%; padding: 11px 12px; color: var(--ink);
  background: var(--paper); border: 1px solid #bfc4bc; border-radius: 8px; }}
.conversation {{ margin: 28px 0; background: var(--paper);
  border: 1px solid var(--line); border-radius: 15px; overflow: hidden; }}
.conversation-head {{ display: flex; justify-content: space-between; gap: 20px;
  padding: 24px 26px; border-bottom: 1px solid var(--line); background: #faf9f3; }}
.conversation-head h2 {{ margin: 3px 0 10px; font-size: 25px; }}
.conversation-meta {{ min-width: 130px; text-align: right; color: var(--muted); }}
.conversation-meta strong {{ color: var(--ink); font-size: 24px; }}
.tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.tags span {{ padding: 3px 8px; color: var(--muted); background: #ecece5;
  border-radius: 999px; font-size: 12px; }}
.review {{ display: block; margin: 6px 0; font-size: 12px; font-weight: 800; }}
.accepted {{ color: var(--green); }}
.turn {{ margin: 22px 26px; padding: 20px; border: 1px solid var(--line);
  border-radius: 12px; }}
.turn-head {{ display: flex; align-items: center; flex-wrap: wrap; gap: 7px;
  margin-bottom: 14px; }}
.turn-number {{ margin-right: 4px; font-weight: 850; }}
.badge {{ padding: 3px 8px; border-radius: 999px; font-size: 11px;
  font-weight: 800; letter-spacing: .02em; }}
.route {{ color: var(--blue); background: var(--blue-soft); }}
.dependency {{ color: var(--amber); background: var(--amber-soft); }}
.outcome.answer {{ color: var(--green); background: var(--green-soft); }}
.outcome.abstain, .outcome.clarify {{ color: var(--red); background: var(--red-soft); }}
.anchor {{ margin-left: auto; font-size: 12px; }}
.message {{ display: grid; grid-template-columns: 130px 1fr; gap: 14px;
  padding: 14px; border-radius: 9px; }}
.message + .message {{ margin-top: 9px; }}
.user-message {{ background: #edf2ed; }}
.answer-message {{ background: #f4f1e8; }}
.message-label {{ color: var(--muted); font-size: 12px; font-weight: 850;
  text-transform: uppercase; letter-spacing: .07em; }}
.citation {{ display: inline-block; padding: 1px 6px; white-space: nowrap;
  color: var(--blue); background: var(--blue-soft); border-radius: 5px;
  font-family: ui-monospace, monospace; font-size: 12px; }}
details {{ margin-top: 14px; }}
summary {{ cursor: pointer; color: var(--blue); font-weight: 750; }}
.facts {{ display: grid; grid-template-columns: 180px 1fr; gap: 6px 14px;
  padding: 12px 0; }}
.facts dt {{ color: var(--muted); }}
.facts dd {{ margin: 0; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 8px 10px; text-align: left; vertical-align: top;
  border-bottom: 1px solid var(--line); }}
th {{ color: var(--muted); background: #f1f1eb; }}
.empty {{ color: var(--muted); font-style: italic; }}
.hidden {{ display: none !important; }}
pre {{ max-height: 520px; overflow: auto; padding: 18px; color: #dce8df;
  background: #152019; border-radius: 10px; font-size: 12px; }}
.footer {{ padding: 30px 0 50px; color: var(--muted); text-align: center; }}
@media (max-width: 800px) {{
  .metrics {{ grid-template-columns: repeat(2, 1fr); }}
  .grid-2, .filter-row {{ grid-template-columns: 1fr; }}
  .conversation-head, .message {{ display: block; }}
  .conversation-meta {{ margin-top: 12px; text-align: left; }}
  .message-label {{ margin-bottom: 6px; }}
}}
@media print {{
  body {{ background: white; }}
  .filters {{ display: none; }}
  .conversation, .section, .metric {{ break-inside: avoid; box-shadow: none; }}
  details {{ display: block; }}
  details > * {{ display: block; }}
}}
</style>
</head>
<body>
<header class="hero">
  <div class="shell">
    <div class="eyebrow">Evaluation · Frozen seed v1</div>
    <h1>Multi-turn conversation gold set</h1>
    <p>Inspectable routing, scope, evidence, abstention, and reference-answer
    judgments for <em>{_e(gold['book']['title'])}</em>.</p>
    <div class="warning"><strong>Model-adjudicated, not human-verified.</strong>
    Independent subagents reviewed the judgments, but their runtime model identity
    was not selectable or independently verifiable. Treat this as a frozen
    implementation baseline and manually spot-check before external claims.</div>
  </div>
</header>
<main>
  <div class="shell metrics">
    <div class="metric"><strong>{validation['conversation_count']}</strong><span>Conversations</span></div>
    <div class="metric"><strong>{validation['turn_count']}</strong><span>Evaluated turns</span></div>
    <div class="metric"><strong>{validation['answerable_count']}</strong><span>Answerable</span></div>
    <div class="metric"><strong>{validation['unanswerable_count']}</strong><span>Abstain / clarify</span></div>
    <div class="metric"><strong>{validation['history_dependency_counts']['dependent']}</strong><span>History-dependent</span></div>
  </div>
  <div class="shell">
    <section class="section">
      <h2>Provenance and validation</h2>
      <dl class="provenance">
        <dt>Set ID</dt><dd>{_e(gold['set_id'])}</dd>
        <dt>Status</dt><dd>{_e(gold['status'])} · {_e(gold['review']['status'])}</dd>
        <dt>Source</dt><dd>{_e(gold['book']['source_filename'])}</dd>
        <dt>SHA-256</dt><dd><code>{_e(source_hash)}</code></dd>
        <dt>Parser</dt><dd>{_e(gold['book']['parser_version'])}</dd>
        <dt>Validation</dt><dd>{'Passed' if validation['valid'] else 'Failed'} with {len(validation['errors'])} errors</dd>
        <dt>Generated</dt><dd>{_e(generated_at)}</dd>
      </dl>
    </section>
    <div class="grid-2">
      <section class="section">
        <h2>Route coverage</h2>
        <table><thead><tr><th>Expected route</th><th>Turns</th></tr></thead>
        <tbody>{route_rows}</tbody></table>
      </section>
      <section class="section">
        <h2>Scenario coverage</h2>
        <table><thead><tr><th>Conversation category</th><th>Count</th></tr></thead>
        <tbody>{category_rows}</tbody></table>
      </section>
    </div>
    <section class="section">
      <h2>Independent review decisions</h2>
      <table><thead><tr><th>Reviewer</th><th>Role</th><th>Decision</th><th>Summary</th></tr></thead>
      <tbody>{reviewer_rows}</tbody></table>
    </section>
  </div>
  <div class="filters">
    <div class="shell filter-row">
      <input id="search" type="search" placeholder="Search questions, answers, tags…">
      <select id="route"><option value="">All routes</option>{''.join(f'<option>{_e(route)}</option>' for route in sorted(route_counts))}</select>
      <select id="dependency"><option value="">Any dependency</option><option>independent</option><option>dependent</option><option>ambiguous</option></select>
      <select id="outcome"><option value="">Any outcome</option><option>answer</option><option>abstain</option><option>clarify</option></select>
    </div>
  </div>
  <div class="shell" id="conversations">
    {''.join(conversations)}
    <section class="section">
      <details>
        <summary>Raw frozen JSON</summary>
        <pre>{raw_json}</pre>
      </details>
    </section>
  </div>
</main>
<footer class="footer">Generated from evaluation/multiturn_gold.json · no external assets</footer>
<script>
const controls = ["search", "route", "dependency", "outcome"].map(id => document.getElementById(id));
function applyFilters() {{
  const q = document.getElementById("search").value.trim().toLowerCase();
  const route = document.getElementById("route").value;
  const dependency = document.getElementById("dependency").value;
  const outcome = document.getElementById("outcome").value;
  document.querySelectorAll(".conversation").forEach(conversation => {{
    let visibleTurns = 0;
    conversation.querySelectorAll(".turn").forEach(turn => {{
      const match = (!route || turn.dataset.route === route)
        && (!dependency || turn.dataset.dependency === dependency)
        && (!outcome || turn.dataset.outcome === outcome)
        && (!q || conversation.dataset.search.includes(q));
      turn.classList.toggle("hidden", !match);
      if (match) visibleTurns += 1;
    }});
    conversation.classList.toggle("hidden", visibleTurns === 0);
  }});
}}
controls.forEach(control => control.addEventListener("input", applyFilters));
</script>
</body>
</html>
"""


def main() -> None:
    args = build_argument_parser().parse_args()
    gold = json.loads(args.gold_set.read_text(encoding="utf-8"))
    connection = sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        validation = validate(gold, connection, allow_pending=False)
        if not validation["valid"]:
            raise ValueError(
                "gold-set validation failed: "
                + "; ".join(validation["errors"])
            )
        rendered = build_html(gold, validation, connection)
    finally:
        connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
