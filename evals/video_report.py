"""Self-contained HTML report for a video evaluation run.

Built on the same page shape as `evals.report`, with two panels the book
report has no need for: the rewriting ablation, which is a comparison rather
than a score and reads wrong as a single percentage, and the per-stretch
summary coverage table, where the whole point is to put "cited" and
"substantively covered" in adjacent columns.
"""

from html import escape
from pathlib import Path

from markdown_it import MarkdownIt


MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})


def _percent(value):
    return "—" if value is None else f"{100 * value:.1f}%"


def _badge(label, passed):
    return f'<span class="badge {"pass" if passed else "fail"}">{escape(label)}</span>'


def _timestamp(milliseconds):
    if milliseconds is None:
        return "—"
    total = int(milliseconds) // 1000
    return f"{total // 60}:{total % 60:02d}"


def _evidence_rows(prediction):
    if not prediction or not prediction.get("evidence"):
        return "<p class='muted'>No evidence retrieved.</p>"
    cells = "".join(
        f"<tr><td>[S{escape(str(item['rank']))}]</td>"
        f"<td>{escape(item['modality'])}</td>"
        f"<td>{_timestamp(item.get('start_ms'))}</td>"
        f"<td>{escape(item['retrieval_method'])}</td>"
        f"<td>{escape(item['excerpt'][:180])}</td></tr>"
        for item in prediction["evidence"]
    )
    return (
        "<table><thead><tr><th>Marker</th><th>Modality</th><th>At</th>"
        f"<th>Found by</th><th>Excerpt</th></tr></thead><tbody>{cells}</tbody></table>"
    )


def _anchor_rows(gold):
    anchors = (gold.get("expected_evidence") or []) + [
        anchor | {"role": "near miss"}
        for anchor in (gold.get("near_miss_evidence") or [])
    ]
    if not anchors:
        return "<p class='muted'>No anchors: this turn is judged on behaviour.</p>"
    cells = "".join(
        f"<tr><td>{escape(str(anchor.get('role', 'required')))}</td>"
        f"<td>{_where(anchor)}</td>"
        f"<td>{escape(', '.join(anchor['modalities']))}</td>"
        f"<td>{escape(anchor['why'])}</td></tr>"
        for anchor in anchors
    )
    return (
        "<table><thead><tr><th>Role</th><th>Where</th><th>Modalities</th>"
        f"<th>Why it is evidence</th></tr></thead><tbody>{cells}</tbody></table>"
    )


def _where(anchor):
    """A moment for the timeline, a page for the deck.

    The linked document has no timestamps and ingestion refuses to invent an
    alignment for it, so its anchors name pages and the column has to say both.
    """

    pages = anchor.get("resource_pages")
    if pages:
        return "page " + ", ".join(str(page) for page in pages)
    return f"{_timestamp(anchor['start_ms'])}–{_timestamp(anchor['end_ms'])}"


def _rewrite_panel(arms):
    if not arms:
        return ""
    rows = "".join(
        f"<tr><td>{escape(arm)}</td><td>{escape(arms['queries'][arm])}</td>"
        f"<td>{_percent(arms['recall'][arm])}</td></tr>"
        for arm in ("raw", "rewritten", "gold")
    )
    verdict = (
        "rewriting helped"
        if arms["rewrite_helped"]
        else "rewriting hurt"
        if arms["rewrite_hurt"]
        else "no difference"
    )
    return f"""
  <details open><summary>Rewriting ablation — {escape(verdict)}</summary>
    <table><thead><tr><th>Arm</th><th>Query sent to retrieval</th>
    <th>Anchor recall</th></tr></thead><tbody>{rows}</tbody></table>
  </details>"""


def _units_panel(row):
    units = row.get("summary_units")
    if not units:
        return ""
    rows = "".join(
        f"<tr><td>{escape(unit['label'])}</td>"
        f"<td>{'yes' if unit['cited'] else 'no'}</td>"
        f"<td class='{'ok' if unit['substantive'] else 'bad'}'>"
        f"{'yes' if unit['substantive'] else 'no'}</td>"
        f"<td>{escape(', '.join(unit['matched_terms']) or '—')}</td>"
        f"<td>{escape(' '.join(unit['citing_claims'])[:260] or '—')}</td></tr>"
        for unit in units
        if unit["required"]
    )
    measured = row["summary_substance"]
    return f"""
  <details open><summary>Summary coverage — cited {_percent(measured["cited_rate"])},
   substantive {_percent(measured["substantive_rate"])},
   {measured["vacuous_citation_count"]} vacuous</summary>
    <table><thead><tr><th>Stretch</th><th>Cited</th><th>Substantive</th>
    <th>Shared terms</th><th>Claim it stands behind</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </details>"""


