/** Where the document pane should open. Kept free of any pdf.js import. */

/**
 * Which PDF to show.
 *
 * A book's source is one signed URL fetched per book. A lecture's linked
 * document is served from an owner-scoped endpoint that authenticates by
 * bearer token. They resolve differently, so the viewer is told which kind it
 * has rather than being handed a URL and left to guess how to authorize it.
 */
export type PdfDocument =
  | { kind: "book"; bookId: number }
  | { kind: "video-resource"; videoId: string; resourceId: string };

export interface PdfTarget {
  document: PdfDocument;
  /** Shown in the pane header and on the restore control. */
  title: string;
  page: number;
  /** The stored passage to highlight, when the reference carries one. */
  excerpt?: string | null;
}

/**
 * Stable identity for "is this still the same document".
 *
 * Moving between citations in one document must not refetch it, and moving to
 * a different one must. Comparing whole target objects would do neither, since
 * the page and excerpt change on every citation.
 */
export function documentKey(document: PdfDocument): string {
  return document.kind === "book"
    ? `book:${document.bookId}`
    : `video:${document.videoId}:${document.resourceId}`;
}
