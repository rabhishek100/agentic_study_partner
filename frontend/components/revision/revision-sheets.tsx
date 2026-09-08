"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PdfViewer } from "@/components/pdf";
import { RevisionHtml } from "./revision-html";
import { InlineFigure } from "@/components/conversation/figures";
import { apiFetch, errorDetail } from "@/lib/api";
import { accessToken } from "@/lib/supabase";
import type { BookSummary, FigureRef } from "@/lib/types";
import type { RevisionAnswer, RevisionCreated, RevisionJob, RevisionList, RevisionReference, RevisionSheet } from "@/lib/revision-types";
import { RevisionNote, RevisionPage } from "./revision-page";

const STAGES: Record<string, string> = { inspecting_figures: "Inspecting every original figure", inventorying_concepts: "Mapping important concepts", reviewing_quality: "Reviewing coverage and page design", revising_sheet: "Refining the sheet from review feedback", queued: "Queued for generation", reading_source: "Reading the complete source", composing: "Composing your revision sheet", checking_content: "Checking content and citations", preparing_page: "Laying out the printable pages", publishing: "Saving your sheet" };
const active = (job: RevisionJob | null) => !!job && ["queued", "running"].includes(job.status);
const selectStyle = "h-10 w-full rounded-md border border-border bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50";
type Chapter = { node_id: number; title: string; start_page: number; end_page: number };

function hashSheet() {
  return /^#revision=([0-9a-f-]{36})$/i.exec(window.location.hash)?.[1] ?? null;
}

