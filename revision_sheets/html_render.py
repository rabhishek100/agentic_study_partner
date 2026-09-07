"""One safe HTML artifact for responsive reading, print, and visual review.

Only an owned template emits markup; model strings are escaped. Chromium has
no network access and no JavaScript, and refuses clipped two-page output.
"""
from base64 import b64encode
from html import escape
import re
import unicodedata

import pymupdf

from .contracts import RevisionError
from .render import citations

LAYOUT_VERSION = "html-a4-spread-v2"
CSS = """
*{box-sizing:border-box}body{margin:0;background:#ecefe9;color:#18332d;font-family:Arial,Helvetica,sans-serif}
.page{width:210mm;height:297mm;padding:12mm 11mm 12mm;margin:20px auto;background:#fffef9;position:relative;overflow:visible}
.running{font-size:8pt;letter-spacing:.13em;text-transform:uppercase;color:#426457;border-bottom:1px solid #aabeb2;padding-bottom:6px;display:flex;justify-content:space-between;gap:20px}
h1{font-family:Georgia,serif;font-size:27pt;font-weight:normal;line-height:1.06;letter-spacing:-.035em;margin:17px 0 14px}h2{font-family:Georgia,serif;font-size:21pt;font-weight:normal;line-height:1.15;margin:10px 0 8px}
h3{font-size:8pt;letter-spacing:.13em;text-transform:uppercase;color:#466a59;margin:0 0 12px}
p{font-size:10.5pt;line-height:1.3;margin:0 0 8px}.lead{background:#e6eee3;border-left:3px solid #2d654f;padding:13px 16px;margin-bottom:12px}.lead p{font-size:12pt;line-height:1.4;margin:0}
.notes-flow{column-count:2;column-gap:24px}.notes-flow p{margin-bottom:6px}.notes-flow .note b{display:inline;margin-right:4px}.notes-flow h3{break-after:avoid}.notes-flow figure{break-inside:avoid}.notes-flow .recall{break-inside:avoid}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}.note{break-inside:avoid;margin-bottom:6px}.note b{display:block;margin-bottom:2px;font-size:10.5pt}.cite{font-size:9pt;color:#315e4b;text-decoration:underline;text-underline-offset:2px}.note .cite{white-space:normal}
figure{margin:0 0 8px;border:1px solid #c5d3c7;padding:8px;background:white}figure img{display:block;max-width:100%;max-height:420px;width:auto;height:auto;margin:auto;object-fit:contain}figcaption{font-size:8pt;line-height:1.3;color:#426457;margin-top:8px}
.relationships{padding:12px 15px;background:#f0f2ea;margin-bottom:12px}.relationships p{font-size:9.5pt;margin-bottom:5px}.recall{border-top:2px solid #315e4b;padding-top:12px;margin-top:15px}.footer{position:absolute;bottom:10mm;left:11mm;right:11mm;border-top:1px solid #bdcdbf;padding-top:7px;font-size:8pt;color:#426457;display:flex;justify-content:space-between;gap:16px}
.body{padding-bottom:8px}.part{font-size:8pt;color:#466a59;letter-spacing:.1em;text-transform:uppercase}.source-name{max-width:80%;overflow-wrap:anywhere}
a:focus-visible{outline:2px solid #28593d;outline-offset:3px}
.source-index{max-width:210mm;margin:20px auto;padding:20px;background:#fffef9}.source-index p{font-size:9pt}.source-index summary{cursor:pointer}@page{size:A4;margin:0}@media print{.source-index{display:none}body{background:white}.page{margin:0;break-after:page}.page:last-child{break-after:auto}*{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
@media screen and (max-width:800px){.page{width:100%;height:auto;min-height:0;margin:0 0 18px;padding:24px}.columns{grid-template-columns:1fr;gap:10px}.notes-flow{column-count:1}.footer{position:static;margin-top:24px}h1{font-size:27pt}figure img{max-height:360px}}
"""


