/** Where the document pane should open. Kept free of any pdf.js import. */
export interface PdfTarget {
  bookId: number;
  bookTitle: string;
  page: number;
  /** The stored passage to highlight, when the reference carries one. */
  excerpt?: string | null;
}
