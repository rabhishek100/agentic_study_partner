"use client";

import { useId } from "react";
import type { RevisionItem, RevisionReference, RevisionSheet } from "@/lib/revision-types";

export function RevisionCitations({ markers, references, onSource }: {
  markers: string[]; references: Record<string, RevisionReference>; onSource: (ref: RevisionReference) => void;
}) {
  return <span className="inline-flex flex-wrap gap-1 align-baseline">{[...new Set(markers)].map(marker => {
    const ref = references[marker];
    return ref ? <button type="button" key={marker} onClick={() => onSource(ref)}
      title={`${ref.path} · PDF page ${ref.page}`} aria-label={`Open ${ref.section_title}, PDF page ${ref.page}`}
      className="min-h-6 rounded px-1 text-xs text-primary underline decoration-primary/30 underline-offset-2 hover:bg-wash focus-visible:outline-2 focus-visible:outline-primary">
      {ref.section_number} · p. {ref.page}
    </button> : null;
  })}</span>;
}

export function RevisionNote({ item, references, onSource }: {
  item: RevisionItem; references: Record<string, RevisionReference>; onSource: (ref: RevisionReference) => void;
}) {
  return <p className="text-sm leading-relaxed" id={`revision-${item.id}`}>
    {item.heading && <strong className="font-semibold">{item.heading}. </strong>}{item.text}{" "}
    <RevisionCitations markers={item.citations} references={references} onSource={onSource} />
  </p>;
}

export function RevisionPage({ sheet, onSource }: { sheet: RevisionSheet; onSource: (ref: RevisionReference) => void }) {
  const { content: c, source_references: refs, diagram_layout: layout } = sheet;
  const marker = useId().replace(/:/g, "");
  const nodes = new Map(c.diagram.nodes.map(n => [n.id, n]));
  const notes = (items: RevisionItem[]) => items.map(item => <RevisionNote key={item.id} item={item} references={refs} onSource={onSource} />);
  return <article className="mx-auto w-full max-w-4xl rounded-xl border border-border bg-card px-5 py-7 text-foreground sm:px-9 sm:py-9">
    <p className="mb-3 text-xs font-medium uppercase tracking-wider text-primary">Revision sheet · {sheet.source_title}</p>
    <h2 className="mb-4 font-serif text-2xl font-semibold tracking-tight sm:text-3xl">{c.title}</h2>
    <RevisionNote item={c.central_idea} references={refs} onSource={onSource} />
    <section className="my-6 border-y border-border py-5" aria-label="Overview diagram">
      <h3 className="mb-2 text-sm font-semibold text-primary">Overview · simplified</h3>
      <p className="text-sm leading-relaxed">{c.diagram.description}{" "}<RevisionCitations markers={c.diagram.description_citations} references={refs} onSource={onSource} /></p>
      <div className="my-3 overflow-x-auto focus-visible:outline-2 focus-visible:outline-primary" tabIndex={0} role="region" aria-label="Scrollable overview diagram">
        <svg viewBox={`0 0 ${layout.width} ${layout.height}`} className="w-full min-w-[480px]" role="img"
          aria-label={`${c.diagram.description} The relationships are listed immediately below.`}>
          <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" className="fill-primary" /></marker></defs>
          {layout.edges.map(edge => <polyline key={edge.id} points={edge.points.map(p => p.join(",")).join(" ")}
            fill="none" className="stroke-primary" strokeWidth="1" markerEnd={`url(#${marker})`} />)}
          {layout.nodes.map(n => <g key={n.id}>
            <rect x={n.x} y={n.y} width={n.width} height={n.height} rx="4" className="fill-wash stroke-primary" strokeWidth="0.7" />
            <foreignObject x={n.x + 5} y={n.y + 3} width={n.width - 10} height={n.height - 6}>
              <div className="flex h-full items-center justify-center text-center text-xs font-semibold leading-tight">{nodes.get(n.id)?.label}</div>
            </foreignObject>
          </g>)}
        </svg>
      </div>
      <ul className="space-y-2">
        {c.diagram.edges.map(edge => <li key={edge.id} className="text-sm leading-relaxed">
          <strong>{nodes.get(edge.source)?.label} → {nodes.get(edge.target)?.label}:</strong>{" "}{edge.label}{" "}
          <RevisionCitations markers={edge.citations} references={refs} onSource={onSource} />
        </li>)}
      </ul>
      <details className="mt-3 text-xs text-muted-foreground"><summary className="min-h-6 cursor-pointer">Component sources</summary>
        {c.diagram.nodes.map(node => <p key={node.id}>{node.label}{" "}<RevisionCitations markers={node.citations} references={refs} onSource={onSource} /></p>)}
      </details>
    </section>
    <div className="grid gap-6 sm:grid-cols-2 sm:gap-9">
      <section className="space-y-3"><h3 className="text-sm font-semibold text-primary">Essential concepts</h3>
        {notes(c.essential_notes)}{c.equation && <RevisionNote item={c.equation} references={refs} onSource={onSource} />}
      </section>
      <div className="space-y-5"><section className="space-y-3"><h3 className="text-sm font-semibold text-primary">{c.template_kind === "paper" ? "Results & limitations" : "Trade-offs & failure modes"}</h3>{notes(c.comparison_rows)}</section>
        <section className="space-y-3"><h3 className="text-sm font-semibold text-primary">Recall</h3>{notes(c.recall_cues)}</section>
      </div>
    </div>
    <p className="mt-7 border-t border-border pt-3 text-xs text-muted-foreground">{sheet.scope_title} · Source citations use PDF page numbers.</p>
  </article>;
}