export function RevisionSheets({ books, selectedBookIds, noun }: {
  books: BookSummary[]; selectedBookIds: number[]; noun: "book" | "paper";
}) {
  const [open, setOpen] = useState(false);
  const [sheetId, setSheetId] = useState<string | null>(null);
  const [sheet, setSheet] = useState<RevisionSheet | null>(null);
  const [library, setLibrary] = useState<RevisionList>({ sheets: [], jobs: [] });
  const [bookId, setBookId] = useState("");
  const [chapterId, setChapterId] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [loadingChapters, setLoadingChapters] = useState(false);
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<RevisionJob | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const mutation = useRef(false);
  const requestKeys = useRef(new Map<string, string>());
  const [pdf, setPdf] = useState("");
  const [htmlDownload, setHtmlDownload] = useState("");
  useEffect(() => {
    if (!sheet?.provenance.html) { setHtmlDownload(""); return; }
    const url = URL.createObjectURL(new Blob([sheet.provenance.html], { type: "text/html" }));
    setHtmlDownload(url);
    return () => URL.revokeObjectURL(url);
  }, [sheet]);
  const [pdfError, setPdfError] = useState("");
  const [pdfRetry, setPdfRetry] = useState(0);
  const [source, setSource] = useState<RevisionReference | null>(null);
  const [sourcePage, setSourcePage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<RevisionAnswer | null>(null);
  const [answerPending, setAnswerPending] = useState(false);
  const questionController = useRef<AbortController | null>(null);

  const showSheet = useCallback((id: string | null) => {
    setSheetId(id); setSource(null); setAnswer(null); setAsking(false); setJob(null);
    questionController.current?.abort(); questionController.current = null; setAnswerPending(false);
    const url = new URL(window.location.href);
    url.hash = id ? `revision=${id}` : "";
    if (window.location.hash !== url.hash) window.history.pushState(window.history.state, "", url);
  }, []);

  useEffect(() => {
    const sync = () => { const id = hashSheet(); setSheetId(id); setOpen(!!id); setJob(null); };
    sync(); window.addEventListener("popstate", sync); window.addEventListener("hashchange", sync);
    return () => { window.removeEventListener("popstate", sync); window.removeEventListener("hashchange", sync); questionController.current?.abort(); };
  }, []);

  const refresh = useCallback(async () => {
    const value = await apiFetch<RevisionList>(`/revision-sheets?document_type=${noun}`);
    setLibrary(value);
  }, [noun]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setError("");
    apiFetch<RevisionList>(`/revision-sheets?document_type=${noun}`).then(value => { if (alive) setLibrary(value); })
      .catch(e => { if (alive) setError(e.message); });
    if (!bookId && selectedBookIds.length === 1) setBookId(String(selectedBookIds[0]));
    return () => { alive = false; };
    // Selection changes underneath the dialog must not reset the user's explicit choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, noun]);

  useEffect(() => {
    setChapterId(""); setChapters([]); setLoadingChapters(false);
    if (!bookId || noun === "paper") return;
    const controller = new AbortController();
    setLoadingChapters(true);
    apiFetch<{ chapters: Chapter[] }>(`/books/${bookId}/chapters`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setChapters(data.chapters); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoadingChapters(false); });
    return () => controller.abort();
  }, [bookId, noun]);

  useEffect(() => {
    questionController.current?.abort(); questionController.current = null;
    setAnswerPending(false); setAnswer(null); setSource(null); setAsking(false); setQuestion("");
    if (!open || !sheetId) { setSheet(null); setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError(""); setSheet(null);
    apiFetch<RevisionSheet>(`/revision-sheets/${sheetId}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setSheet(value); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, sheetId]);

  useEffect(() => {
    if (!open || !job || !active(job)) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const updated = await apiFetch<RevisionJob>(`/revision-sheet-jobs/${job!.id}`, { signal: controller.signal });
        if (!alive) return;
        setJob(updated);
        if (updated.status === "ready" && updated.sheet_id) { showSheet(updated.sheet_id); await refresh(); }
        else if (active(updated)) timer = setTimeout(poll, 2000);
        else await refresh();
      } catch (e) {
        if (alive) { setError((e as Error).message); timer = setTimeout(poll, 5000); }
      }
    }
    void poll();
    return () => { alive = false; controller.abort(); clearTimeout(timer); };
    // A status refresh must not restart an already scheduled polling loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, job?.id, showSheet, refresh]);

  useEffect(() => {
    setPdf(""); setPdfError("");
    if (!open || !sheetId) return;
    const controller = new AbortController(); let url = "";
    void (async () => {
      try {
        const token = await accessToken();
        const response = await fetch(`/api/revision-sheets/${sheetId}/pdf`, { signal: controller.signal, headers: token ? { Authorization: `Bearer ${token}` } : {} });
        if (!response.ok) throw new Error(await errorDetail(response));
        const blob = await response.blob();
        if (!controller.signal.aborted) { url = URL.createObjectURL(blob); setPdf(url); }
      } catch (e) { if (!controller.signal.aborted) setPdfError((e as Error).message); }
    })();
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [open, sheetId, pdfRetry]);

  const scopeKey = bookId ? noun === "paper" ? `paper:${bookId}` : chapterId ? `book:${bookId}:chapter:${chapterId}` : "" : "";
  const saved = library.sheets.find(s => s.scope_key === scopeKey);
  const scopeJob = library.jobs.find(j => j.scope_key === scopeKey && active(j));

  async function mutate(path: string, body?: unknown) {
    if (mutation.current) return;
    mutation.current = true; setSubmitting(true); setError("");
    const identity = `${path}:${JSON.stringify(body)}`;
    let key = requestKeys.current.get(identity);
    if (!key) { key = crypto.randomUUID(); requestKeys.current.set(identity, key); }
    try {
      const result = await apiFetch<RevisionCreated>(path, { method: "POST", headers: { "Idempotency-Key": key }, ...(body ? { body: JSON.stringify(body) } : {}) });
      requestKeys.current.delete(identity);
      if (result.sheet) showSheet(result.sheet.id);
      if (result.job) {
        setJob(result.job);
        if (result.job.status === "ready" && result.job.sheet_id) showSheet(result.job.sheet_id);
      }
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { mutation.current = false; setSubmitting(false); }
  }

  function openSource(ref: RevisionReference) { setSource(ref); setSourcePage(ref.page); setZoom(1); }
  function changeOpen(value: boolean) {
    setOpen(value);
    if (!value) {
      questionController.current?.abort(); setAnswerPending(false);
      const url = new URL(window.location.href); url.hash = "";
      window.history.replaceState(window.history.state, "", url); setSheetId(null);
    }
  }

  async function askQuestion(event: React.FormEvent) {
    event.preventDefault();
    if (!sheet || !question.trim() || questionController.current) return;
    const controller = new AbortController(); questionController.current = controller;
    setAnswerPending(true); setError(""); setAnswer(null);
    try {
      const result = await apiFetch<RevisionAnswer>(`/revision-sheets/${sheet.id}/ask`, { method: "POST", signal: controller.signal, body: JSON.stringify({ question: question.trim() }) });
      if (!controller.signal.aborted) setAnswer(result);
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    finally { if (questionController.current === controller) { questionController.current = null; setAnswerPending(false); } }
  }

  return <>
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild><Button type="button" variant="ghost" size="sm" disabled={!books.length}><FileText aria-hidden className="size-3.5" />Revision sheet</Button></DialogTrigger>
      <DialogContent className="flex max-h-[92dvh] flex-col gap-4 overflow-hidden sm:max-w-5xl">
        <div className="shrink-0 pr-8"><DialogTitle>{sheet ? sheet.scope_title : "Revision sheets"}</DialogTitle>
          <DialogDescription>{sheet ? `${sheet.source_title} · Saved version ${sheet.version}` : "One chapter or an entire paper, condensed for repeated review. Up to five printable A4 pages."}</DialogDescription></div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {job && <div className="shrink-0 rounded-lg border border-border bg-wash p-3 text-sm">
          <p role="status">{active(job) ? job.cancellation_requested ? "Cancellation requested" : STAGES[job.stage] ?? "Generating sheet" : job.status === "failed" ? job.error_detail : job.status === "cancelled" ? "Generation cancelled" : "Sheet saved"} · {job.scope_title}</p>
          {active(job) && <Button type="button" variant="ghost" size="sm" disabled={job.cancellation_requested} onClick={() => {
            void apiFetch<RevisionJob>(`/revision-sheet-jobs/${job.id}/cancel`, { method: "POST" }).then(setJob).catch(e => setError(e.message));
          }}>Cancel generation</Button>}
          {/*
            No retry when the provider refused on spend: the same request would
            be refused again, and offering the button is what sent readers round
            a retry loop while an exhausted monthly key limit stayed exhausted.
            The failure detail already says what to change.
          */}
          {["failed", "cancelled"].includes(job.status) && job.error_code !== "provider_quota_exhausted" && <Button type="button" variant="ghost" size="sm" disabled={submitting} onClick={() => void mutate(`/revision-sheet-jobs/${job.id}/retry`)}>Retry generation</Button>}
        </div>}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {loading && <p role="status" className="p-6 text-sm text-muted-foreground">Loading saved sheet…</p>}
          {sheet ? <>
            <div className="mb-4 flex flex-wrap gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => showSheet(null)}>Saved sheets</Button>
              {pdf && <><Button asChild variant="outline" size="sm"><a href={pdf} download={`revision-${sheet.id}.pdf`}>Download PDF</a></Button>
                <Button asChild variant="outline" size="sm"><a href={pdf} target="_blank" rel="noopener noreferrer">Print / A4 preview</a></Button></>}
              {htmlDownload && <Button asChild variant="outline" size="sm"><a href={htmlDownload} download={`revision-${sheet.id}.html`}>Download HTML</a></Button>}
              {!pdf && !pdfError && <span role="status" className="self-center text-xs text-muted-foreground">Loading A4 export…</span>}
              {pdfError && <Button type="button" variant="outline" size="sm" onClick={() => setPdfRetry(n => n + 1)}>Retry PDF download</Button>}
              <Button type="button" variant="outline" size="sm" disabled={submitting || active(job)} onClick={() => void mutate(`/revision-sheets/${sheet.id}/regenerate`)}><RefreshCw aria-hidden className="size-3.5" />Regenerate</Button>
              <Button type="button" variant="outline" size="sm" onClick={() => setAsking(v => !v)}>Ask about this</Button>
            </div>
            {(sheet.source_changed || sheet.settings_changed) && <p className="mb-4 text-sm text-muted-foreground">{sheet.source_changed ? "The source has changed since this sheet was saved." : "New generation settings are available."} Regenerate when you want a new version.</p>}
            {sheet.provenance.html ? <RevisionHtml sheet={sheet} onSource={openSource} /> : <RevisionPage sheet={sheet} onSource={openSource} />}
            {/*
              Stated before the review panel and never inside a collapsed
              section: a sheet published with known gaps has to say so where a
              reader will see it, or the fallback that stopped generation from
              failing becomes a way of quietly shipping a worse sheet.
            */}
            {sheet.provenance.outstanding_findings?.length ? <div role="note" className="mt-4 rounded-lg border border-border bg-wash p-4 text-sm">
              <p className="font-medium">Published with {sheet.provenance.outstanding_findings.length} known {sheet.provenance.outstanding_findings.length === 1 ? "gap" : "gaps"}</p>
              <p className="mt-1 text-xs text-muted-foreground">The independent reviewer still wanted more after its revisions were spent. The sheet is usable; these points are where it is thin, so check the source before relying on them.</p>
              <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">{sheet.provenance.outstanding_findings.map((finding, index) => <li key={index}>{finding}</li>)}</ul>
            </div> : null}
            {sheet.provenance.review && <details className="mt-4 rounded-lg border border-border p-4 text-sm"><summary className="cursor-pointer font-medium">Independent quality review</summary>
              <dl className="mt-3 grid gap-3 sm:grid-cols-2">{([ ["beauty", "Visual design"], ["presentation", "Presentation"], ["concept_coverage", "Concept coverage"], ["conciseness", "Conciseness"] ] as const).map(([key, label]) => <div key={key}><dt className="font-medium">{label} · {sheet.provenance.review![key].score}/5</dt><dd className="mt-1 text-xs text-muted-foreground">{sheet.provenance.review![key].rationale}</dd></div>)}</dl>
              <p className="mt-3 text-xs text-muted-foreground">Reviewed against the source and rendered pages. Automated review is an assessment, not a guarantee.</p>
            </details>}
            {(sheet.provenance as RevisionSheet["provenance"] & { figure_references?: FigureRef[] }).figure_references?.length ? <details className="mt-4 rounded-lg border border-border p-4"><summary className="cursor-pointer text-sm font-medium">{sheet.provenance.html ? "All original figures" : "Inspected original figures"} · {sheet.provenance.inspected_figures.length}</summary><p className="mt-2 text-xs text-muted-foreground">The complete source gallery accompanies the compact summary. Select any figure to enlarge it.</p><div className="grid gap-4 sm:grid-cols-2">{(sheet.provenance as RevisionSheet["provenance"] & { figure_references?: FigureRef[] }).figure_references?.map(f => <InlineFigure key={f.block_id} figure={f} />)}</div></details> : null}
            {asking && <section className="mt-5 rounded-lg border border-border p-4">
              <h3 className="mb-2 font-medium">Ask about {sheet.scope_title}</h3>
              <p className="mb-3 text-xs text-muted-foreground">Answers use the original chapter or paper. {sheet.source_changed ? "The current source is newer than this sheet." : ""}</p>
              <form onSubmit={askQuestion} className="space-y-3"><Label htmlFor="revision-question">Your question</Label><Textarea id="revision-question" value={question} onChange={e => setQuestion(e.target.value)} maxLength={2000} />
                <Button type="submit" disabled={answerPending || !question.trim()}>{answerPending ? "Reading source…" : "Ask"}</Button></form>
              {answer && <div className="mt-4 space-y-3">{answer.insufficient_evidence && <p>{answer.insufficient_evidence}</p>}{answer.items.map(item => <RevisionNote key={item.id} item={item} references={answer.source_references} onSource={openSource} />)}</div>}
            </section>}
            <details className="mt-5 rounded-lg border border-border p-4 text-sm"><summary className="cursor-pointer font-medium">Sources and compression details</summary>
              <ul className="mt-3 list-disc space-y-2 pl-5">{sheet.content.compression_notes.map((note, i) => <li key={i}>{note}</li>)}</ul>
              <p className="mt-3 text-xs text-muted-foreground">{sheet.provenance.model} · {sheet.provenance.prompt_version} · {sheet.provenance.content_repairs} content repairs · {sheet.provenance.fit_repairs} layout repairs. Citation locations are checked; semantic completeness is not an automatic guarantee.</p>
              {sheet.provenance.uninspected_figures.length > 0 && <p className="mt-2 text-xs">{sheet.provenance.uninspected_figures.length} source figures were not inspected.</p>}
              <details className="mt-3"><summary className="cursor-pointer">Source coverage ledger</summary><ul className="mt-2 space-y-2">{sheet.content.source_dispositions.map(d => <li key={d.source_unit} className="text-xs">{d.source_unit}: {d.item_ids.length ? `represented by ${d.item_ids.join(", ")}` : d.reason}</li>)}</ul></details>
            </details>
          </> : !loading && <div className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="revision-source">{noun === "paper" ? "Paper" : "Book"}</Label>
              <select id="revision-source" className={selectStyle} value={bookId} onChange={e => { setBookId(e.target.value); setJob(null); }}><option value="">Choose a {noun}</option>{books.map(b => <option key={b.book_id} value={b.book_id}>{b.title}</option>)}</select></div>
              {noun === "book" ? <div className="space-y-2"><Label htmlFor="revision-chapter">Chapter</Label><select id="revision-chapter" className={selectStyle} value={chapterId} disabled={!bookId || loadingChapters} onChange={e => { setChapterId(e.target.value); setJob(null); }}><option value="">{loadingChapters ? "Loading chapters…" : "Choose a chapter"}</option>{chapters.map(c => <option key={c.node_id} value={c.node_id}>{c.title} · pp. {c.start_page}–{c.end_page}</option>)}</select></div> : <p className="self-end py-3 text-sm text-muted-foreground">Scope: Entire paper</p>}
            </div>
            <Button type="button" disabled={!scopeKey || submitting} onClick={() => {
              if (saved) showSheet(saved.id);
              else if (scopeJob) setJob(scopeJob);
              else void mutate("/revision-sheets", { scope_kind: noun === "paper" ? "paper" : "chapter", book_id: Number(bookId), ...(noun === "book" ? { chapter_node_id: Number(chapterId) } : {}) });
            }}>{saved ? "Open saved sheet" : scopeJob ? "View generation" : submitting ? "Queueing…" : "Create sheet"}</Button>
            <section><h3 className="mb-3 text-sm font-semibold">Saved sheets</h3>
              {!library.sheets.length && <p className="text-sm text-muted-foreground">Your saved chapter and paper reviews will appear here.</p>}
              <ul className="space-y-2">{library.sheets.map(s => <li key={s.id}><button type="button" className="w-full rounded-lg border border-border p-3 text-left hover:bg-wash focus-visible:outline-2 focus-visible:outline-primary" onClick={() => showSheet(s.id)}><span className="block text-sm font-medium">{s.scope_title}</span><span className="text-xs text-muted-foreground">{s.source_title} · Version {s.version} · {new Date(s.created_at).toLocaleDateString()}</span></button></li>)}</ul>
            </section>
            {library.jobs.some(j => j.status !== "ready") && <section><h3 className="mb-3 text-sm font-semibold">Recent generation</h3><ul className="space-y-2">{library.jobs.filter(j => j.status !== "ready").slice(0, 8).map(j => <li key={j.id}><Button type="button" variant="ghost" className="h-auto whitespace-normal text-left" onClick={() => setJob(j)}>{j.scope_title} · {j.status}</Button></li>)}</ul></section>}
          </div>}
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!source} onOpenChange={value => { if (!value) setSource(null); }}><DialogContent className="flex h-[90dvh] flex-col sm:max-w-5xl"><DialogTitle>Source evidence</DialogTitle><DialogDescription>{source?.path}</DialogDescription>
      {source && <div className="min-h-0 flex-1 overflow-hidden"><PdfViewer target={{ document: { kind: "book", bookId: source.book_id }, title: source.section_title, page: source.page }} page={sourcePage} onPageChange={setSourcePage} zoom={zoom} onZoomChange={setZoom} /></div>}
    </DialogContent></Dialog>
  </>;
}
