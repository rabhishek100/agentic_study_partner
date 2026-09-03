import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-authenticated-image", () => ({
  useAuthenticatedImage: (url: string) => ({
    status: "ready",
    url: `blob:${url}`,
  }),
}));

import { Answer } from "@/components/conversation/answer";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { CitationRef, EvidenceRef, FigureRef } from "@/lib/types";

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

  it("shows the page when one chapter is cited at several of them", () => {
    // The reported case: a chapter summary of a book whose table of contents
    // stops at the chapter. Every marker names the same node, so every chip
    // read "1" and three different pages were indistinguishable.
    const chapter = evidence({
      node_id: 2531,
      pages: [5, 22, 32],
      path: "CHAPTER 1: SCALE FROM ZERO TO MILLIONS OF USERS",
      rank: null,
    });
    const { container } = renderAnswer(
      "Cache eviction [N2531:P22]. Sharding keys [N2531:P32]. Scaling [N2531:P5].",
      [chapter],
      [
        { marker: "[N2531:P22]", node_id: 2531, page: 22, book_id: 7, evidence_rank: null },
        { marker: "[N2531:P32]", node_id: 2531, page: 32, book_id: 7, evidence_rank: null },
        { marker: "[N2531:P5]", node_id: 2531, page: 5, book_id: 7, evidence_rank: null },
      ],
    );

    expect(container.textContent).not.toContain("[N2531");
    expect(screen.getByText("1\u00b722")).toBeInTheDocument();
    expect(screen.getByText("1\u00b732")).toBeInTheDocument();
    expect(screen.getByText("1\u00b75")).toBeInTheDocument();
  });

  it("leaves a chapter cited once with its bare number", () => {
    const { container } = renderAnswer(
      "One place only [N2531:P5].",
      [evidence({ node_id: 2531, pages: [5], rank: null })],
      [{ marker: "[N2531:P5]", node_id: 2531, page: 5, book_id: 7, evidence_rank: null }],
    );

    expect(container.textContent).not.toContain("[N2531");
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

  it("renders LaTeX written with \\( \\) delimiters as maths", () => {
    const { container } = renderAnswer(
      "typically \\(m \\approx \\sqrt{p}\\) is chosen [S1].",
      [evidence()],
    );

    expect(container.querySelector(".katex")).not.toBeNull();
    // KaTeX keeps the TeX source in a visually-hidden MathML annotation, so
    // assert against the visible HTML rendering rather than textContent.
    const rendered = container.querySelector(".katex-html")?.textContent ?? "";
    expect(rendered).not.toContain("\\approx");
    expect(rendered).not.toContain("\\sqrt");
    expect(rendered).toContain("≈");
  });

  it("renders display maths", () => {
    const { container } = renderAnswer("\\[ \\hat{y} = X\\beta \\]", [
      evidence(),
    ]);
    expect(container.querySelector(".katex-display")).not.toBeNull();
  });

  it("keeps citation chips working alongside maths", () => {
    const { container } = renderAnswer(
      "Choosing \\(m = \\sqrt{p}\\) decorrelates the trees [S1].",
      [evidence()],
    );

    expect(container.querySelector(".katex")).not.toBeNull();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(container.textContent).not.toContain("[S1]");
  });

  it("does not rewrite markers that appear inside a formula", () => {
    // Subscripts can look like a marker; the maths must win.
    const { container } = renderAnswer("\\(x_{[S1]}\\) is not a citation.", [
      evidence(),
    ]);
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  it("makes chips keyboard operable", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderAnswer("Skew [S1].", [evidence()], [], onOpen);

    await user.tab();
    await user.keyboard("{Enter}");

    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("keeps an inline figure mounted through an unrelated rerender", () => {
    const refs = [evidence()];
    const figures: FigureRef[] = [
      {
        book_id: 7,
        node_id: 10,
        block_id: 99,
        page: 4,
        mime_type: "image/png",
        path: "Chapter 1 :: Core idea",
        caption: "Training-serving skew diagram",
        evidence_rank: 1,
      },
    ];
    const view = render(
      <TooltipProvider>
        <Answer
          text="The paths diverge here [S1]."
          evidence={refs}
          citations={[]}
          figures={figures}
        />
      </TooltipProvider>,
    );
    const original = screen.getByAltText("Training-serving skew diagram");

    view.rerender(
      <TooltipProvider>
        <Answer
          text="The paths diverge here [S1]."
          evidence={refs}
          citations={[]}
          figures={figures}
        />
      </TooltipProvider>,
    );

    expect(screen.getByAltText("Training-serving skew diagram")).toBe(original);
  });
});
