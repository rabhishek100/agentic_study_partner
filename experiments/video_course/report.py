"""Interactive, dependency-free HTML report for the video-course pilot."""

from __future__ import annotations

from dataclasses import asdict
from html import escape
import json
from pathlib import Path
from typing import Any

from .models import EvidenceUnit, EvaluationResult, FrameCandidate, VisualObservation


def build_report(
    destination: Path,
    *,
    title: str,
    source_url: str,
    source_video: Path,
    frames: list[FrameCandidate],
    observations: list[VisualObservation],
    evidence: list[EvidenceUnit],
    results: list[EvaluationResult],
    metrics: dict[str, Any],
    root: Path,
) -> Path:
    by_frame = {frame.id: frame for frame in frames}
    by_evidence = {item.id: item for item in evidence}
    observation_payload = []
    for observation in observations:
        frame = by_frame.get(observation.evidence_frame_ids[0])
        observation_payload.append(
            {
                **asdict(observation),
                "preview": relative(frame.preview_path, root) if frame else None,
                "ocr": frame.ocr_text if frame else "",
                "timestamp": format_ms(observation.start_ms),
            }
        )
    result_payload = []
    for result in results:
        result_payload.append(
            {
                **asdict(result),
                "evidence": [
                    {
                        "id": retrieved.evidence_id,
                        "rank": retrieved.rank,
                        "score": retrieved.score,
                        "methods": list(retrieved.retrieval_methods),
                        "start_ms": (
                            by_evidence[retrieved.evidence_id].start_ms
                            if retrieved.evidence_id in by_evidence
                            else None
                        ),
                        "end_ms": (
                            by_evidence[retrieved.evidence_id].end_ms
                            if retrieved.evidence_id in by_evidence
                            else None
                        ),
                        "page": (
                            by_evidence[retrieved.evidence_id].page
                            if retrieved.evidence_id in by_evidence
                            else None
                        ),
                        "modality": (
                            by_evidence[retrieved.evidence_id].modality
                            if retrieved.evidence_id in by_evidence
                            else "unknown"
                        ),
                        "preview": (
                            relative(by_evidence[retrieved.evidence_id].image_path, root)
                            if retrieved.evidence_id in by_evidence
                            and by_evidence[retrieved.evidence_id].image_path
                            else None
                        ),
                    }
                    for retrieved in result.retrieved
                ],
            }
        )
    payload = {
        "title": title,
        "sourceUrl": source_url,
        "video": relative(source_video, root),
        "observations": observation_payload,
        "results": result_payload,
        "metrics": metrics,
    }
    encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    destination.write_text(_template(encoded), encoding="utf-8")
    return destination


def relative(path: str | Path, root: Path) -> str:
    return str(Path(path).resolve().relative_to(root.resolve()))


def format_ms(value: int) -> str:
    seconds = value // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _template(payload: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video-first course pilot</title>
