import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "@/components/conversation/answer";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { CitationRef, EvidenceRef } from "@/lib/types";

function evidence(overrides: Partial<EvidenceRef> = {}): EvidenceRef {
  return {
    node_id: 10,
    pages: [4],
    path: "Chapter 1 :: Core idea",
    book_id: 7,
    book_title: "Designing ML Systems",
    rank: 1,
    chunk_id: null,
    chunk_index: null,
    retrieval_method: "hybrid",
    score: 0.812,
    excerpt: "Training-serving skew arises when…",
    ...overrides,
  };
}

function renderAnswer(
  text: string,
  refs: EvidenceRef[],
  citations: CitationRef[] = [],
  onOpenReference?: (reference: EvidenceRef) => void,
) {
  return render(
    <TooltipProvider>
      <Answer
        text={text}
        evidence={refs}
        citations={citations}
        onOpenReference={onOpenReference}
      />
    </TooltipProvider>,
  );
}

describe("Answer", () => {
  it("renders citation markers as numbered chips, not raw brackets", () => {
    const { container } = renderAnswer("Skew comes from drift [S1].", [
      evidence(),
    ]);

    expect(container.textContent).not.toContain("[S1]");
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("numbers chips by position in the reference list", () => {
    const { container } = renderAnswer(
      "First [S1] then second [S2].",
      [evidence({ rank: 1 }), evidence({ node_id: 20, rank: 2 })],
    );

    expect(container.textContent).not.toContain("[S");
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renders node-and-page markers from the summary route", () => {
    const { container } = renderAnswer(
      "The chapter defines it [N10:P4].",
      [evidence({ rank: null, pages: [4] })],
    );

    expect(container.textContent).not.toContain("[N10:P4]");
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("leaves an unresolvable marker as literal text", () => {
    const { container } = renderAnswer("Invented [S9].", [evidence()]);
    expect(container.textContent).toContain("[S9]");
  });

  it("does not turn markers inside code spans into chips", () => {
    // The answer sometimes explains the citation format itself.
    const { container } = renderAnswer("Use `[S1]` to cite.", [evidence()]);

    expect(container.querySelector("code")?.textContent).toBe("[S1]");
  });

  it("does not turn markers inside fenced code blocks into chips", () => {
    const { container } = renderAnswer(
      "Example:\n\n```\ncite [S1] here\n```\n",
      [evidence()],
    );

    expect(container.querySelector("pre")?.textContent).toContain("[S1]");
  });

  it("still renders ordinary markdown structure", () => {
    const { container } = renderAnswer(
      "## Heading\n\n- one [S1]\n- two\n",
      [evidence()],
    );

    expect(container.querySelector("h2")?.textContent).toBe("Heading");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("opens the reference when a chip is activated", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderAnswer("Skew [S1].", [evidence()], [], onOpen);

    await user.click(screen.getByText("1"));

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen.mock.calls[0]?.[0]).toMatchObject({ node_id: 10 });
  });

  it("makes chips keyboard operable", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderAnswer("Skew [S1].", [evidence()], [], onOpen);

    await user.tab();
    await user.keyboard("{Enter}");

    expect(onOpen).toHaveBeenCalledOnce();
  });
});
