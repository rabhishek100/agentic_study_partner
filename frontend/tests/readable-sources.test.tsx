import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ReadableSources } from "@/components/read/readable-sources";
import { TooltipProvider } from "@/components/ui/tooltip";
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
      <TooltipProvider>
      <ReadableSources
        books={[book(7, "Designing Machine Learning Systems", true)]}
        noun="book"
      />
      </TooltipProvider>,
    );

    expect(
      screen.getByRole("link", { name: /Designing Machine Learning Systems/ }),
    ).toHaveAttribute("href", "/read/7");
    expect(screen.getByText("Read a book")).toBeInTheDocument();
  });

  it("does not offer a book that is not ready to be read", () => {
    render(
      <TooltipProvider>
        <ReadableSources books={[book(8, "Still ingesting", false)]} noun="book" />
      </TooltipProvider>,
    );

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("names papers as papers", () => {
    render(
      <TooltipProvider>
        <ReadableSources books={[book(9, "Attention", true)]} noun="paper" />
      </TooltipProvider>,
    );

    expect(screen.getByText("Read a paper")).toBeInTheDocument();
  });

  it("shows nothing at all when the library is empty", () => {
    const { container } = render(
      <TooltipProvider>
        <ReadableSources books={[]} noun="book" />
      </TooltipProvider>,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("keeps the whole title reachable when the rail truncates it", async () => {
    // A rail this narrow cuts most real titles, and the cut part is often what
    // tells two editions apart.
    const long =
      "System Design Interview – An insider's guide, Second Edition: Step by Step Guide";
    render(
      <TooltipProvider>
        <ReadableSources books={[book(523, long, true)]} noun="book" />
      </TooltipProvider>,
    );

    await userEvent.hover(screen.getByRole("link", { name: new RegExp(long.slice(0, 20)) }));

    expect(await screen.findAllByText(long)).not.toHaveLength(0);
  });
});
