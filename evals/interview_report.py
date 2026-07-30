"""Self-contained review report for interview-answer evaluation runs."""

import json
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt

from evals.interview import normalize_interview_evaluation

MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def _badge(label: str, value: bool) -> str:
    state = "pass" if value else "fail"
    return f'<span class="badge {state}">{escape(label)}</span>'


def _evidence_list(items: list[dict]) -> str:
    if not items:
        return "<p>None</p>"
    return (
        "<ul>"
        + "".join(
            f"<li>N{item['node_id']} · {escape(item['path'])} · "
            f"pp. {escape(', '.join(str(page) for page in item['pages']))}</li>"
            for item in items
        )
        + "</ul>"
    )


def _criteria(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _card(row: dict) -> str:
    case = row["case"]
    prediction = row.get("prediction")
    checks = row["checks"]
    answer = (
        MARKDOWN.render(prediction["answer"])
        if prediction
        else f"<p>{escape(row.get('error', 'Case failed'))}</p>"
    )
    badges = "".join(
        _badge(key.replace("_", " "), value)
        for key, value in checks.items()
        if isinstance(value, bool)
    )
    evidence_recall = (
        f'<span class="badge neutral">evidence '
        f"{_percent(checks['evidence_recall'])}</span>"
        if "evidence_recall" in checks
        else ""
    )
    judgment = row.get("answer_judgment")
    judge = (
        f"<pre>{escape(json.dumps(judgment, indent=2, ensure_ascii=False))}</pre>"
        if judgment
        else f"<p>{escape(row.get('judge_error') or 'Judge not run')}</p>"
    )
    search = escape(
        " ".join(
            (
                case["id"],
                case["title"],
                case["prompt"],
                case["book_key"],
                case["expected_archetype"],
                prediction["answer"] if prediction else "",
            )
        ).casefold()
    )
    return f"""
<article class="case" data-book="{escape(case["book_key"])}"
 data-depth="{escape(case["expected_depth"])}"
 data-archetype="{escape(case["expected_archetype"])}"
 data-search="{search}">
  <header>
    <div><strong>{escape(case["id"])}</strong> · {escape(case["title"])}</div>
    <div>{badges}{evidence_recall}</div>
  </header>
  <h3>{escape(case["prompt"])}</h3>
  <p class="muted">{escape(case["book_key"])} ·
    {escape(case["expected_archetype"].replace("_", " "))} ·
    expected depth {escape(case["expected_depth"])} ·
    {row["latency_seconds"]:.2f}s</p>
  <div class="grid">
    <section>
      <h4>Evaluation contract</h4>
      <h5>Must cover</h5>{_criteria(case["must_cover"])}
      <h5>Must avoid</h5>{_criteria(case["must_avoid"])}
      <h5>Candidate evidence</h5>{_evidence_list(case["candidate_evidence"])}
    </section>
    <section>
      <h4>Candidate answer</h4>
      <div class="answer">{answer}</div>
    </section>
  </div>
  <details><summary>Judge, raw checks, and routing provenance</summary>
    {judge}
    <pre>{escape(json.dumps(checks, indent=2))}</pre>
    <pre>{
        escape(
            json.dumps(
                {
                    "route": prediction.get("route") if prediction else None,
                    "outcome": prediction.get("outcome") if prediction else None,
                    "depth": prediction.get("response_depth") if prediction else None,
                    "archetype": prediction.get("answer_archetype")
                    if prediction
                    else None,
                    "routing_reason": prediction.get("routing_reason")
                    if prediction
                    else None,
                    "prompt_profile_version": prediction.get("prompt_profile_version")
                    if prediction
                    else None,
                },
                indent=2,
            )
        )
    }</pre>
  </details>
</article>
"""


def render_interview_report(evaluation: dict, output: str | Path) -> Path:
    evaluation = normalize_interview_evaluation(evaluation)
    summary = evaluation["summary"]
    metrics = (
        ("Route", summary["route_accuracy"]),
        ("Outcome", summary["outcome_accuracy"]),
        ("Depth", summary["depth_accuracy"]),
        ("Archetype", summary["archetype_accuracy"]),
        ("Evidence recall", summary["required_evidence_recall"]),
        ("Citation validity", summary["citation_validity"]),
        ("No evidence preface", summary["evidence_preface_avoidance"]),
    )
    metric_html = "".join(
        f'<div class="metric"><strong>{_percent(value)}</strong>'
        f"<span>{escape(label)}</span></div>"
        for label, value in metrics
    )
    judge_mean = summary["judge"]["focused_mean_0_to_4"]
    if judge_mean is not None:
        metric_html += (
            f'<div class="metric"><strong>{judge_mean:.2f}/4</strong>'
            "<span>Focused judge mean</span></div>"
        )
    books = sorted({row["case"]["book_key"] for row in evaluation["cases"]})
    depths = sorted({row["case"]["expected_depth"] for row in evaluation["cases"]})
    archetypes = sorted(
        {row["case"]["expected_archetype"] for row in evaluation["cases"]}
    )
    option = lambda value: (
        f'<option value="{escape(value)}">{escape(value.replace("_", " "))}</option>'
    )
    cards = "".join(_card(row) for row in evaluation["cases"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Interview answer evaluation</title>
<style>
:root {{ color-scheme:light; font:15px/1.5 system-ui,sans-serif; }}
body {{ margin:0; background:#f4f6f8; color:#17202a; }}
main {{ max-width:1280px; margin:auto; padding:32px 20px; }}
h1 {{ margin-bottom:4px }} .muted {{ color:#607080 }}
.metrics {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
 gap:10px;margin:22px 0 }}
.metric,.case {{ background:white;border:1px solid #dce2e8;border-radius:10px;
 box-shadow:0 2px 8px #1b263110 }}
.metric {{ padding:14px }} .metric strong {{ display:block;font-size:24px }}
.metric span {{ color:#607080 }}
.controls {{ display:flex;flex-wrap:wrap;gap:10px;margin:20px 0;position:sticky;
 top:0;background:#f4f6f8;padding:10px 0;z-index:2 }}
input,select {{ padding:10px;border:1px solid #bcc7d1;border-radius:7px }}
input {{ flex:1;min-width:240px }} .case {{ padding:18px;margin:14px 0 }}
.case header {{ display:flex;justify-content:space-between;gap:12px }}
.grid {{ display:grid;grid-template-columns:minmax(280px,.8fr) minmax(0,1.2fr);
 gap:22px }} .grid section {{ min-width:0 }}
.badge {{ display:inline-block;padding:2px 7px;margin:2px;border-radius:99px;
 font-size:12px }} .pass {{ background:#d9f5e5;color:#12633a }}
.fail {{ background:#ffe0df;color:#922b21 }} .neutral {{ background:#e9eef3 }}
pre {{ white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:10px }}
.answer {{ overflow-wrap:anywhere }}
@media(max-width:800px) {{ .grid {{ grid-template-columns:1fr }}
 .case header {{ display:block }} }}
</style></head><body><main>
<h1>Interview answer evaluation</h1>
<p class="muted">{escape(evaluation["dataset_id"])} · {summary["cases"]} cases ·
 {summary["errors"]} execution errors · {summary["judge_errors"]} judge errors ·
 dataset review status: {escape(evaluation["dataset_review_status"])}</p>
<div class="metrics">{metric_html}</div>
<div class="controls">
 <input id="search" placeholder="Search questions, answers, or case IDs">
 <select id="book"><option value="">All books</option>
  {"".join(option(value) for value in books)}</select>
 <select id="depth"><option value="">All depths</option>
  {"".join(option(value) for value in depths)}</select>
 <select id="archetype"><option value="">All archetypes</option>
  {"".join(option(value) for value in archetypes)}</select>
</div>
<div id="cases">{cards}</div>
</main><script>
const search=document.querySelector('#search'), book=document.querySelector('#book'),
 depth=document.querySelector('#depth'), archetype=document.querySelector('#archetype');
function filter() {{
 const q=search.value.trim().toLowerCase(), b=book.value, d=depth.value,
  a=archetype.value;
 document.querySelectorAll('.case').forEach(card => {{
  card.hidden=(b&&card.dataset.book!==b)||(d&&card.dataset.depth!==d)||
   (a&&card.dataset.archetype!==a)||(q&&!card.dataset.search.includes(q));
 }});
}}
[search,book,depth,archetype].forEach(control =>
 control.addEventListener(control===search?'input':'change',filter));
</script></body></html>"""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output
