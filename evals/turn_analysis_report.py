"""Self-contained HTML report for isolated turn-analysis evaluation."""

import html
import json


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _badge(label: str, passed: bool | None) -> str:
    status = "na" if passed is None else "pass" if passed else "fail"
    return f'<span class="badge {status}">{_e(label)}</span>'


def _scope(value: dict | None) -> str:
    if value is None:
        return "Book-wide / none"
    path = value.get("display_path")
    suffix = f" · {path}" if path else ""
    return f"{value['kind']} N{value['node_id']}{suffix}"


def _state_update(value: dict) -> str:
    return (
        f"scope={value['active_scope']}; "
        f"clarification={value['pending_clarification']}"
    )


def _candidate_table(candidates: list[dict]) -> str:
    if not candidates:
        return '<p class="empty">No canonical candidates</p>'
    rows = "".join(
        "<tr>"
        f"<td>{index}</td>"
        f"<td><code>N{candidate['node_id']}</code></td>"
        f"<td>{_e(candidate['kind'])}</td>"
        f"<td>{_e(candidate['display_path'])}</td>"
        f"<td>{candidate['start_page']}–{candidate['end_page']}</td>"
        f"<td>{_e(candidate['match_reason'])}</td>"
        "</tr>"
        for index, candidate in enumerate(candidates, start=1)
    )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Rank</th><th>Node</th><th>Kind</th><th>Hierarchy</th>"
        "<th>PDF pages</th><th>Why included</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _query_judgment(value: dict | None) -> str:
    if value is None:
        return '<p class="empty">Not available because analysis failed.</p>'
    method = value["method"]
    preserves = value["preserves_meaning"]
    judgment = value.get("judgment")
    if judgment and "error" in judgment:
        detail = f'<p class="error">{_e(judgment["error"])}</p>'
    elif judgment:
        detail = (
            f"<p>{_e(judgment['explanation'])}</p>"
            "<p><strong>Missing concepts:</strong> "
            f"{_e(judgment['missing_concepts'] or 'None')}<br>"
            "<strong>Added assumptions:</strong> "
            f"{_e(judgment['added_assumptions'] or 'None')}</p>"
        )
    else:
        detail = '<p class="empty">No semantic model call was needed.</p>'
    return (
        f"<p><strong>Method:</strong> {_e(method)}; "
        f"<strong>meaning preserved:</strong> {_e(preserves)}; "
        f"<strong>presence correct:</strong> "
        f"{_e(value['presence_correct'])}</p>{detail}"
    )


def _turn_html(turn: dict) -> str:
    gold = turn["gold"]
    prediction = turn["prediction"]
    judgment = turn["judgment"]
    status = "pass" if judgment["all_components_correct"] else "fail"
    predicted = prediction or {
        "route": "analysis_error",
        "history_dependency": "—",
        "standalone_query": None,
        "scope_behavior": "—",
        "resolved_scope": None,
        "state_update": {
            "active_scope": "—",
            "pending_clarification": "—",
        },
        "clarification_question": None,
        "decision_reason": turn["analysis_error"] or "Unknown error",
        "decision_source": "—",
    }
    expected_update = {
        "active_scope": gold["state_update"],
        "pending_clarification": gold.get(
            "pending_clarification_update",
            "none",
        ),
    }
    trace = turn.get("trace")
    trace_html = '<span class="empty">No trace recorded</span>'
    if trace:
        trace_html = (
            f'<a href="{_e(trace.get("url", "#"))}">'
            f'{_e(trace.get("run_id", "LangSmith trace"))}</a>'
        )
    searchable = " ".join(
        [
            turn["turn_id"],
            gold["user"],
            gold["expected_route"],
            str(gold["expected_standalone_query"]),
            predicted["route"],
            str(predicted["standalone_query"]),
        ]
    ).casefold()
    error = (
        f'<p class="error"><strong>Analysis error:</strong> '
        f'{_e(turn["analysis_error"])}</p>'
        if turn["analysis_error"]
        else ""
    )
    return f"""
<section class="turn" id="{_e(turn['turn_id'])}" data-status="{status}"
 data-route="{_e(gold['expected_route'])}" data-search="{_e(searchable)}">
  <div class="turn-head">
    <strong>{_e(turn['turn_id'])}</strong>
    {_badge("route", judgment["route_correct"])}
    {_badge("dependency", judgment["history_dependency_correct"])}
    {_badge("scope", judgment["scope_correct"])}
    {_badge("behavior", judgment["scope_behavior_correct"])}
    {_badge("state", judgment["state_update_correct"])}
    {_badge("query", turn["query_meaning"]["preserves_meaning"] if turn["query_meaning"] else None)}
    <a href="#{_e(turn['turn_id'])}">link</a>
  </div>
  <div class="question"><span>User</span>{_e(gold['user'])}</div>
  {error}
  <div class="comparison">
    <div>
      <h4>Expected decision</h4>
      <dl>
        <dt>Route</dt><dd>{_e(gold['expected_route'])}</dd>
        <dt>Dependency</dt><dd>{_e(gold['history_dependency'])}</dd>
        <dt>Standalone meaning</dt><dd>{_e(gold['expected_standalone_query'])}</dd>
        <dt>Scope</dt><dd>{_e(_scope(gold['expected_scope']))}</dd>
        <dt>Scope behavior</dt><dd>{_e(gold['scope_behavior'])}</dd>
        <dt>State update</dt><dd>{_e(_state_update(expected_update))}</dd>
      </dl>
    </div>
    <div>
      <h4>Observed decision</h4>
      <dl>
        <dt>Route</dt><dd>{_e(predicted['route'])}</dd>
        <dt>Dependency</dt><dd>{_e(predicted['history_dependency'])}</dd>
        <dt>Standalone query</dt><dd>{_e(predicted['standalone_query'])}</dd>
        <dt>Scope</dt><dd>{_e(_scope(predicted['resolved_scope']))}</dd>
        <dt>Scope behavior</dt><dd>{_e(predicted['scope_behavior'])}</dd>
        <dt>State update</dt><dd>{_e(_state_update(predicted['state_update']))}</dd>
      </dl>
    </div>
  </div>
  <div class="decision">
    <strong>Decision source:</strong> {_e(predicted['decision_source'])}<br>
    <strong>Reason:</strong> {_e(predicted['decision_reason'])}<br>
    <strong>Clarification:</strong> {_e(predicted['clarification_question'])}
  </div>
  <details>
    <summary>Context, candidates, semantic judgment, and trace</summary>
    <h4>Gold pre-turn state</h4>
    <pre>{_e(json.dumps(turn['gold_state_before'], indent=2, ensure_ascii=False))}</pre>
    <h4>Canonical scope candidates</h4>
    {_candidate_table(turn['scope_candidates'])}
    <h4>Standalone-query meaning</h4>
    {_query_judgment(turn['query_meaning'])}
    <h4>Component checks</h4>
    <pre>{_e(json.dumps(judgment, indent=2, ensure_ascii=False))}</pre>
    <h4>LangSmith trace</h4>
    <p>{trace_html}</p>
  </details>
</section>
"""


