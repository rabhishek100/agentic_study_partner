import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EvidenceIndex } from "@/components/conversation/evidence-index";
import type { CitationRef, EvidenceRef } from "@/lib/types";

const evidence: EvidenceRef[] = [
  {
    node_id: 10,
    pages: [142],
    path: "Chapter 4 :: Training-serving skew",
    book_id: 1,
    book_title: "ML Systems",
    rank: 1,
    chunk_id: "chunk-1",
    chunk_index: 0,
    retrieval_method: "bm25",
    score: 0.9,
    excerpt: "Production data differs from training data.",
  },
];
const citations: CitationRef[] = [
  { marker: "[S1]", node_id: 10, page: 142, book_id: 1, evidence_rank: 1 },
];

describe("EvidenceIndex", () => {
  it("opens the cited page and marks the active source", () => {
    const onOpen = vi.fn();
    render(
      <EvidenceIndex
        evidence={evidence}
        citations={citations}
        activePage={142}
        onOpen={onOpen}
      />,
    );

    const source = screen.getByRole("button", { name: /Page 142/i });
    expect(source).toHaveAttribute("aria-current", "true");
    fireEvent.click(source);
    expect(onOpen).toHaveBeenCalledWith(evidence[0], 142);
  });
});
