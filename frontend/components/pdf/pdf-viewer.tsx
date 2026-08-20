"use client";

import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Minimize2,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { API_BASE, apiFetch } from "@/lib/api";
import {
  MAX_PDF_ZOOM,
  MIN_PDF_ZOOM,
  zoomStep,
} from "./zoom";
import { findExcerptRange } from "@/lib/pdf-match";
import { accessToken } from "@/lib/supabase";
import type { BookSourceResponse } from "@/lib/types";
import { documentKey, type PdfDocument, type PdfTarget } from "./target";
import { cn } from "@/lib/utils";

export { MAX_PDF_ZOOM, MIN_PDF_ZOOM, PDF_ZOOM_STEP, zoomStep } from "./zoom";

import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

// Served from our own origin, copied at install time so its version always
// matches pdfjs-dist. See scripts/copy-pdf-worker.mjs.
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

/** What pdf.js is handed: a plain URL, or one with the headers to fetch it. */
type DocumentSource = string | { url: string; httpHeaders: Record<string, string> };

/**
 * Resolve a document to something pdf.js can load.
 *
 * A book's original is in private storage and comes back as a short-lived
 * signed URL, so it is fetched per book rather than cached. A lecture's linked
 * document is served by the API itself and authenticates by bearer token,
 * which a plain URL cannot carry — pdf.js takes the header instead, so the
 * bytes stream and range requests still work. Downloading the whole file into
 * a blob first would defeat both.
 */
async function resolveSource(document: PdfDocument): Promise<DocumentSource> {
  if (document.kind === "book") {
    const payload = await apiFetch<BookSourceResponse>(
      `/books/${document.bookId}/source`,
    );
    return payload.url;
  }
  const token = await accessToken();
  return {
    url: `${API_BASE}/videos/${document.videoId}/resources/${document.resourceId}/content`,
    httpHeaders: token ? { Authorization: `Bearer ${token}` } : {},
  };
}

function PdfLoadingState({ label = "Loading document…" }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex h-96 w-full flex-col items-center justify-center gap-3 rounded-lg border border-divider bg-surface text-sm text-muted-foreground"
    >
      <Loader2 className="size-6 animate-spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

/** Elements whose own keyboard interaction must win over document shortcuts. */
function ownsArrowKeys(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable="true"], [role="combobox"], [role="dialog"], [role="listbox"], [role="menu"], [role="separator"], [role="slider"], [role="spinbutton"], [role="tablist"]',
    ),
  );
}

/**
 * Highlight the runs matching the excerpt, or the whole page when none match.
 *
 * Runs after the text layer renders, because pdf.js only exposes text items
 * once it has laid them out. Returns whether an exact passage was found, so
 * the caller can say plainly that it fell back.
 */
function highlightExcerpt(
  container: HTMLElement,
  excerpt: string | null | undefined,
): boolean {
  const spans = Array.from(
    container.querySelectorAll<HTMLElement>(".react-pdf__Page__textContent span"),
  );
  for (const span of spans) span.removeAttribute("data-cited");
  if (!excerpt || spans.length === 0) return false;

  const range = findExcerptRange(
    spans.map((span) => ({ str: span.textContent ?? "" })),
    excerpt,
  );
  if (!range) return false;

  for (let index = range.start; index <= range.end; index += 1) {
    spans[index]?.setAttribute("data-cited", "");
  }

  // Scroll only the PDF viewport. `scrollIntoView` also scrolls eligible
  // ancestors, which could move the conversation (or the whole app) when a
  // citation opened. An immediate jump is intentional: a smooth animation is
  // restarted whenever pdf.js lays the text layer out again and was the main
  // source of the visible citation flicker.
  const first = spans[range.start];
  if (first) {
    const viewportBounds = container.getBoundingClientRect();
    const passageBounds = first.getBoundingClientRect();
    const top =
      container.scrollTop +
      passageBounds.top -
      viewportBounds.top -
      (container.clientHeight - passageBounds.height) / 2;
    const left =
      container.scrollLeft +
      passageBounds.left -
      viewportBounds.left -
      (container.clientWidth - passageBounds.width) / 2;
    container.scrollTo({
      top: Math.max(0, top),
      left: Math.max(0, left),
      behavior: "auto",
    });
  }
  return true;
}