<style>
:root {{ color-scheme: light dark; --bg:#0b1020; --card:#121a2c; --muted:#9aa7bd; --line:#29344b; --accent:#73a8ff; --good:#58d6a5; --bad:#ff8b8b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif; background:var(--bg); color:#eef3fb; }}
button,input,select {{ font:inherit; }}
a {{ color:var(--accent); }}
header {{ position:sticky; top:0; z-index:5; display:flex; gap:16px; align-items:center; padding:12px 20px; background:rgba(11,16,32,.94); border-bottom:1px solid var(--line); backdrop-filter:blur(12px); }}
header h1 {{ margin:0; font-size:17px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.layout {{ display:grid; grid-template-columns:minmax(360px,44%) 1fr; min-height:calc(100vh - 58px); }}
.viewer {{ position:sticky; top:58px; height:calc(100vh - 58px); overflow:auto; padding:18px; border-right:1px solid var(--line); }}
video {{ width:100%; border-radius:12px; background:#000; box-shadow:0 12px 32px #0008; }}
.content {{ min-width:0; padding:18px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:14px 0; }}
.metric,.card {{ border:1px solid var(--line); background:var(--card); border-radius:12px; }}
.metric {{ padding:10px 12px; }} .metric strong {{ display:block; font-size:20px; }} .metric span,.muted {{ color:var(--muted); }}
.controls {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }}
input,select {{ min-width:180px; border:1px solid var(--line); background:var(--card); color:inherit; border-radius:8px; padding:8px 10px; }}
.timeline {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px; }}
.frame {{ text-align:left; border:1px solid var(--line); background:var(--card); color:inherit; border-radius:10px; padding:0; overflow:hidden; cursor:pointer; }}
.frame:hover,.frame:focus-visible {{ outline:2px solid var(--accent); outline-offset:1px; }}
.frame img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
.frame div {{ padding:7px; }} .frame small {{ color:var(--muted); }}
.question {{ margin:0 0 14px; padding:16px; }}
.question h3 {{ margin:0 0 8px; font-size:17px; }}
.badge {{ display:inline-block; margin:0 5px 5px 0; padding:2px 7px; border:1px solid var(--line); border-radius:999px; color:var(--muted); font-size:12px; }}
.score {{ color:var(--good); font-weight:700; }} .error {{ color:var(--bad); }}
.answer-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.answer {{ padding:12px; border:1px solid var(--line); border-radius:9px; background:#0d1425; white-space:pre-wrap; }}
.answer h4 {{ margin:0 0 6px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
.cite,.evidence-seek {{ border:1px solid var(--accent); border-radius:6px; background:transparent; color:var(--accent); cursor:pointer; padding:0 4px; }}
.evidence-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px; margin-top:10px; }}
.evidence-card {{ border:1px solid var(--line); border-radius:8px; overflow:hidden; min-width:0; }}
.evidence-card img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
.evidence-card div {{ padding:7px; overflow-wrap:anywhere; }}
details {{ margin-top:10px; }}
@media (max-width:900px) {{ .layout {{ display:block; }} .viewer {{ position:static; height:auto; border-right:0; border-bottom:1px solid var(--line); }} .answer-grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header><h1 id="title">Video-first course pilot</h1><a id="source" target="_blank" rel="noreferrer">Open on YouTube</a></header>
<div class="layout">
  <aside class="viewer">
    <video id="video" controls preload="metadata"></video>
    <div id="metrics" class="metrics"></div>
    <div class="controls"><input id="frame-search" placeholder="Search visual evidence"><select id="type-filter"><option value="">All visual types</option></select></div>
    <div id="timeline" class="timeline" aria-label="Visual timeline"></div>
  </aside>
  <main class="content">
    <div class="controls"><input id="question-search" placeholder="Search questions"><select id="category-filter"><option value="">All question categories</option></select></div>
    <div id="questions"></div>
  </main>
</div>
<script>
const data={payload};
const $=id=>document.getElementById(id);
$('title').textContent=data.title; $('source').href=data.sourceUrl; $('video').src=data.video;
const metricEntries={{
  'Frames':data.metrics.frame_count,'Visual coverage':pct(data.metrics.visual_success_rate),
  'Mean agreement':pct(data.metrics.mean_semantic_agreement),'Citation validity':pct(data.metrics.mean_citation_correctness),
  'Ingestion cost':money(data.metrics.ingestion_cost_usd),'Evaluation cost':money(data.metrics.evaluation_cost_usd)
}};
$('metrics').innerHTML=Object.entries(metricEntries).map(([k,v])=>`<div class="metric"><strong>${{esc(v)}}</strong><span>${{esc(k)}}</span></div>`).join('');
const types=[...new Set(data.observations.flatMap(o=>o.content_types))].sort();
types.forEach(t=>$('type-filter').insertAdjacentHTML('beforeend',`<option>${{esc(t)}}</option>`));
function renderFrames(){{
 const q=$('frame-search').value.toLowerCase(), type=$('type-filter').value;
 const rows=data.observations.filter(o=>(!type||o.content_types.includes(type))&&(!q||(o.summary+' '+o.visible_text+' '+o.ocr).toLowerCase().includes(q)));
 $('timeline').innerHTML=rows.map(o=>`<button class="frame" data-ms="${{o.start_ms}}"><img loading="lazy" src="${{attr(o.preview)}}" alt="Frame at ${{o.timestamp}}"><div><strong>${{esc(o.timestamp)}}</strong><br><small>${{esc(o.content_types.join(', ')||'unclassified')}}</small><br>${{esc(short(o.summary,100))}}</div></button>`).join('');
 document.querySelectorAll('.frame').forEach(button=>button.onclick=()=>seek(+button.dataset.ms));
}}
function seek(ms){{ const video=$('video'); video.currentTime=Math.max(0,ms/1000-3); video.scrollIntoView({{behavior:'smooth',block:'center'}}); }}
const categories=[...new Set(data.results.map(r=>r.question.category))].sort();
categories.forEach(t=>$('category-filter').insertAdjacentHTML('beforeend',`<option>${{esc(t)}}</option>`));
function renderQuestions(){{
 const q=$('question-search').value.toLowerCase(), category=$('category-filter').value;
 const rows=data.results.filter(r=>(!category||r.question.category===category)&&(!q||(r.question.question+' '+r.system_answer+' '+r.reference_answer).toLowerCase().includes(q)));
 $('questions').innerHTML=rows.map(r=>`<article class="question card"><div><span class="badge">${{esc(r.question.category)}}</span><span class="badge">${{money(r.cost_usd)}}</span><span class="score">${{pct(r.semantic_agreement)}} agreement</span></div><h3>${{esc(r.question.question)}}</h3>${{r.error?`<p class="error">${{esc(r.error)}}</p>`:''}}<div class="answer-grid"><div class="answer"><h4>System answer</h4>${{answerHtml(r.system_answer,r.evidence)}}</div><div class="answer"><h4>Source-grounded reference</h4>${{esc(r.reference_answer)}}</div></div><details><summary>Diagnostics and retrieved evidence</summary><p>${{esc(r.judge_summary)}}</p><p><strong>Missing:</strong> ${{esc((r.missing_points||[]).join('; ')||'none')}}</p><p><strong>Unsupported:</strong> ${{esc((r.unsupported_claims||[]).join('; ')||'none')}}</p><div class="evidence-grid">${{r.evidence.map(e=>evidenceCard(e)).join('')}}</div></details></article>`).join('');
 document.querySelectorAll('.cite,.evidence-seek').forEach(button=>button.onclick=()=>seek(+button.dataset.ms));
}}
function answerHtml(value,evidence){{
 const safe=esc(value);
 return safe.replace(/\\[E(\\d+)\\]/g,(match,number)=>{{const item=evidence[+number-1];return item&&item.start_ms!==null?`<button class="cite" data-ms="${{item.start_ms}}" title="Seek to evidence">${{match}}</button>`:match;}});
}}
function evidenceCard(e){{const locator=e.start_ms!==null?time(e.start_ms):(e.page?`slide ${{e.page}}`:'no locator');return `<div class="evidence-card">${{e.preview?`<img loading="lazy" src="${{attr(e.preview)}}" alt="${{esc(e.modality)}} evidence">`:''}}<div><span class="badge">${{esc(e.modality)}}</span><br>${{e.start_ms!==null?`<button class="evidence-seek" data-ms="${{e.start_ms}}">${{locator}}</button>`:esc(locator)}}<br><small>${{esc(e.id)}} · ${{esc(e.methods.join('+'))}}</small></div></div>`;}}
function esc(v){{return String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
function attr(v){{return esc(v).replace(/ /g,'%20');}} function short(v,n){{v=String(v||'');return v.length>n?v.slice(0,n-1)+'…':v;}}
function pct(v){{return Number.isFinite(+v)?Math.round(+v*100)+'%':'—';}} function money(v){{return Number.isFinite(+v)?'$'+(+v).toFixed(3):'—';}}
function time(ms){{const s=Math.floor(ms/1000);return [Math.floor(s/3600),Math.floor(s%3600/60),s%60].map(v=>String(v).padStart(2,'0')).join(':');}}
['frame-search','type-filter'].forEach(id=>$(id).addEventListener('input',renderFrames));
['question-search','category-filter'].forEach(id=>$(id).addEventListener('input',renderQuestions));
renderFrames();renderQuestions();
</script>
</body></html>"""
