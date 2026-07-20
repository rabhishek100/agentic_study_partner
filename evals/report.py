"""Self-contained HTML report for one multi-turn evaluation run."""

import html
import json

from markdown_it import MarkdownIt


_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
    },
).enable("table")


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _answer(value: str) -> str:
    return _MARKDOWN.render(value)


def _badge(label: str, passed: bool | None) -> str:
    status = "na" if passed is None else "pass" if passed else "fail"
    return f'<span class="badge {status}">{_e(label)}</span>'


def _evidence_rows(items: list[dict], *, expected: bool) -> str:
    if not items:
        return '<p class="empty">None</p>'
    rows = []
    for index, item in enumerate(items, start=1):
        if expected:
            node_id = item["node_id"]
            pages = ", ".join(map(str, item["pages"]))
            role = item["role"]
            path = ""
            rank = ""
        else:
            node_id = item["node_id"]
            pages = ", ".join(map(str, item["pages"]))
            role = item.get("retrieval_method") or "scope"
            path = item["path"]
            rank = item.get("rank") or index
        rows.append(
            "<tr>"
            f"<td>{_e(rank)}</td><td><code>N{node_id}</code></td>"
            f"<td>{_e(path)}</td><td>{_e(pages)}</td><td>{_e(role)}</td>"
            "</tr>"
        )
    return (
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>Rank</th><th>Node</th><th>Path</th><th>PDF pages</th>"
        "<th>Role/method</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _turn_html(turn: dict) -> str:
    gold = turn["gold"]
    prediction = turn["prediction"]
    judgment = turn["judgment"]
    failed = [
        field
        for field in (
            "route_correct",
            "history_dependency_correct",
            "scope_correct",
            "scope_behavior_correct",
            "state_update_correct",
            "outcome_correct",
        )
        if not judgment[field]
    ]
    safety = judgment["safety_violations"]
    status = "fail" if failed or safety else "pass"
    searchable = " ".join(
        [
            turn["turn_id"],
            gold["user"],
            gold["reference_answer"],
            prediction["answer"],
            gold["expected_route"],
            prediction["route"],
        ]
    ).casefold()
    expected_scope = gold["expected_scope"]
    predicted_scope = prediction["resolved_scope"]
    expected_scope_text = (
        "Book-wide / none"
        if expected_scope is None
        else f"{expected_scope['kind']} N{expected_scope['node_id']}"
    )
    predicted_scope_text = (
        "Book-wide / none"
        if predicted_scope is None
        else (
            f"{predicted_scope['kind']} N{predicted_scope['node_id']} · "
            f"{predicted_scope['display_path']}"
        )
    )
    quality = turn["answer_quality"]
    if quality is None:
        quality_html = '<p class="empty">Not judged</p>'
    elif "error" in quality:
        quality_html = f'<p class="error">{_e(quality["error"])}</p>'
    else:
        unsupported = quality["unsupported_claims"]
        quality_html = (
            '<div class="quality">'
            f"<span>Correctness <strong>{quality['correctness']}/4</strong></span>"
            "<span>Required-point coverage "
            f"<strong>{quality['required_point_coverage']}/4</strong></span>"
            f"<span>Usefulness <strong>{quality['usefulness']}/4</strong></span>"
            "</div>"
            f"<p>{_e(quality['explanation'])}</p>"
            "<p><strong>Unsupported claims:</strong> "
            + (
                _e("; ".join(unsupported))
                if unsupported
                else "None identified"
            )
            + "</p>"
        )
    trace = turn.get("trace")
    trace_html = '<span class="empty">No trace recorded</span>'
    if trace:
        trace_html = (
            f'<a href="{_e(trace.get("url", "#"))}">'
            f'{_e(trace.get("run_id", "LangSmith trace"))}</a>'
        )
    oracle = turn.get("oracle_retrieval")
    if oracle is None or oracle["judgment"] is None:
        oracle_html = '<p class="empty">Not applicable or not run</p>'
    else:
        oracle_judgment = oracle["judgment"]
        oracle_html = (
            f"<p><strong>Gold query:</strong> "
            f"{_e(oracle_judgment['query'])}<br>"
            f"<strong>Required recall:</strong> "
            f"{_percent(oracle_judgment['required_evidence_recall'])}<br>"
            f"<strong>Method note:</strong> "
            f"{_e(oracle_judgment['note'])}</p>"
            + _evidence_rows(oracle["evidence"], expected=False)
        )
    return f"""
<section class="turn" id="{_e(turn['turn_id'])}" data-status="{status}"
 data-route="{_e(gold['expected_route'])}" data-search="{_e(searchable)}">
  <div class="turn-head">
    <strong>{_e(turn['turn_id'])}</strong>
    {_badge("scored" if turn["scored"] else "setup", turn["scored"])}
    {_badge("route", judgment["route_correct"])}
    {_badge("scope", judgment["scope_correct"])}
    {_badge("state", judgment["state_update_correct"])}
    {_badge("outcome", judgment["outcome_correct"])}
    <a href="#{_e(turn['turn_id'])}">link</a>
  </div>
  <div class="question"><span>User</span>{_e(gold['user'])}</div>
  <div class="comparison">
    <div>
      <h4>Gold</h4>
      <dl>
        <dt>Route</dt><dd>{_e(gold['expected_route'])}</dd>
        <dt>Dependency</dt><dd>{_e(gold['history_dependency'])}</dd>
        <dt>Standalone meaning</dt><dd>{_e(gold['expected_standalone_query'])}</dd>
        <dt>Scope</dt><dd>{_e(expected_scope_text)}</dd>
        <dt>Scope behavior</dt><dd>{_e(gold['scope_behavior'])}</dd>
        <dt>State update</dt><dd>{_e(gold['state_update'])}</dd>
      </dl>
    </div>
    <div>
      <h4>Predicted</h4>
      <dl>
        <dt>Route</dt><dd>{_e(prediction['route'])}</dd>
        <dt>Dependency</dt><dd>{_e(prediction['history_dependency'])}</dd>
        <dt>Standalone query</dt><dd>{_e(prediction['standalone_query'])}</dd>
        <dt>Scope</dt><dd>{_e(predicted_scope_text)}</dd>
        <dt>Scope behavior</dt><dd>{_e(prediction['scope_behavior'])}</dd>
        <dt>State update</dt><dd>{_e(prediction['state_update']['active_scope'])}</dd>
      </dl>
    </div>
  </div>
  <div class="comparison answers">
    <div><h4>Reference response</h4><div class="markdown">{_answer(gold['reference_answer'])}</div></div>
    <div><h4>Generated response</h4><div class="markdown">{_answer(prediction['answer'])}</div></div>
  </div>
  <details>
    <summary>Evidence, safety, state, and judging</summary>
    <div class="comparison">
      <div><h4>Expected evidence</h4>{_evidence_rows(gold['expected_evidence'], expected=True)}</div>
      <div><h4>Observed evidence</h4>{_evidence_rows(prediction['evidence'], expected=False)}</div>
    </div>
    <h4>Retrieval judgment</h4>
    <p>Required recall: {_percent(judgment['required_evidence_recall'])};
      missing nodes: {_e(judgment['missing_required_nodes'])};
      citation coverage: {_percent(judgment['citation_required_node_coverage'])}.
    </p>
    <h4>Oracle-query retrieval isolation</h4>
    {oracle_html}
    <h4>Safety invariants</h4>
    <p class="{'error' if safety else 'success'}">{_e('; '.join(safety) if safety else 'No violations detected.')}</p>
    <h4>Semantic answer judgment</h4>
    {quality_html}
    <h4>State snapshots</h4>
    <div class="comparison">
      <pre>{_e(json.dumps(turn['state_before'], indent=2))}</pre>
      <pre>{_e(json.dumps(turn['state_after'], indent=2))}</pre>
    </div>
    <h4>Trace</h4><p>{trace_html}</p>
  </details>
</section>
"""


def build_html(run: dict) -> str:
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
    <span>{len(conversation['turns'])} replayed turns</span>
  </header>
  {turns}
</article>
"""
        )
    answer_quality = summary.get("answer_quality", {})
    oracle_metrics = summary["oracle_query_retrieval"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22><text y=%2214%22>✓</text></svg>">
<title>Multi-turn baseline · {_e(metadata['run_id'])}</title>
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
.metrics {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin-top:-18px; }}
.metric {{ padding:16px; background:var(--paper); border:1px solid var(--line);
 border-radius:11px; box-shadow:0 5px 18px #17251d12; }}
.metric strong {{ display:block; font-size:25px; }} .metric span {{ color:var(--muted); font-size:11px; }}
.panel,.conversation {{ margin:26px 0; background:var(--paper); border:1px solid var(--line); border-radius:13px; }}
.panel {{ padding:22px; }} .run-grid,.comparison {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
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
.comparison>div {{ min-width:0; padding:14px; background:#f7f5ee; border-radius:8px; }}
.answers {{ margin-top:12px; }} h4 {{ margin:0 0 9px; }} details {{ margin-top:14px; }}
summary {{ color:var(--blue); cursor:pointer; font-weight:800; }}
.table-wrap {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th,td {{ padding:7px; text-align:left; vertical-align:top; border-bottom:1px solid var(--line); }}
th {{ color:var(--muted); }} .quality {{ display:flex; gap:9px; flex-wrap:wrap; }}
.quality span {{ padding:6px 9px; background:var(--blue-bg); border-radius:7px; }}
.error {{ color:var(--red); }} .success {{ color:var(--green); }} .empty {{ color:var(--muted); font-style:italic; }}
.markdown > :first-child {{ margin-top:0; }} .markdown > :last-child {{ margin-bottom:0; }}
.markdown h1 {{ font-size:22px; }} .markdown h2 {{ font-size:19px; }}
.markdown h3 {{ font-size:16px; }} .markdown li + li {{ margin-top:4px; }}
.markdown code {{ padding:1px 4px; background:#e9e7df; border-radius:4px; }}
.markdown blockquote {{ margin-left:0; padding-left:12px; color:var(--muted);
 border-left:3px solid var(--line); }}
pre {{ overflow:auto; max-height:420px; padding:12px; background:#17251d; color:#e5eee7;
 border-radius:8px; font-size:11px; }} .hidden {{ display:none!important; }}
footer {{ padding:28px; color:var(--muted); text-align:center; }}
@media(max-width:800px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }}
 .run-grid,.comparison,.filter-grid {{ grid-template-columns:1fr; }}
 .conversation>header {{ display:block; }} .question {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header class="hero"><div class="shell">
  <span class="eyebrow">Evaluation run · current one-turn baseline</span>
  <h1>Multi-turn baseline report</h1>
  <p>Gold versus observed routing, state, retrieval evidence, answers, citations,
  and safety behavior. Subjective answer scores are diagnostic, not safety gates.</p>
</div></header>
<main>
<div class="shell metrics">
  <div class="metric"><strong>{summary['scored_turn_count']}</strong><span>Scored turns</span></div>
  <div class="metric"><strong>{_percent(summary['route_accuracy'])}</strong><span>Route accuracy</span></div>
  <div class="metric"><strong>{_percent(summary['scope_accuracy'])}</strong><span>Scope accuracy</span></div>
  <div class="metric"><strong>{_percent(summary['outcome_accuracy'])}</strong><span>Outcome accuracy</span></div>
  <div class="metric"><strong>{summary['safety_violation_count']}</strong><span>Safety violations</span></div>
</div>
<div class="shell">
  <section class="panel">
    <h2>Run configuration</h2>
    <div class="run-grid">
      <dl>
        <dt>Run</dt><dd>{_e(metadata['run_id'])}</dd>
        <dt>Generated</dt><dd>{_e(metadata['generated_at'])}</dd>
        <dt>Gold set</dt><dd>{_e(run['set_id'])}</dd>
        <dt>System</dt><dd>{_e(metadata['system'])}</dd>
      </dl>
      <dl>
        <dt>Generation model</dt><dd>{_e(metadata['models']['generation'])}</dd>
        <dt>Control model</dt><dd>{_e(metadata['models']['control'])}</dd>
        <dt>Reasoning</dt><dd>{_e(metadata['models']['control_reasoning'])}</dd>
        <dt>LangSmith project</dt><dd>{_e(metadata['langsmith_project'])}</dd>
      </dl>
    </div>
  </section>
  <section class="panel">
    <h2>Component metrics</h2>
    <dl>
      <dt>History dependency</dt><dd>{_percent(summary['history_dependency_accuracy'])}</dd>
      <dt>Scope behavior</dt><dd>{_percent(summary['scope_behavior_accuracy'])}</dd>
      <dt>State update</dt><dd>{_percent(summary['state_update_accuracy'])}</dd>
      <dt>Required evidence recall</dt><dd>{_percent(summary['mean_required_evidence_recall'])}</dd>
      <dt>Full evidence coverage</dt><dd>{_percent(summary['full_required_evidence_coverage_rate'])}</dd>
      <dt>Citation node coverage</dt><dd>{_percent(summary['mean_citation_required_node_coverage'])}</dd>
      <dt>Gold-query retrieval recall</dt><dd>{_percent(oracle_metrics['mean_required_evidence_recall'])}</dd>
      <dt>Gold-query full coverage</dt><dd>{_percent(oracle_metrics['full_required_evidence_coverage_rate'])}</dd>
      <dt>Mean correctness</dt><dd>{_e(answer_quality.get('mean_correctness_out_of_4', 'n/a'))}</dd>
    </dl>
  </section>
</div>
<div class="filters"><div class="shell filter-grid">
  <input id="search" type="search" placeholder="Search turns, questions, and answers…">
  <select id="status"><option value="">Pass and fail</option><option value="pass">Pass</option><option value="fail">Fail</option></select>
  <select id="route"><option value="">All expected routes</option>
    {''.join(f'<option>{_e(route)}</option>' for route in sorted({turn["gold"]["expected_route"] for conversation in run["conversations"] for turn in conversation["turns"]}))}
  </select>
</div></div>
<div class="shell">{''.join(conversations)}</div>
</main>
<footer>Derived from the frozen gold set · detailed machine-readable results are in results.json</footer>
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
 document.querySelectorAll(".conversation").forEach(c=>{{
  c.classList.toggle("hidden",!c.querySelector(".turn:not(.hidden)"));
 }});
}}
controls.forEach(control=>control.addEventListener("input",filter));
</script>
</body></html>"""