def _card(row):
    gold = row["gold"]
    prediction = row.get("prediction")
    checks = row["checks"]
    answer = (
        MARKDOWN.render(prediction["answer"])
        if prediction
        else f"<p>{escape(row.get('error', 'Turn failed'))}</p>"
    )
    badges = "".join(
        _badge(name.replace("_", " "), passed)
        for name, passed in checks.items()
        if isinstance(passed, bool)
    ) + "".join(
        f'<span class="badge neutral">{escape(name.replace("_", " "))}'
        f" {_percent(value)}</span>"
        for name, value in checks.items()
        if isinstance(value, float)
    )
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
<article class="turn" data-route="{escape(gold["expected_route"])}"
 data-search="{search}">
  <header>
    <div><strong>{escape(row["turn_id"])}</strong> ·
      {escape(row["conversation_title"])}</div>
    <div>{badges}</div>
  </header>
  <h3>{escape(gold["user"])}</h3>
  <div class="grid">
    <section>
      <h4>Expected</h4>
      <dl>
        <dt>Route</dt><dd>{escape(gold["expected_route"])}</dd>
        <dt>Dependency</dt><dd>{escape(gold["history_dependency"])}</dd>
        <dt>Answerable</dt><dd>{"yes" if gold.get("answerable", True) else "no"}</dd>
        <dt>Query</dt><dd>{escape(gold.get("expected_standalone_query") or "—")}</dd>
      </dl>
      <p>{escape(gold["reference_answer"])}</p>
      {_anchor_rows(gold)}
    </section>
    <section>
      <h4>Actual</h4>
      <dl>
        <dt>Route</dt><dd>{escape(prediction["route"]) if prediction else "error"}</dd>
        <dt>Dependency</dt>
        <dd>{escape(prediction["history_dependency"]) if prediction else "—"}</dd>
        <dt>Outcome</dt><dd>{escape(prediction["outcome"]) if prediction else "—"}</dd>
        <dt>Query</dt>
        <dd>{escape((prediction or {}).get("standalone_query") or "—")}</dd>
      </dl>
      <div class="answer">{answer}</div>
      {_evidence_rows(prediction)}
    </section>
  </div>
  {_rewrite_panel(row.get("rewrite_arms"))}
  {_units_panel(row)}
  <details><summary>Raw checks and optional judge</summary>
    <pre>{escape(str(checks))}</pre>
    <pre>{escape(str(row.get("answer_judgment") or "Not run"))}</pre>
  </details>
</article>
"""


def _headline(evaluation):
    summary = evaluation["summary"]
    coverage = evaluation["summary_coverage"]
    ablation = evaluation["rewrite_ablation"]
    metrics = [
        ("Route", summary["route_accuracy"]),
        ("History", summary["history_dependency_accuracy"]),
        ("Outcome", summary["outcome_accuracy"]),
        ("Evidence recall", summary["required_evidence_recall"]),
        ("Citations valid", summary["citation_validity"]),
    ]
    if coverage.get("summaries"):
        metrics += [
            ("Summary cited", coverage["mean_cited_rate"]),
            ("Summary substantive", coverage["mean_substantive_rate"]),
        ]
    if ablation.get("probes"):
        metrics += [
            ("Recall, as typed", ablation["mean_recall"]["raw"]),
            ("Recall, rewritten", ablation["mean_recall"]["rewritten"]),
            ("Recall, gold rewrite", ablation["mean_recall"]["gold"]),
        ]
    return "".join(
        f'<div class="metric"><strong>{_percent(value)}</strong>'
        f"<span>{escape(label)}</span></div>"
        for label, value in metrics
    )


def _ablation_note(evaluation):
    ablation = evaluation["rewrite_ablation"]
    if not ablation.get("probes"):
        return ""
    return (
        f"<p class='muted'>Rewriting replay: {ablation['probes']} follow-ups, "
        f"{ablation['queries_actually_rewritten']} of which the router actually "
        f"rewrote. Rewriting helped {ablation['rewrite_helped']}, hurt "
        f"{ablation['rewrite_hurt']}, changed nothing for "
        f"{ablation['rewrite_neutral']}. It captured "
        f"{_percent(ablation['share_of_gold_gain'])} of the recall the gold "
        "rewrite shows was available.</p>"
    )


def render_video_report(evaluation: dict, output: str | Path) -> Path:
    summary = evaluation["summary"]
    routes = sorted({row["gold"]["expected_route"] for row in evaluation["turns"]})
    options = "".join(
        f'<option value="{escape(route)}">{escape(route)}</option>' for route in routes
    )
    cards = "".join(_card(row) for row in evaluation["turns"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lecture conversation evaluation</title>
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
table {{ width:100%;border-collapse:collapse;font-size:13px;margin-top:8px }}
th,td {{ text-align:left;padding:5px 7px;border-bottom:1px solid #e6ebf0;
 vertical-align:top;overflow-wrap:anywhere }}
th {{ color:#607080;font-weight:600 }}
td.ok {{ color:#12633a;font-weight:600 }} td.bad {{ color:#922b21;font-weight:600 }}
details {{ margin-top:12px }} summary {{ cursor:pointer;color:#31506b }}
pre {{ white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:10px }}
@media(max-width:760px) {{ .grid {{ grid-template-columns:1fr }}
 .turn header {{ display:block }} }}
</style></head><body><main>
<h1>Lecture conversation evaluation</h1>
<p class="muted">{summary["turns"]} turns · {summary["errors"]} execution errors ·
 evidence judged by overlap with a stretch of lecture, not by evidence id.</p>
<div class="metrics">{_headline(evaluation)}</div>
{_ablation_note(evaluation)}
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


__all__ = ["render_video_report"]
