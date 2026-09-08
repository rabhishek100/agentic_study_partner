"use client";

import { useEffect, useRef, useState } from "react";
import type { RevisionReference, RevisionSheet } from "@/lib/revision-types";

/** Owned, script-free template. Source links stay in the authenticated viewer. */
export function RevisionHtml({ sheet, onSource }: { sheet: RevisionSheet; onSource: (ref: RevisionReference) => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(2300);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    let disconnect = () => {};
    const connect = () => {
      disconnect();
      const doc = element.contentDocument;
      if (!doc?.body) return;
      const measure = () => {
        // scrollHeight may include the viewport and create a resize loop.
        const bottom = Math.max(200, ...Array.from(doc.body.children).map(child => child.getBoundingClientRect().bottom));
        const next = Math.ceil(bottom + (doc.defaultView?.scrollY ?? 0) + 24);
        setHeight(previous => Math.abs(previous - next) > 1 ? next : previous);
      };
      const resize = new ResizeObserver(measure);
      resize.observe(doc.body);
      const follow = (event: MouseEvent) => {
        const link = (event.target as Element | null)?.closest("a");
        if (!link) return;
        event.preventDefault();
        const marker = link.getAttribute("href")?.replace(/^#source-/, "");
        if (marker && sheet.source_references[marker]) onSource(sheet.source_references[marker]);
      };
      doc.addEventListener("click", follow);
      measure();
      disconnect = () => { resize.disconnect(); doc.removeEventListener("click", follow); };
    };
    element.addEventListener("load", connect);
    // srcdoc can finish loading before React hydrates. Connect immediately too.
    connect();
    return () => { disconnect(); element.removeEventListener("load", connect); };
  }, [sheet.provenance.html, sheet.source_references, onSource]);
  return <iframe ref={frame} title={`${sheet.content.title} — printable revision summary`}
    srcDoc={sheet.provenance.html} sandbox="allow-same-origin"
    className="w-full rounded-lg border border-border bg-card" style={{ height }} />;
}
