"use client";

import { AlertCircle, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
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
  spans[range.start]?.scrollIntoView({ block: "center", behavior: "smooth" });
  return true;
}

export function PdfViewer({
  target,
  onClose,
}: {
  target: PdfTarget;
  onClose: () => void;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(target.page);
  const [exactMatch, setExactMatch] = useState<boolean | null>(null);
  const [width, setWidth] = useState(640);

  const containerRef = useRef<HTMLDivElement | null>(null);

  // A signed URL is short-lived, so it is fetched per book rather than cached.
  useEffect(() => {
    let cancelled = false;
    setSource(null);
    setError("");
    (async () => {
      try {
        const payload = await apiFetch<BookSourceResponse>(
          `/books/${target.bookId}/source`,
        );
        if (!cancelled) setSource(payload.url);
      } catch (caught) {
        if (!cancelled) {
          setError(
            (caught as Error).message ||
              "This book's original file could not be opened.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [target.bookId]);

  useEffect(() => setPage(target.page), [target.page, target.excerpt]);

  // Render at the pane's width so the page fills it without horizontal scroll.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(280, entry.contentRect.width - 32));
    });
    observer.observe(element);
    return () => observer.disconnect();
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

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{target.bookTitle}</p>
          <p className="text-xs text-muted-foreground">
            Page {page}
            {pageCount ? ` of ${pageCount}` : ""}
          </p>
        </div>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => setPage((current) => Math.max(1, current - 1))}
        >
          <ChevronLeft aria-hidden />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Next page"
          disabled={pageCount > 0 && page >= pageCount}
          onClick={() => setPage((current) => current + 1)}
        >
          <ChevronRight aria-hidden />
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

      {exactMatch === false && target.excerpt && (
        <p className="shrink-0 border-b border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
          Showing the cited page. The exact passage could not be located in
          this page&apos;s text.
        </p>
      )}

      <div ref={containerRef} className="min-h-0 flex-1 overflow-auto p-4">
        {error ? (
          <Alert variant="destructive">
            <AlertCircle aria-hidden />
            <AlertTitle>Cannot open this book</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : !source ? (
          <Skeleton className="h-96 w-full" />
        ) : (
          <Document
            file={source}
            onLoadSuccess={({ numPages }) => setPageCount(numPages)}
            onLoadError={(cause) =>
              setError(cause.message || "The document could not be read.")
            }
            loading={<Skeleton className="h-96 w-full" />}
            className={cn("flex justify-center")}
          >
            <Page
              pageNumber={page}
              width={width}
              renderAnnotationLayer={false}
              onRenderTextLayerSuccess={onPageRendered}
              loading={<Skeleton className="h-96 w-full" />}
              className="shadow-sm"
            />
          </Document>
        )}
      </div>
    </div>
  );
}
