"use client";

import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
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
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api";
import { findExcerptRange } from "@/lib/pdf-match";
import type { BookSourceResponse } from "@/lib/types";
import type { PdfTarget } from "./target";
import { cn } from "@/lib/utils";

import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

// Served from our own origin, copied at install time so its version always
// matches pdfjs-dist. See scripts/copy-pdf-worker.mjs.
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

export const MIN_PDF_ZOOM = 0.5;
export const MAX_PDF_ZOOM = 2;
export const PDF_ZOOM_STEP = 0.25;

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
  onMinimize: () => void;
  onClose: () => void;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pageCount, setPageCount] = useState(0);
  const [exactMatch, setExactMatch] = useState<boolean | null>(null);
  // Zero until the pane is measured. A guessed starting width is drawn once
  // at that size before any correction arrives, and on a narrow pane that is a
  // page rendered several times too large with only its top-left corner in
  // view. Rendering nothing for one frame is the honest alternative.
  const [width, setWidth] = useState(0);

  const containerRef = useRef<HTMLDivElement | null>(null);

  // A signed URL is short-lived, so it is fetched per book rather than cached.
  useEffect(() => {
    let cancelled = false;
    setSource(null);
    setError("");
    setPageCount(0);
    setExactMatch(null);
    (async () => {
      try {
        const payload = await apiFetch<BookSourceResponse>(
          `/books/${target.bookId}/source`,
        );
        if (!cancelled) setSource(payload.url);
      } catch (caught) {
        if (!cancelled) {
          const detail = (caught as Error).message ?? "";
          // API messages are written for readers; anything containing a URL
          // is not, and would leak a signed token onto the page.
          setError(
            detail && !detail.includes("http")
              ? detail
              : "This book's original file could not be opened.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [target.bookId]);

  useEffect(() => {
    setExactMatch(null);
  }, [target.bookId, target.page, target.excerpt]);

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

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-card">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{target.bookTitle}</p>
        </div>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Minimize the document"
          onClick={onMinimize}
        >
          <Minimize2 aria-hidden />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Close the document"
          onClick={onClose}
        >
          <X aria-hidden />
        </Button>
      </header>

      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-1.5">
        <div
          role="group"
          aria-label="Document page navigation"
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
            onClick={() => setZoomWithinLimits(zoom - PDF_ZOOM_STEP)}
          >
            <ZoomOut aria-hidden />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="min-w-14 px-1.5 text-xs tabular-nums"
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
            onClick={() => setZoomWithinLimits(zoom + PDF_ZOOM_STEP)}
          >
            <ZoomIn aria-hidden />
          </Button>
        </div>
      </div>

      {exactMatch === false && target.excerpt && (
        <p className="shrink-0 border-b border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
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
            <AlertTitle>Cannot open this book</AlertTitle>
            <AlertDescription className="break-words">{error}</AlertDescription>
          </Alert>
        ) : !source ? (
          <Skeleton className="h-96 w-full" />
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
            loading={<Skeleton className="h-96 w-full" />}
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
                loading={<Skeleton className="h-96 w-full" />}
                className="shrink-0 shadow-sm"
              />
            )}
          </Document>
        )}
      </div>
    </div>
  );
}
