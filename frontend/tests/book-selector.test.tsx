import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BookSelector, describeSelection } from "@/components/book-selector";
import type { BookSummary } from "@/lib/types";

function book(id: number, title: string): BookSummary {
  return {
    book_id: id,
    title,
    author: null,
    page_count: 100,
    ready_at: null,
    chunk_count: 10,
    embedding_count: 10,
    retrieval_complete: true,
  };
}

const LIBRARY = [book(1, "ISLP"), book(2, "Designing ML Systems"), book(3, "MLE")];

describe("describeSelection", () => {
  it("names the whole library when everything is selected", () => {
    expect(describeSelection(LIBRARY, [1, 2, 3])).toBe("All 3 books");
  });

  it("names the single book when only one exists and is selected", () => {
    expect(describeSelection([LIBRARY[0]!], [1])).toBe("ISLP");
  });

  it("names the book when exactly one of many is selected", () => {
    expect(describeSelection(LIBRARY, [2])).toBe("Designing ML Systems");
  });

  it("counts a partial selection", () => {
    expect(describeSelection(LIBRARY, [1, 3])).toBe("2 of 3 books");
  });

  it("handles an empty library", () => {
    expect(describeSelection([], [])).toBe("No books");
  });
});

describe("BookSelector", () => {
  it("toggles a book out of the selection", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <BookSelector
        books={LIBRARY}
        selected={[1, 2, 3]}
        onChange={onChange}
        hasConversation={false}
      />,
    );

    await user.click(screen.getByLabelText("Choose which books to search"));
    await user.click(screen.getByLabelText("ISLP", { exact: false }));

    expect(onChange).toHaveBeenCalledExactlyOnceWith([2, 3]);
  });

  it("allows the final selected book to be deselected", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <BookSelector
        books={LIBRARY}
        selected={[2]}
        onChange={onChange}
        hasConversation={false}
      />,
    );

    await user.click(screen.getByLabelText("Choose which books to search"));
    await user.click(
      screen.getByLabelText("Designing ML Systems", { exact: false }),
    );

    expect(onChange).toHaveBeenCalledExactlyOnceWith([]);
  });

  it("changes select all to deselect all when the library is selected", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <BookSelector
        books={LIBRARY}
        selected={[1, 2, 3]}
        onChange={onChange}
        hasConversation={false}
      />,
    );

    await user.click(screen.getByLabelText("Choose which books to search"));
    await user.click(screen.getByRole("button", { name: "Deselect all" }));

    expect(onChange).toHaveBeenCalledExactlyOnceWith([]);
  });

  it("warns that changing the selection restarts the conversation", () => {
    render(
      <BookSelector
        books={LIBRARY}
        selected={[1]}
        onChange={vi.fn()}
        hasConversation
      />,
    );

    expect(screen.getByText(/starts a new conversation/i)).toBeInTheDocument();
  });

  it("stays quiet when there is no conversation to lose", () => {
    render(
      <BookSelector
        books={LIBRARY}
        selected={[1]}
        onChange={vi.fn()}
        hasConversation={false}
      />,
    );

    expect(screen.queryByText(/starts a new conversation/i)).toBeNull();
  });
});
