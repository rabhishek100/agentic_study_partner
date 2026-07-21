"""Self-contained HTML report for conversational evaluation runs."""

from html import escape
from pathlib import Path

from markdown_it import MarkdownIt


MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})


def _percent(value):
    return f"{100 * value:.1f}%"


def _badge(label, passed):
    state = "pass" if passed else "fail"
    return f'<span class="badge {state}">{escape(label)}</span>'


def _scope(value):
    if not value:
        return "—"
    return escape(value.get("display_path") or str(value.get("node_id")))


def _card(row):
    gold = row["gold"]
    prediction = row.get("prediction")
    checks = row["checks"]
    answer = (
        MARKDOWN.render(prediction["answer"])
        if prediction
        else f"<p>{escape(row.get('error', 'Turn failed'))}</p>"
    )
    predicted_query = prediction.get("standalone_query") if prediction else None
    predicted_scope = (
        prediction.get("resolved_scope")
        or row.get("state", {}).get("active_scope")
        if prediction
        else None
    )
    badges = "".join(
        _badge(name.replace("_", " "), passed)
        for name, passed in checks.items()
        if isinstance(passed, bool) and name != "standalone_exact"
    )
    recall = _percent(checks["evidence_recall"])
    search = escape(
        " ".join(
            [
                row["turn_id"],
                row["conversation_title"],
                gold["user"],
                gold["expected_route"],
                prediction["answer"] if prediction else "",
            ]
        ).casefold()
    )
    return f"""
<article class="turn" data-route="{escape(gold['expected_route'])}"
 data-search="{search}">
  <header>
    <div><strong>{escape(row['turn_id'])}</strong> ·
      {escape(row['conversation_title'])}</div>
    <div>{badges}<span class="badge neutral">evidence {recall}</span></div>
  </header>
  <h3>{escape(gold['user'])}</h3>
  <div class="grid">
    <section>
      <h4>Expected</h4>
      <dl>
        <dt>Route</dt><dd>{escape(gold['expected_route'])}</dd>
        <dt>Dependency</dt><dd>{escape(gold['history_dependency'])}</dd>
        <dt>Query</dt><dd>{escape(gold.get('expected_standalone_query', '—'))}</dd>
        <dt>Scope</dt><dd>{escape(str(gold.get('expected_scope') or '—'))}</dd>
      </dl>
      <p>{escape(gold['reference_answer'])}</p>
    </section>
    <section>
      <h4>Actual</h4>
      <dl>
        <dt>Route</dt><dd>{escape(prediction['route']) if prediction else 'error'}</dd>
        <dt>Dependency</dt><dd>{escape(prediction['history_dependency']) if prediction else '—'}</dd>
        <dt>Query</dt><dd>{escape(predicted_query or '—')}</dd>
        <dt>Scope</dt><dd>{_scope(predicted_scope)}</dd>
      </dl>
      <div class="answer">{answer}</div>
    </section>
  </div>
  <details><summary>Raw checks and optional judge</summary>
    <pre>{escape(str(checks))}</pre>
    <pre>{escape(str(row.get('answer_judgment') or 'Not run'))}</pre>
  </details>
</article>
"""


def render_report(evaluation: dict, output: str | Path) -> Path:
    summary = evaluation["summary"]
    metrics = [
        ("Route", summary["route_accuracy"]),
        ("History", summary["history_dependency_accuracy"]),
        ("Scope", summary["scope_accuracy"]),
        ("Outcome", summary["outcome_accuracy"]),
        ("Evidence recall", summary["required_evidence_recall"]),
        ("Citations valid", summary["citation_validity"]),
    ]
    metric_html = "".join(
        f'<div class="metric"><strong>{_percent(value)}</strong>'
        f"<span>{escape(label)}</span></div>"
        for label, value in metrics
    )
    routes = sorted({row["gold"]["expected_route"] for row in evaluation["turns"]})
    options = "".join(
        f'<option value="{escape(route)}">{escape(route)}</option>'
        for route in routes
    )
    cards = "".join(_card(row) for row in evaluation["turns"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Conversation evaluation</title>
<style>
:root {{ color-scheme: light; font: 15px/1.5 system-ui,sans-serif; }}
body {{ margin: 0; background:#f4f6f8; color:#17202a; }}
main {{ max-width:1200px; margin:auto; padding:32px 20px; }}
h1 {{ margin-bottom:4px }} .muted {{ color:#607080 }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
 gap:10px; margin:22px 0 }}
.metric,.turn {{ background:white; border:1px solid #dce2e8;
 border-radius:10px; box-shadow:0 2px 8px #1b263110 }}
.metric {{ padding:14px }} .metric strong {{ display:block;font-size:24px }}
.metric span {{ color:#607080 }}
.controls {{ display:flex;gap:10px;margin:20px 0;position:sticky;top:0;
 background:#f4f6f8;padding:10px 0;z-index:2 }}
input,select {{ padding:10px;border:1px solid #bcc7d1;border-radius:7px }}
input {{ flex:1 }} .turn {{ padding:18px;margin:14px 0 }}
.turn header {{ display:flex;justify-content:space-between;gap:12px }}
.grid {{ display:grid;grid-template-columns:1fr 1fr;gap:18px }}
.grid section {{ min-width:0 }} dl {{ display:grid;grid-template-columns:100px 1fr }}
dt {{ color:#607080 }} dd {{ margin:0;overflow-wrap:anywhere }}
.badge {{ display:inline-block;padding:2px 7px;margin:2px;border-radius:99px;
 font-size:12px }} .pass {{ background:#d9f5e5;color:#12633a }}
.fail {{ background:#ffe0df;color:#922b21 }} .neutral {{ background:#e9eef3 }}
pre {{ white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:10px }}
@media(max-width:760px) {{ .grid {{ grid-template-columns:1fr }}
 .turn header {{ display:block }} }}
</style></head><body><main>
<h1>Conversation evaluation</h1>
<p class="muted">{summary['turns']} turns · {summary['errors']} execution errors.
 Exact query wording is retained only as a debugging signal.</p>
<div class="metrics">{metric_html}</div>
<div class="controls">
 <input id="search" placeholder="Search questions, answers, or turn IDs">
 <select id="route"><option value="">All routes</option>{options}</select>
</div>
<div id="turns">{cards}</div>
</main><script>
const search=document.querySelector('#search'), route=document.querySelector('#route');
function filter() {{
 const q=search.value.trim().toLowerCase(), r=route.value;
 document.querySelectorAll('.turn').forEach(card => {{
  card.hidden=(r && card.dataset.route!==r)||(q && !card.dataset.search.includes(q));
 }});
}}
search.addEventListener('input',filter); route.addEventListener('change',filter);
</script></body></html>"""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output