def build_turn_analysis_html(run: dict) -> str:
    summary = run["summary"]
    metadata = run["run"]
    conversations = []
    for conversation in run["conversations"]:
        turns = "".join(_turn_html(turn) for turn in conversation["turns"])
        conversations.append(
            f"""
<article class="conversation">
  <header>
    <div>
      <span class="eyebrow">{_e(conversation['category'])}</span>
      <h2>{_e(conversation['conversation_id'])} · {_e(conversation['title'])}</h2>
    </div>
    <span>{len(conversation['turns'])} analyzed turns</span>
  </header>
  {turns}
</article>
"""
        )
    routes = sorted(
        {
            turn["gold"]["expected_route"]
            for conversation in run["conversations"]
            for turn in conversation["turns"]
        }
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>Turn analysis · {_e(metadata['run_id'])}</title>
<style>
:root {{ --bg:#f3f2ed; --paper:#fffdf8; --ink:#222722; --muted:#69736b;
 --line:#d8d8cf; --green:#286246; --green-bg:#e5f1e9; --red:#963e35;
 --red-bg:#fae8e5; --blue:#2d5976; --blue-bg:#e7f0f6; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--bg);
 font:15px/1.5 Inter,ui-sans-serif,system-ui,sans-serif; }}
a {{ color:var(--blue); }} .shell {{ width:min(1240px,calc(100% - 32px)); margin:auto; }}
.hero {{ padding:48px 0 38px; color:#f7f8f4; background:#17251d; }}
.hero h1 {{ margin:6px 0; font-size:clamp(32px,5vw,58px); letter-spacing:-.04em; }}
.hero p {{ color:#c9d6cc; max-width:850px; }} .eyebrow {{ color:#7fa68a;
 text-transform:uppercase; font-size:11px; font-weight:800; letter-spacing:.12em; }}
.metrics {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin-top:-18px; }}
.metric {{ padding:16px; background:var(--paper); border:1px solid var(--line);
 border-radius:11px; box-shadow:0 5px 18px #17251d12; }}
.metric strong {{ display:block; font-size:24px; }} .metric span {{ color:var(--muted); font-size:11px; }}
.panel,.conversation {{ margin:26px 0; background:var(--paper); border:1px solid var(--line); border-radius:13px; }}
.panel {{ padding:22px; }} .comparison {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
dl {{ display:grid; grid-template-columns:150px 1fr; gap:6px 12px; }}
dt {{ color:var(--muted); }} dd {{ margin:0; overflow-wrap:anywhere; }}
.filters {{ position:sticky; top:0; z-index:5; padding:12px 0; background:#f3f2edef;
 backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }}
.filter-grid {{ display:grid; grid-template-columns:2fr 1fr 1fr; gap:10px; }}
input,select {{ padding:10px 12px; width:100%; border:1px solid #bcc2ba;
 border-radius:8px; background:var(--paper); }}
.conversation>header {{ display:flex; justify-content:space-between; gap:16px; padding:20px 24px;
 border-bottom:1px solid var(--line); background:#faf9f3; }}
.conversation h2 {{ margin:3px 0 0; font-size:22px; }}
.turn {{ margin:20px 24px; padding:18px; border:1px solid var(--line); border-radius:11px; }}
.turn-head {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; }}
.turn-head a {{ margin-left:auto; }} .badge {{ padding:2px 7px; border-radius:99px;
 font-size:11px; font-weight:800; }} .badge.pass {{ color:var(--green); background:var(--green-bg); }}
.badge.fail {{ color:var(--red); background:var(--red-bg); }} .badge.na {{ color:var(--muted); background:#ecece6; }}
.question {{ display:grid; grid-template-columns:120px 1fr; gap:12px; margin:13px 0;
 padding:13px; background:#edf2ed; border-radius:8px; }}
.question span {{ color:var(--muted); font-size:11px; font-weight:800; text-transform:uppercase; }}
.comparison>div,.decision {{ min-width:0; padding:14px; background:#f7f5ee; border-radius:8px; }}
.decision {{ margin-top:12px; }} details {{ margin-top:14px; }}
summary {{ color:var(--blue); cursor:pointer; font-weight:800; }}
.table-wrap {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th,td {{ padding:7px; text-align:left; vertical-align:top; border-bottom:1px solid var(--line); }}
th {{ color:var(--muted); }} .error {{ color:var(--red); }}
.empty {{ color:var(--muted); font-style:italic; }}
pre {{ overflow:auto; max-height:430px; padding:12px; background:#17251d; color:#e5eee7;
 border-radius:8px; font-size:11px; }} .hidden {{ display:none!important; }}
footer {{ padding:28px; color:var(--muted); text-align:center; }}
@media(max-width:900px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }}
 .comparison,.filter-grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header class="hero"><div class="shell">
  <span class="eyebrow">Batch 2C · isolated gold-state evaluation</span>
  <h1>Conversational turn analysis</h1>
  <p>Measures question understanding before retrieval or answer generation.
  Earlier context is reconstructed from expected state, so failures do not cascade.</p>
</div></header>
<main>
<div class="shell metrics">
  <div class="metric"><strong>{summary['scored_turn_count']}</strong><span>Turns</span></div>
  <div class="metric"><strong>{_percent(summary['route_accuracy'])}</strong><span>Route</span></div>
  <div class="metric"><strong>{_percent(summary['history_dependency_accuracy'])}</strong><span>Dependency</span></div>
  <div class="metric"><strong>{_percent(summary['scope_accuracy'])}</strong><span>Scope</span></div>
  <div class="metric"><strong>{_percent(summary['state_update_accuracy'])}</strong><span>State update</span></div>
  <div class="metric"><strong>{_percent(summary['semantic_query_preservation_rate'])}</strong><span>Query meaning</span></div>
</div>
<div class="shell">
  <section class="panel">
    <h2>Run configuration</h2>
    <dl>
      <dt>Run</dt><dd>{_e(metadata['run_id'])}</dd>
      <dt>Generated</dt><dd>{_e(metadata['generated_at'])}</dd>
      <dt>Control model</dt><dd>{_e(metadata['model'])}</dd>
      <dt>Reasoning</dt><dd>{_e(metadata['reasoning'])}</dd>
      <dt>LangSmith project</dt><dd>{_e(metadata['langsmith_project'])}</dd>
      <dt>Analysis errors</dt><dd>{summary['analysis_error_count']}</dd>
      <dt>Invalid scopes</dt><dd>{summary['invalid_scope_count']}</dd>
      <dt>All components</dt><dd>{_percent(summary['all_components_accuracy'])}</dd>
    </dl>
  </section>
</div>
<div class="filters"><div class="shell filter-grid">
  <input id="search" type="search" placeholder="Search turns and queries…">
  <select id="status"><option value="">Pass and fail</option><option value="pass">Pass</option><option value="fail">Fail</option></select>
  <select id="route"><option value="">All expected routes</option>
    {''.join(f'<option>{_e(route)}</option>' for route in routes)}
  </select>
</div></div>
<div class="shell">{''.join(conversations)}</div>
</main>
<footer>Gold-state component evaluation · no retrieval or answer generation</footer>
<script>
const controls=[...document.querySelectorAll("#search,#status,#route")];
function filter(){{
 const q=document.querySelector("#search").value.trim().toLowerCase();
 const status=document.querySelector("#status").value;
 const route=document.querySelector("#route").value;
 document.querySelectorAll(".turn").forEach(turn=>{{
  const visible=(!q||turn.dataset.search.includes(q))
   &&(!status||turn.dataset.status===status)&&(!route||turn.dataset.route===route);
  turn.classList.toggle("hidden",!visible);
 }});
 document.querySelectorAll(".conversation").forEach(item=>{{
  item.classList.toggle("hidden",![...item.querySelectorAll(".turn")]
   .some(turn=>!turn.classList.contains("hidden")));
 }});
}}
controls.forEach(control=>control.addEventListener("input",filter));
</script>
</body>
</html>"""
