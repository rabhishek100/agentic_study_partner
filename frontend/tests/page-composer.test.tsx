import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { PageComposer } from "@/components/read/page-composer";

vi.mock("@/lib/dictation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/dictation")>()),
  canDictate: vi.fn(() => false),
}));

function Harness({ onSubmit }: { onSubmit: (question: string) => void }) {
  const [inContext, setInContext] = useState(true);
  return (
    <PageComposer
      page={108}
      sectionTitle="4.3 Class imbalance"
      pageInContext={inContext}
      onPageInContextChange={setInContext}
      onSubmit={onSubmit}
    />
  );
}

describe("the composer under the document", () => {
  it("says where the reader is without them having to", async () => {
    // The whole gesture of source-first study: no selection, no typing about
    // the page, and the question still lands on it.
    render(<Harness onSubmit={vi.fn()} />);

    expect(screen.getByText("p. 108")).toBeInTheDocument();
    expect(screen.getByText("4.3 Class imbalance")).toBeInTheDocument();
    expect(screen.getByText("This page is in context")).toBeInTheDocument();
  });

  it("asks the question the reader typed", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await userEvent.type(
      screen.getByLabelText("Ask about this page"),
      "Why is accuracy the wrong measure here?{Enter}",
    );

    expect(onSubmit).toHaveBeenCalledWith("Why is accuracy the wrong measure here?");
  });

  it("lets the reader take the page back out, and says so", async () => {
    // "Ignore the page, answer generally" is a real thing to want, and having
    // to leave the reader to ask it would be worse than a chip with an X.
    render(<Harness onSubmit={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", {
        name: "Stop including page 108 with this question",
      }),
    );

    expect(screen.queryByText("p. 108")).not.toBeInTheDocument();
    expect(screen.getByText("Answering without the page")).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Ask anything about this book…"),
    ).toBeInTheDocument();
  });

  it("puts the page back", async () => {
    render(<Harness onSubmit={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", {
        name: "Stop including page 108 with this question",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Include page 108" }),
    );

    expect(screen.getByText("p. 108")).toBeInTheDocument();
  });

  it("does not send an empty question", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText("Ask about this page"), "   {Enter}");

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("offers dictation where the browser supports it", async () => {
    // Speaking a question was already available in the main chat; it should
    // not stop being available because the question is about a page. The
    // control hides itself where recording is unsupported, which is why this
    // says so rather than asserting it is always there.
    const { canDictate } = await import("@/lib/dictation");
    vi.mocked(canDictate).mockReturnValue(true);

    render(<Harness onSubmit={vi.fn()} />);

    expect(
      await screen.findByRole("button", { name: /Dictate a question/ }),
    ).toBeInTheDocument();
  });
});
