import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReadableSources } from "@/components/read/readable-sources";
import type { BookSummary } from "@/lib/types";

function book(id: number, title: string, ready: boolean): BookSummary {
  return {
    book_id: id,
    title,
    author: "Chip Huyen",
    page_count: 389,
    ready_at: ready ? "2026-08-20T00:00:00Z" : null,
    chunk_count: 343,
    embedding_count: 343,
    retrieval_complete: true,
  };
}

describe("ReadableSources", () => {
  it("offers each ready book to read, in one visible click", () => {
    // The first version put this behind a hover on a row inside the scope
    // popover, and it was unreachable in practice.
    render(
      <ReadableSources
        books={[book(7, "Designing Machine Learning Systems", true)]}
        noun="book"
      />,
    );

    expect(
      screen.getByRole("link", { name: /Designing Machine Learning Systems/ }),
    ).toHaveAttribute("href", "/read/7");
    expect(screen.getByText("Read a book")).toBeInTheDocument();
  });

  it("does not offer a book that is not ready to be read", () => {
    render(<ReadableSources books={[book(8, "Still ingesting", false)]} noun="book" />);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("names papers as papers", () => {
    render(<ReadableSources books={[book(9, "Attention", true)]} noun="paper" />);

    expect(screen.getByText("Read a paper")).toBeInTheDocument();
  });

  it("shows nothing at all when the library is empty", () => {
    const { container } = render(<ReadableSources books={[]} noun="book" />);

    expect(container).toBeEmptyDOMElement();
  });
});