export function PdfViewer({
  target,
  page,
  onPageChange,
  zoom,
  onZoomChange,
  onMinimize,
  onClose,
}: {
  target: PdfTarget;
  page: number;
  onPageChange: (page: number) => void;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  /**
   * Both optional, because the pane is not always a panel. In a reading
   * session the document *is* the page: there is nothing to minimize it to,
   * and closing it would leave the reader looking at nothing. Omitting them
   * removes the controls rather than leaving two buttons that cannot mean
   * anything.
   */
  onMinimize?: () => void;
  onClose?: () => void;
}) {
  const [source, setSource] = useState<DocumentSource | null>(null);
  const [error, setError] = useState("");
  const [pageCount, setPageCount] = useState(0);
  const [exactMatch, setExactMatch] = useState<boolean | null>(null);
  // Zero until the pane is measured. A guessed starting width is drawn once
  // at that size before any correction arrives, and on a narrow pane that is a
  // page rendered several times too large with only its top-left corner in
  // view. Rendering nothing for one frame is the honest alternative.
  const [width, setWidth] = useState(0);

  const containerRef = useRef<HTMLDivElement | null>(null);

  // Keyed by document identity, not by the target: moving between citations
  // inside one document must not reload it.
  const key = documentKey(target.document);
  // Not named `document`: the keyboard handler below needs the global one.
  const pdfDocument = target.document;
  useEffect(() => {
    let cancelled = false;
    setSource(null);
    setError("");
    setPageCount(0);
    setExactMatch(null);
    (async () => {
      try {
        const resolved = await resolveSource(pdfDocument);
        if (!cancelled) setSource(resolved);
      } catch (caught) {
        if (!cancelled) {
          const detail = (caught as Error).message ?? "";
          // API messages are written for readers; anything containing a URL
          // is not, and would leak a signed token onto the page.
          setError(
            detail && !detail.includes("http")
              ? detail
              : "This document's original file could not be opened.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // `document` is recreated on every render by the caller; its identity is
    // the key, so depending on the object itself would refetch continuously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    setExactMatch(null);
  }, [key, target.page, target.excerpt]);

  // Render at the pane's width so the page fills it without horizontal scroll.
  //
  // Measured synchronously before paint as well as observed, because the
  // observer's first callback arrives after a frame has already been drawn.
  // The floor is the pane's own width rather than a constant: a 280px minimum
  // on a 170px pane is how a page ends up wider than the box holding it.
  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    let resizeTimer: number | null = null;
    const measure = (available: number) => {
      const usable = available - 32;
      if (usable > 0) {
        const next = Math.round(usable);
        setWidth((current) => (current === next ? current : next));
      }
    };

    measure(element.clientWidth);
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      // Dragging the divider can report dozens of widths per second. pdf.js
      // replaces the canvas while honoring each one, which looks like the page
      // is flashing. Keep the existing page visible and render once the pane
      // has settled instead.
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(
        () => measure(entry.contentRect.width),
        100,
      );
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
    };
  }, []);

  const onPageRendered = useCallback(() => {
    const element = containerRef.current;
    if (!element) return;
    // Only the page the citation pointed at carries a highlight; paging away
    // and back should not re-assert it on a different page.
    if (page !== target.page) {
      setExactMatch(null);
      return;
    }
    setExactMatch(highlightExcerpt(element, target.excerpt));
  }, [page, target.page, target.excerpt]);

  const setZoomWithinLimits = useCallback(
    (next: number) => {
      onZoomChange(Math.min(MAX_PDF_ZOOM, Math.max(MIN_PDF_ZOOM, next)));
    },
    [onZoomChange],
  );

  const zoomPercent = Math.round(zoom * 100);

  // The reader often keeps focus in the answer while consulting the document,
  // so the page shortcut belongs to the open viewer rather than to one focused
  // toolbar button. Editable and composite controls retain their native arrow
  // behaviour, and modifiers are left to the browser/operating system.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        ownsArrowKeys(event.target) ||
        document.querySelector('[role="dialog"]')
      ) {
        return;
      }

      if (event.key === "ArrowLeft" && page > 1) {
        event.preventDefault();
        onPageChange(page - 1);
      } else if (
        event.key === "ArrowRight" &&
        (pageCount === 0 || page < pageCount)
      ) {
        event.preventDefault();
        onPageChange(page + 1);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onPageChange, page, pageCount]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-card">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{target.title}</p>
        </div>
        {onMinimize && (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Minimize the document"
            onClick={onMinimize}
          >
            <Minimize2 aria-hidden />
          </Button>
        )}
        {onClose && (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Close the document"
            onClick={onClose}
          >
            <X aria-hidden />
          </Button>
        )}
      </header>

      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2">
        <div
          role="group"
          aria-label="Document page navigation. Use Left and Right Arrow keys."
          className="flex items-center gap-1"
        >
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Previous page"
            disabled={page <= 1}
            onClick={() => onPageChange(Math.max(1, page - 1))}
          >
            <ChevronLeft aria-hidden />
          </Button>
          <p
            className="min-w-20 text-center text-xs tabular-nums text-muted-foreground"
            aria-live="polite"
          >
            {pageCount ? `${page} / ${pageCount}` : `Page ${page}`}
          </p>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Next page"
            disabled={pageCount > 0 && page >= pageCount}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRight aria-hidden />
          </Button>
          {/*
            A hint, not a control. These sat immediately beside the real page
            buttons wearing the same border and the same size, so they read as
            two more buttons and got clicked — reasonably, since nothing said
            otherwise. They now say what they are, take no pointer events, and
            carry no button-like edge.
          */}
          <span
            aria-hidden
            className="ml-2 hidden select-none items-center gap-1 text-xs text-muted-foreground pointer-events-none xl:flex"
          >
            <kbd className="rounded-sm bg-surface-hover px-1 font-sans">←</kbd>
            <kbd className="rounded-sm bg-surface-hover px-1 font-sans">→</kbd>
            <span>to turn pages</span>
          </span>
        </div>

        <div
          role="group"
          aria-label="Document zoom"
          className="flex items-center gap-1"
        >
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Zoom out"
            disabled={zoom <= MIN_PDF_ZOOM}
            onClick={() => setZoomWithinLimits(zoomStep(zoom, -1))}
          >
            <ZoomOut aria-hidden />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="min-w-14 px-2 text-xs tabular-nums"
            aria-label={`Reset zoom to 100%. Current zoom ${zoomPercent}%`}
            onClick={() => setZoomWithinLimits(1)}
          >
            {zoomPercent}%
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Zoom in"
            disabled={zoom >= MAX_PDF_ZOOM}
            onClick={() => setZoomWithinLimits(zoomStep(zoom, 1))}
          >
            <ZoomIn aria-hidden />
          </Button>
        </div>
      </div>

      {exactMatch === false && target.excerpt && (
        <p className="shrink-0 border-b border-border bg-surface px-3 py-2 text-xs text-muted-foreground">
          Showing the cited page. The exact passage could not be located in
          this page&apos;s text.
        </p>
      )}

      <div
        ref={containerRef}
        className="min-h-0 w-full flex-1 overflow-auto overscroll-contain p-4 [scrollbar-gutter:stable]"
      >
        {error ? (
          <Alert variant="destructive">
            <AlertCircle aria-hidden />
            <AlertTitle>Cannot open this document</AlertTitle>
            <AlertDescription className="break-words">{error}</AlertDescription>
          </Alert>
        ) : !source ? (
          <PdfLoadingState />
        ) : (
          <Document
            file={source}
            onLoadSuccess={({ numPages }) => setPageCount(numPages)}
            onLoadError={(cause) => {
              // pdf.js puts the whole signed URL in its message, token and
              // all. Log it for debugging and show the reader a sentence.
              console.error("PDF load failed", cause);
              setError("The document could not be read.");
            }}
            loading={<PdfLoadingState label="Opening document…" />}
            className={cn("flex w-max min-w-full justify-center")}
          >
            {width > 0 && (
              <Page
                pageNumber={page}
                width={Math.round(width * zoom)}
                renderAnnotationLayer={false}
                onRenderTextLayerSuccess={onPageRendered}
                // Without this a failed page render is silent: no message, no
                // log, just an empty box that reads as a hung viewer.
                onRenderError={(cause) => {
                  console.error("PDF page render failed", cause);
                  setError("This page could not be rendered.");
                }}
                loading={<PdfLoadingState label={`Loading page ${page}…`} />}
                className="shrink-0 shadow-sm"
              />
            )}
          </Document>
        )}
      </div>
    </div>
  );
}