def make_html(sheet, *, source_title, scope_title, references, figures=None, figure_limit=2, intro_note_count=0, compact=False):
    figures = figures or {}
    css = CSS.replace("font-size:10.5pt;line-height:1.3", "font-size:10.5pt;line-height:1.25").replace("break-inside:avoid;margin-bottom:6px", "break-inside:avoid;margin-bottom:4px") if compact else CSS
    def cites(markers):
        return " ".join(f'<a class="cite" title="{escape(references[m]["section_title"], quote=True)} · PDF page {references[m]["page"]}" href="#source-{escape(m, quote=True)}">[{escape(references[m]["section_number"])}:{references[m]["page"]}]</a>'
                        for m in dict.fromkeys(markers))
    def note(item):
        return f'<div class="note" id="item-{escape(item.id)}"><p><b>{escape(item.heading)}</b> {escape(item.text)} {cites(item.citations)}</p></div>'
    def fig(block_id):
        image = figures[block_id]
        return (f'<figure><img alt="{escape("Original source figure, PDF page " + str(image["page"]), quote=True)}" src="data:image/png;base64,{b64encode(image["bytes"]).decode()}">'
                f'<figcaption>ORIGINAL FIGURE · PDF p. {image["page"]} {cites([image["citation"]])}</figcaption></figure>')
    selected = [i for i in sheet.diagram.source_figure_ids if i in figures][:figure_limit]
    hero = fig(selected[0]) if selected else ''
    second = fig(selected[1]) if len(selected) > 1 else ''
    intro = sheet.essential_notes[:intro_note_count]
    remaining = sheet.essential_notes[intro_note_count:]
    node_numbers = {n.id: index for index, n in enumerate(sheet.diagram.nodes, 1)}
    relationships = ''.join(f'<p id="item-{escape(e.id)}"><b>{node_numbers[e.source]} → {node_numbers[e.target]}</b>: {escape(e.label)} {cites(e.citations)}</p>' for e in sheet.diagram.edges)
    component_sources = ''.join(f'<p id="item-{escape(n.id)}"><b>{node_numbers[n.id]}.</b> {escape(n.label)} {cites(n.citations)}</p>' for n in sheet.diagram.nodes)
    overview = f'<div class="relationships"><h3>Mechanism at a glance</h3><p id="item-diagram_description">{escape(sheet.diagram.description)} {cites(sheet.diagram.description_citations)}</p><div class="columns"><section><h3>Component key</h3>{component_sources}</section><section><h3>Relationships</h3>{relationships}</section></div></div>'
    pages = [
        f'<h1>{escape(sheet.title)}</h1><div class="lead">{note(sheet.central_idea)}</div>{hero}{overview}<div class="columns">{"".join(note(i) for i in intro)}</div>',
        f'<span class="part">The details that change the answer</span><h2>Constraints, choices & recall</h2><div class="notes-flow"><h3>02 / Complete the model</h3>{"".join(note(i) for i in remaining)}{note(sheet.equation) if sheet.equation else ""}{second}<h3>{"Results & limitations" if sheet.template_kind == "paper" else "Trade-offs & failure modes"}</h3>{"".join(note(i) for i in sheet.comparison_rows)}<div class="recall"><h3>03 / Reconstruct from memory</h3>{"".join(note(i) for i in sheet.recall_cues)}</div></div>'

    ]
    content = ''.join(f'<article class="page"><header class="running"><span class="source-name">{escape(source_title)}</span><span>REVISION / {index:02d}</span></header><main class="body">{body}</main><footer class="footer"><span>{escape(scope_title)}</span><span>{index} / 2 · Citations [section:PDF page]</span></footer></article>' for index, body in enumerate(pages, 1))
    used = list(dict.fromkeys(m for item in sheet.items() for m in item.citations))
    source_key = '<details class="source-index"><summary>Source key · section and PDF pages</summary>' + ''.join(
        f'<p id="source-{escape(m, quote=True)}">{escape(citations([m], references))} · {escape(references[m].get("path", references[m]["section_title"]))}</p>' for m in used) + '</details>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>{escape(sheet.title)}</title><style>{css}</style></head><body>{content}{source_key}</body></html>')


def render_html_pdf(html, sheet, *, on_preview=None, on_layout=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(java_script_enabled=False, viewport={"width": 1100, "height": 1200})
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            failures = page.evaluate("""() => [...document.querySelectorAll('.page')].flatMap((page, index) => {
                const body = page.querySelector('.body').getBoundingClientRect();
                const footer = page.querySelector('.footer').getBoundingClientRect();
                const images = [...page.querySelectorAll('img')];
                return [...(body.bottom > footer.top - 8 ? [`Page ${index + 1} content overlaps its footer by ${Math.ceil(body.bottom - footer.top + 8)} pixels`] : []),
                    ...(images.some(i => !i.complete || !i.naturalWidth) ? ['An original figure failed to render'] : [])];
            })""")
            if on_layout:
                on_layout(page.evaluate("""() => [...document.querySelectorAll('.page')].map(p => {
                    const top = p.getBoundingClientRect().top;
                    return (p.querySelector('.body').getBoundingClientRect().bottom - top) /
                        (p.querySelector('.footer').getBoundingClientRect().top - top);
                })"""))
            if on_preview:
                on_preview(page.pdf(format="A4", print_background=True, prefer_css_page_size=True))
            if failures:
                raise RevisionError("page_overflow", "; ".join(failures) + ". Shorten prose without removing essential conditions.")
            data = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if len(pdf) > 2:
            raise RevisionError("page_overflow", "Summary exceeds two A4 pages.")
        normalize = lambda value: re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
        extracted = normalize(''.join(p.get_text() for p in pdf))
        for item in sheet.items():
            if normalize(getattr(item, "text", getattr(item, "label", ""))) not in extracted:
                raise RevisionError("text_preservation", f"Rendered HTML lost item {item.id}.")
    if len(data) > 2_000_000:
        raise RevisionError("artifact_too_large", "The two-page PDF exceeds the artifact size limit.")
    return data
